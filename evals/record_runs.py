"""Save one run per sample ticket, so the web page can replay them instantly and for free.

    python evals/record_runs.py                 # auto: Haiku, escalating to Opus; needs ANTHROPIC_API_KEY

The runs are saved with placeholders, not personal data, in support_agent/data/recorded/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support_agent.agent import DEMO_TODAY, load_tickets  # noqa: E402
from support_agent.erp import ERP  # noqa: E402
from support_agent.escalation import run_triage  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "support_agent" / "data" / "recorded"


async def main(provider: str, only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ticket in load_tickets():
        if only and ticket["id"] not in only:
            continue
        result = await run_triage(ticket, ERP(), provider, as_of=DEMO_TODAY)
        saved = result.to_dict(restore_pii=False)
        for key in ("proposal_id", "applied", "recorded"):  # set fresh when a visitor loads it
            saved.pop(key)
        saved["as_of"] = DEMO_TODAY.isoformat()
        (OUT / f"{ticket['id']}.json").write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
        esc = f", escalated: {'; '.join(result.escalation['reasons'])}" if result.escalation else ""
        print(f"{ticket['id']}  {result.provider}{esc}{'  ERROR ' + result.error if result.error else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="auto")
    parser.add_argument("tickets", nargs="*", help="only these tickets (default: all)")
    args = parser.parse_args()
    anyio.run(main, args.provider, args.tickets)
