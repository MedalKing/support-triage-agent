"""Run the agent on every sample ticket and score it against evals/cases.yaml.

    python evals/run_evals.py                        # auto: Haiku, escalating to Opus; needs ANTHROPIC_API_KEY
    python evals/run_evals.py --provider claude      # one model (CLAUDE_MODEL, default Opus); a few cents
    python evals/run_evals.py --provider ollama      # needs a running Ollama

Besides the per-case checks, every case also checks that no personal data from the ticket
or the ERP reached the model. Results are written to evals/results/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support_agent.agent import DEMO_TODAY, load_tickets  # noqa: E402
from support_agent.escalation import auto_label, final_attempt, run_triage  # noqa: E402
from support_agent.proposal import reply_problems  # noqa: E402
from support_agent.usage import summarize  # noqa: E402
from support_agent.eval_results import RESULTS_DIR, write_index  # noqa: E402
from support_agent.erp import ERP  # noqa: E402
from support_agent.providers import ProviderError, make_provider  # noqa: E402

HERE = Path(__file__).parent
SECRETS = [  # every personal value in the sample data
    "anna.virtanen@pohjolabakery.example", "+358 40 123 4567", "logistics@saimaa-electronics.example",
    "+358 50 765 4321", "mikko@laplandgear.example", "044 222 3333", "+358 44 222 3333",
    "orders@balticfoods.example", "+46 70 111 2222", "FI21 1234 5600 0007 85", "131052-308T",
]


# A statement of how late the order is. Numbers after a comparison word are the runbook's
# thresholds being quoted ("no credit below 10 working days late"), not the delay itself.
# It covers the form "N working days late/past/overdue/behind/after", in any case.
DELAY = re.compile(r"(?<!below )(?<!under )(?<!over )(?<!above )(?<!than )(?<!< )(?<!> )"
                   r"\b(\d+) working days? (?:late|past|overdue|behind|after)", re.IGNORECASE)


def proposal_text(p) -> str:
    """Everything the agent wrote: cause, evidence, action reasons and the reply."""
    return " ".join([p.likely_cause, *p.evidence, *(a.reason for a in p.actions), p.customer_reply])


def score(case: dict, result) -> dict[str, bool]:
    p = result.proposal
    # Only the steps of the model that made the final proposal count: after an escalation the
    # strong model must have done its own lookups. Only lookups that returned data count.
    steps = final_attempt(result.trace)
    tools = {s.name for s in steps if s.kind == "tool" and s.detail.get("ok", True)}
    actions = {a.type for a in p.actions} if p else set()
    # With `order` set, a required action only counts when it targets that order.
    targeted = {a.type for a in p.actions if case.get("order") in (None, a.order_id)} if p else set()
    seen = "\n".join(result.model_inputs)  # both attempts: neither model may see personal data
    checks = {
        "proposal": p is not None,
        "category": bool(p) and p.category == case["category"],
        "tools": set(case.get("must_call", [])) <= tools,
        "required_actions": set(case.get("must_propose", [])) <= targeted,
        "forbidden_actions": not (set(case.get("must_not_propose", [])) & actions),
        "no_pii_to_model": not any(s in seen for s in SECRETS),
        "reply_ready": bool(p) and not reply_problems(p.customer_reply),
    }
    if "working_days_late" in case:  # every "N working days late" the agent states is the true number
        stated = {int(n) for n in DELAY.findall(proposal_text(p))} if p else set()
        checks["working_days"] = bool(p) and stated <= {case["working_days_late"]}
    if case.get("policy_clean"):
        checks["policy"] = bool(p) and not result.policy_violations
    if "order" in case and "get_order" in case.get("must_call", []):
        # The lookup has to be of the right order, not just any order.
        looked_up = {s.detail["arguments"].get("order_id") for s in steps
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


def run_totals(rows: list[dict]) -> dict:
    """What the whole run cost, how long a ticket took, and how much input came from the cache."""
    used = [r for r in rows if r["usage"]]
    costs = [r["usage"]["cost_usd"] for r in used]
    seconds = [r["seconds"] for r in used if r["seconds"] is not None]
    tokens_in = sum(r["usage"]["input_tokens"] for r in used)
    return {
        "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
        "median_seconds": round(statistics.median(seconds), 1) if seconds else None,
        "max_seconds": max(seconds) if seconds else None,
        "input_tokens": tokens_in,
        "output_tokens": sum(r["usage"]["output_tokens"] for r in used),
        "cached_share": (sum(r["usage"]["cached_input_tokens"] for r in used) / tokens_in) if tokens_in else 0.0,
    }


async def main(provider_name: str, out_path: str | None) -> int:
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    tickets = {t["id"]: t for t in load_tickets()}
    rows = []
    provider_label = auto_label() if provider_name == "auto" else make_provider(provider_name).name
    for case in cases:
        escalation, usage, seconds = None, None, None
        try:
            # Pinned to the demo date: the expected answers depend on it, so AGENT_TODAY must not move it.
            result = await run_triage(tickets[case["ticket"]], ERP(), provider_name, as_of=DEMO_TODAY)
            escalation = result.escalation
            usage, seconds = summarize(result.usage), result.seconds
            checks, error = score(case, result), result.error
            proposed = result.proposal and {
                "category": result.proposal.category,
                # What the agent said about the delay, so a working_days failure can be read later.
                "stated_delay": DELAY.findall(proposal_text(result.proposal)),
                "actions": [{"type": a.type, "order_id": a.order_id, "amount_eur": a.amount_eur}
                            for a in result.proposal.actions],
            }
        except ProviderError as e:
            checks, error, proposed = {"proposal": False}, str(e), None
        passed = all(checks.values())
        rows.append({"ticket": case["ticket"], "passed": passed, "checks": checks, "proposed": proposed,
                     "escalation": escalation, "error": error, "usage": usage, "seconds": seconds})
        failed = [k for k, v in checks.items() if not v]
        note = f"  (escalated: {'; '.join(escalation['reasons'])})" if escalation else ""
        cost = f"  ${usage['cost_usd']:.4f}, {seconds:.0f} s" if usage and usage["cost_usd"] is not None else ""
        print(f"{case['ticket']}  {'PASS' if passed else 'FAIL ' + ', '.join(failed)}{cost}{note}")

    n_pass = sum(r["passed"] for r in rows)
    n_esc = sum(bool(r["escalation"]) for r in rows)
    totals = run_totals(rows)
    print(f"\n{n_pass}/{len(rows)} cases passed ({provider_label}), {n_esc} escalated; "
          f"${totals['cost_usd']} in total, median {totals['median_seconds']} s per ticket, "
          f"{totals['cached_share']:.0%} of input read from cache")
    out = Path(out_path) if out_path else HERE / "results" / f"{datetime.now():%Y-%m-%d-%H%M}-{provider_label.replace(':', '_').replace('->', '_to_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"provider": provider_label, "as_of": DEMO_TODAY.isoformat(), "passed": n_pass,
                               "escalated": n_esc, "totals": totals, "prompt_cache": os.environ.get("CLAUDE_PROMPT_CACHE", "1") != "0",
                               "total": len(rows), "cases": rows}, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if out.resolve().is_relative_to(RESULTS_DIR.resolve()):
        write_index()  # keep evals/results/README.md in step with the folder
    return 0 if n_pass == len(rows) else 1  # a failed case fails the run, also in CI


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="auto", help="auto (default), claude or ollama")
    parser.add_argument("--out", help="write the report here instead of evals/results/")
    args = parser.parse_args()
    sys.exit(anyio.run(main, args.provider, args.out))
