"""Triage a ticket from the command line.

    python -m support_agent.cli T-1001 [--provider auto|claude|ollama]
    python -m support_agent.cli --list
"""

from __future__ import annotations

import argparse
import json

import anyio

from .agent import load_tickets
from .erp import ERP
from .escalation import run_triage
from .providers import ProviderError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_id", nargs="?")
    parser.add_argument("--provider", default=None, help="auto (default: Haiku, escalating to Opus), claude or ollama")
    parser.add_argument("--list", action="store_true", help="list the sample tickets")
    args = parser.parse_args()

    tickets = {t["id"]: t for t in load_tickets()}
    if args.list or not args.ticket_id:
        for t in tickets.values():
            print(f"{t['id']}  {t['subject']}")
        return

    if args.ticket_id not in tickets:
        raise SystemExit(f"error: no ticket {args.ticket_id}; use --list")
    try:
        result = anyio.run(lambda: run_triage(tickets[args.ticket_id], ERP(), args.provider, transport="stdio"))
    except (ProviderError, ValueError) as e:
        raise SystemExit(f"error: {e}")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
