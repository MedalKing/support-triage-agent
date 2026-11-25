"""Run a fast, cheap model first and hand unclear tickets to a stronger one.

Most tickets are routine: one runbook, clear facts. A small model handles those. When its
run shows signs that the ticket was not routine, the same ticket is triaged again from
scratch by the stronger model, and the reviewer sees why.

The signals are things code can check, not the model's say-so alone:
- no proposal at all (the model gave up, ran out of steps or the call failed),
- a proposal that breaks policy,
- the model rated its own confidence below high,
- the category is "other" (no runbook fits),
- no_action sits next to real actions (the model couldn't decide whether to act),
- the model had to be corrected first (proposal refused, or fields dropped from it).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Callable, Literal

from .agent import TraceStep, TriageResult, triage
from .erp import ERP
from .providers import ClaudeProvider, Provider, ProviderError, make_provider

FAST_MODEL = "claude-haiku-5-5"
STRONG_MODEL = "claude-opus-5-5"
# A fixed-up reply (invalid_reply) is not here on purpose: wording is cheap to correct and says
# nothing about whether the investigation was right.
CORRECTION_NOTES = {
    "invalid_proposal": "a proposal that broke the schema",
    "proposal_without_lookup": "a proposal before looking anything up",
    "dropped_field": "an amount or address on the wrong action",
}


def escalation_reasons(result: TriageResult) -> list[str]:
    """Why this run should be redone by the stronger model. Empty means the run stands."""
    if result.proposal is None:
        return [f"no proposal: {result.error or 'unknown error'}"]
    reasons = []
    if result.policy_violations:
        reasons.append("the proposal breaks policy")
    if result.proposal.confidence != "high":
        reasons.append(f"the model's confidence was {result.proposal.confidence}")
    if result.proposal.category == "other":
        reasons.append("no runbook category fits")
    types = {a.type for a in result.proposal.actions}
    if "no_action" in types and len(types) > 1:
        reasons.append("the proposal mixes no_action with real actions")
    corrected = sorted({CORRECTION_NOTES[s.name] for s in result.trace if s.kind == "note" and s.name in CORRECTION_NOTES})
    if corrected:
        reasons.append("the model had to be corrected (" + "; ".join(corrected) + ")")
    return reasons


async def triage_with_escalation(
    ticket: dict[str, Any],
    erp: ERP,
    fast: Callable[[], Provider] | None = None,
    strong: Callable[[], Provider] | None = None,
    as_of: date | None = None,
    transport: Literal["inprocess", "stdio"] = "inprocess",
) -> TriageResult:
    """Triage with the fast model; redo it with the strong model if the run looks unclear.

    Lookups are read-only, so the first run changes nothing and the second starts clean.
    """
    fast = fast or (lambda: ClaudeProvider(model=os.environ.get("CLAUDE_FAST_MODEL", FAST_MODEL)))
    strong = strong or (lambda: ClaudeProvider(model=os.environ.get("CLAUDE_STRONG_MODEL", STRONG_MODEL)))

    first_provider = fast()
    try:
        first = await triage(ticket, first_provider, erp, transport, as_of=as_of)
        reasons = escalation_reasons(first)
        first_trace, first_inputs = first.trace, first.model_inputs
    except ProviderError as e:  # e.g. a refusal or max_tokens on the small model
        reasons, first_trace, first_inputs = [f"the first model failed: {e}"], [], []
    if not reasons:
        return first

    second = await triage(ticket, strong(), erp, transport, as_of=as_of)
    second.escalation = {"from": first_provider.name, "to": second.provider, "reasons": reasons}
    # Keep the first attempt visible in the trace, so the reviewer can see what was redone.
    second.trace[:0] = [TraceStep("note", "first_attempt", {"provider": first_provider.name})] + first_trace + [
        TraceStep("note", "escalated", {"to": second.provider, "reasons": reasons})]
    second.model_inputs[:0] = first_inputs  # both models' inputs count for the privacy check
    return second


def auto_label() -> str:
    fast = os.environ.get("CLAUDE_FAST_MODEL", FAST_MODEL)
    strong = os.environ.get("CLAUDE_STRONG_MODEL", STRONG_MODEL)
    return f"auto:{fast}->{strong}"


async def run_triage(ticket: dict[str, Any], erp: ERP, provider: str | None = None,
                     as_of: date | None = None,
                     transport: Literal["inprocess", "stdio"] = "inprocess") -> TriageResult:
    """Triage with a provider by name: auto (default: fast model, escalating), claude or ollama."""
    name = (provider or os.environ.get("AGENT_PROVIDER", "auto")).lower()
    if name == "auto":
        return await triage_with_escalation(ticket, erp, as_of=as_of, transport=transport)
    return await triage(ticket, make_provider(name), erp, transport, as_of=as_of)
