"""Tokens, cost and time per model call, so runs can be compared on what they cost.

Prices are USD per million tokens on the Claude API (checked 2026-11-26). Cache writes use the
5-minute TTL (1.25x input); cache reads are what the model's price list says. Haiku 5.5's prices
apply to prompts up to 100K tokens, which every prompt here is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PRICES = {  # input, output, cache write (5 min), cache read
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
}


@dataclass
class CallUsage:
    model: str
    input_tokens: int  # uncached input only; cached input is in the two fields below
    output_tokens: int
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    seconds: float = 0.0

    def cost_usd(self) -> float | None:
        price = PRICES.get(self.model.split(":")[-1])
        if price is None:  # a local model, or one without a price here
            return None
        p_in, p_out, p_write, p_read = price
        return (self.input_tokens * p_in + self.output_tokens * p_out
                + self.cache_write_tokens * p_write + self.cache_read_tokens * p_read) / 1e6

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals over a run's model calls. Cost is None if any call has no known price."""
    usage = [CallUsage(**c) for c in calls]
    costs = [u.cost_usd() for u in usage]
    total_in = sum(u.input_tokens + u.cache_write_tokens + u.cache_read_tokens for u in usage)
    return {
        "calls": len(usage),
        "models": sorted({u.model for u in usage}),
        "input_tokens": total_in,
        "cached_input_tokens": sum(u.cache_read_tokens for u in usage),
        "output_tokens": sum(u.output_tokens for u in usage),
        "model_seconds": round(sum(u.seconds for u in usage), 2),
        "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
    }
