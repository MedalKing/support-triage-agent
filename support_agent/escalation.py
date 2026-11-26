"""Run a fast, cheap model first and hand unclear tickets to a stronger one.

Most tickets are routine: one runbook, clear facts. A small model handles those. When its
run shows signs that the ticket was not routine, the same ticket is triaged again from
scratch by the stronger model, and the reviewer sees why.

The signals are things code can check, not the model's say-so alone:
- no proposal at all (the model gave up, ran out of steps or the call failed),
- a proposal that breaks policy,
- the model rated its own confidence below high,
- the category is "other" (no runbook fits),
- no actions at all, not even no_action (the model didn't decide anything),
- no_action sits next to real actions (the model couldn't decide whether to act),
- the model had to be corrected first: it proposed before looking anything up, put fields on the
  wrong action, or broke the schema more than once. A single schema slip it fixed itself doesn't
  count: in 24 sampled Haiku runs that happened 7 times and every one of those runs passed its eval.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Callable, Literal

from .agent import TraceStep, TriageResult, triage, usage_of
from .erp import ERP
from .providers import ClaudeProvider, Provider, ProviderError, make_provider

FAST_MODEL = "claude-haiku-5-5"
STRONG_MODEL = "claude-opus-5-5"
# A fixed-up reply (invalid_reply) is not here on purpose: wording is cheap to correct and says
# nothing about whether the investigation was right.
CORRECTION_NOTES = {
    "proposal_without_lookup": "a proposal before looking anything up",
    "dropped_field": "an amount or address on the wrong action",
}
# One refused proposal that the model then fixed is a formatting slip, not a sign the ticket is hard.
# Repeated refusals are. (Three in a row means no proposal at all, which escalates anyway.)
SCHEMA_SLIPS_ALLOWED = 1


def escalation_reasons(result: TriageResult) -> list[str]:
    """Why this run should be redone by the stronger model. Empty means the run stands."""
    if result.proposal is None:
        error = (result.error or "unknown error").rstrip(".")
        return [f"it gave no usable proposal ({error[0].lower()}{error[1:]})"]
    reasons = []
    if result.policy_violations:
        reasons.append("the proposal breaks policy")
    if result.proposal.confidence != "high":
        reasons.append(f"the model's confidence was {result.proposal.confidence}")
    if result.proposal.category == "other":
        reasons.append("no runbook category fits")
    types = {a.type for a in result.proposal.actions}
    if not types:  # not even no_action: the model didn't decide anything
        reasons.append("it proposed no actions at all, not even no_action")
    if "no_action" in types and len(types) > 1:
        reasons.append("it proposed taking no action and real actions at the same time")
    notes = [s.name for s in result.trace if s.kind == "note"]
    corrected = sorted({CORRECTION_NOTES[n] for n in notes if n in CORRECTION_NOTES})
    schema_slips = notes.count("invalid_proposal")
    if schema_slips > SCHEMA_SLIPS_ALLOWED:
        corrected.append(f"{schema_slips} proposals that broke the schema")
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
    started = time.perf_counter()
    try:
        first = await triage(ticket, first_provider, erp, transport, as_of=as_of)
        reasons = escalation_reasons(first)
        first_trace, first_inputs, first_usage = first.trace, first.model_inputs, first.usage
    except ProviderError as e:  # e.g. a refusal or max_tokens on the small model
        partial = getattr(e, "partial", None)  # what the first model saw before it failed
        reasons = [f"the first model failed: {e}"]
        first_trace, first_inputs, first_usage = ((partial.trace, partial.model_inputs, partial.usage)
                                                  if partial else ([], [], usage_of(first_provider)))
    if not reasons:
        return first

    second = await triage(ticket, strong(), erp, transport, as_of=as_of)
    second.escalation = {"from": first_provider.name, "to": second.provider, "reasons": reasons}
    # Keep the first attempt visible in the trace, so the reviewer can see what was redone.
    second.trace[:0] = [TraceStep("note", "first_attempt", {"provider": first_provider.name})] + first_trace + [
        TraceStep("note", "escalated", {"to": second.provider, "reasons": reasons})]
    second.model_inputs[:0] = first_inputs  # both models' inputs count for the privacy check
    second.usage[:0] = first_usage  # and both attempts count for what the ticket cost
    second.seconds = round(time.perf_counter() - started, 2)
    return second


def final_attempt(trace: list[TraceStep]) -> list[TraceStep]:
    """The steps of the model that made the final proposal: after an escalation, the strong
    model's own steps only, so its proposal can't lean on lookups the first model made."""
    marks = [i for i, s in enumerate(trace) if s.kind == "note" and s.name == "escalated"]
    return trace[marks[-1] + 1:] if marks else trace


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
