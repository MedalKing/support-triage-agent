"""Triage a ticket from the command line.

    python -m support_agent.cli T-1001 [--provider claude|ollama]
    python -m support_agent.cli --list
"""

from __future__ import annotations

import argparse
import json

import anyio

from .agent import load_tickets, triage
from .erp import ERP
from .providers import ProviderError, make_provider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_id", nargs="?")
    parser.add_argument("--provider", default=None, help="claude (default) or ollama")
    parser.add_argument("--list", action="store_true", help="list the sample tickets")
    args = parser.parse_args()

    tickets = {t["id"]: t for t in load_tickets()}
    if args.list or not args.ticket_id:
        for t in tickets.values():
            print(f"{t['id']}  {t['subject']}")
        return

    try:
        result = anyio.run(lambda: triage(tickets[args.ticket_id], make_provider(args.provider), ERP(),
                                          transport="stdio"))
    except ProviderError as e:
        raise SystemExit(f"error: {e}")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
