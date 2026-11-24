"""Run the agent on every sample ticket and score it against evals/cases.yaml.

    python evals/run_evals.py --provider claude      # needs ANTHROPIC_API_KEY; costs a few cents
    python evals/run_evals.py --provider ollama      # needs a running Ollama

Besides the per-case checks, every case also checks that no personal data from the ticket
or the ERP reached the model. Results are written to evals/results/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support_agent.agent import DEMO_TODAY, load_tickets, triage  # noqa: E402
from support_agent.erp import ERP  # noqa: E402
from support_agent.providers import ProviderError, make_provider  # noqa: E402

HERE = Path(__file__).parent
SECRETS = [  # every personal value in the sample data
    "anna.virtanen@pohjolabakery.example", "+358 40 123 4567", "logistics@saimaa-electronics.example",
    "+358 50 765 4321", "mikko@laplandgear.example", "044 222 3333", "+358 44 222 3333",
    "orders@balticfoods.example", "+46 70 111 2222", "FI21 1234 5600 0007 85", "131052-308T",
]


def score(case: dict, result) -> dict[str, bool]:
    p = result.proposal
    # Only lookups that returned data count; a call that hit "not found" isn't evidence.
    tools = {s.name for s in result.trace if s.kind == "tool" and s.detail.get("ok", True)}
    actions = {a.type for a in p.actions} if p else set()
    # With `order` set, a required action only counts when it targets that order.
    targeted = {a.type for a in p.actions if case.get("order") in (None, a.order_id)} if p else set()
    seen = "\n".join(result.model_inputs)
    checks = {
        "proposal": p is not None,
        "category": bool(p) and p.category == case["category"],
        "tools": set(case.get("must_call", [])) <= tools,
        "required_actions": set(case.get("must_propose", [])) <= targeted,
        "forbidden_actions": not (set(case.get("must_not_propose", [])) & actions),
        "no_pii_to_model": not any(s in seen for s in SECRETS),
    }
    if case.get("policy_clean"):
        checks["policy"] = bool(p) and not result.policy_violations
    if "order" in case and "get_order" in case.get("must_call", []):
        # The lookup has to be of the right order, not just any order.
        looked_up = {s.detail["arguments"].get("order_id") for s in result.trace
                     if s.kind == "tool" and s.name == "get_order" and s.detail.get("ok", True)}
        checks["looked_up_order"] = case["order"] in looked_up
    if "allowed_actions" in case:  # nothing beyond what the runbook allows for this case
        checks["only_allowed_actions"] = bool(p) and actions <= set(case["allowed_actions"])
    if "credit_eur" in case:  # every credit on the case's order, and the total exact to the cent
        credits = [a for a in p.actions if a.type == "issue_credit"] if p else []
        total = round(sum(a.amount_eur or 0 for a in credits) * 100)
        checks["credit_amount"] = (bool(credits) and all(a.order_id == case.get("order") for a in credits)
                                   and total == round(case["credit_eur"] * 100))
    return checks


async def main(provider_name: str, out_path: str | None) -> int:
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    tickets = {t["id"]: t for t in load_tickets()}
    rows, provider_label = [], provider_name
    for case in cases:
        provider = make_provider(provider_name)
        provider_label = provider.name
        try:
            # Pinned to the demo date: the expected answers depend on it, so AGENT_TODAY must not move it.
            result = await triage(tickets[case["ticket"]], provider, ERP(), as_of=DEMO_TODAY)
            checks, error = score(case, result), result.error
            proposed = result.proposal and {
                "category": result.proposal.category,
                "actions": [{"type": a.type, "order_id": a.order_id, "amount_eur": a.amount_eur}
                            for a in result.proposal.actions],
            }
        except ProviderError as e:
            checks, error, proposed = {"proposal": False}, str(e), None
        passed = all(checks.values())
        rows.append({"ticket": case["ticket"], "passed": passed, "checks": checks, "proposed": proposed,
                     "error": error})
        failed = [k for k, v in checks.items() if not v]
        print(f"{case['ticket']}  {'PASS' if passed else 'FAIL ' + ', '.join(failed)}")

    n_pass = sum(r["passed"] for r in rows)
    print(f"\n{n_pass}/{len(rows)} cases passed ({provider_label})")
    out = Path(out_path) if out_path else         HERE / "results" / f"{datetime.now():%Y-%m-%d-%H%M}-{provider_label.replace(':', '_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"provider": provider_label, "as_of": DEMO_TODAY.isoformat(), "passed": n_pass,
                               "total": len(rows), "cases": rows}, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0 if n_pass == len(rows) else 1  # a failed case fails the run, also in CI


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="claude")
    parser.add_argument("--out", help="write the report here instead of evals/results/")
    args = parser.parse_args()
    sys.exit(anyio.run(main, args.provider, args.out))
