"""Web API for the review UI.

    uvicorn support_agent.api:app --reload

POST /api/tickets/{id}/triage runs the agent; POST /api/tickets/{id}/approve applies the
actions a person approved. Actions that break policy are refused here as well as in the UI.
An approval names the proposal it was given for, so a newer triage can't swap the actions
underneath the reviewer, and each action can be applied only once.

This is a local demo: there is no login, so `approved_by` is whatever the caller sends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent import TriageResult, load_tickets, triage
from .erp import ERP
from .proposal import check_policy
from .providers import ProviderError, make_provider

app = FastAPI(title="Support triage agent")
erp = ERP()
tickets = {t["id"]: t for t in load_tickets()}
results: dict[str, TriageResult] = {}


@app.get("/api/tickets")
def list_tickets() -> list[dict[str, Any]]:
    return [{**t, "triaged": t["id"] in results} for t in tickets.values()]


@app.post("/api/tickets/{ticket_id}/triage")
async def run_triage(ticket_id: str, provider: str | None = None) -> dict[str, Any]:
    ticket = tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(404, "ticket not found")
    try:
        result = await triage(ticket, make_provider(provider), erp)
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:  # unknown provider or a bad AGENT_TODAY
        raise HTTPException(400, str(e)) from e
    results[ticket_id] = result
    return result.to_dict()


class Approval(BaseModel):
    proposal_id: str
    action_indexes: list[int] = Field(min_length=1)
    approved_by: str = Field(min_length=1, max_length=100)

    @field_validator("approved_by")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approved_by must not be blank")
        return value.strip()


@app.post("/api/tickets/{ticket_id}/approve")
def approve(ticket_id: str, approval: Approval) -> dict[str, Any]:
    result = results.get(ticket_id)
    if result is None or result.proposal is None:
        raise HTTPException(409, "triage the ticket first")
    if approval.proposal_id != result.proposal_id:
        raise HTTPException(409, "the proposal has changed since you loaded it; review it again")
    if check_policy(result.proposal, tickets[ticket_id]["customer_id"], erp):
        raise HTTPException(422, "proposal violates policy; fix it by hand instead")
    # Validate the whole selection before writing anything.
    actions = result.proposal.actions
    indexes = approval.action_indexes
    if len(set(indexes)) != len(indexes):
        raise HTTPException(422, "an action is selected twice")
    for i in indexes:
        if not 0 <= i < len(actions):
            raise HTTPException(422, f"no action {i} in this proposal")
        if actions[i].type == "no_action":
            raise HTTPException(422, f"action {i} is no_action; there is nothing to apply")
        if i in result.applied:
            raise HTTPException(409, f"action {i} was already applied")
    applied = []
    for i in indexes:
        action = actions[i].model_dump()
        applied.append(erp.apply_action({"ticket_id": ticket_id, "proposal_id": result.proposal_id, **action},
                                        approval.approved_by))
        result.applied.add(i)
    return {"applied": applied}


@app.get("/api/audit")
def audit() -> list[dict[str, Any]]:
    return erp.audit_log


dist = Path(__file__).parent.parent / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
