"""Web API for the review UI.

    uvicorn support_agent.api:app --reload

POST /api/tickets/{id}/triage runs the agent; POST /api/tickets/{id}/approve applies the
actions a person approved. Actions that break policy are refused here as well as in the UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import TriageResult, load_tickets, triage
from .erp import ERP
from .proposal import NEEDS_APPROVAL, check_policy
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
    results[ticket_id] = result
    return result.to_dict()


class Approval(BaseModel):
    action_indexes: list[int]
    approved_by: str


@app.post("/api/tickets/{ticket_id}/approve")
def approve(ticket_id: str, approval: Approval) -> dict[str, Any]:
    result = results.get(ticket_id)
    if result is None or result.proposal is None:
        raise HTTPException(409, "triage the ticket first")
    if check_policy(result.proposal, tickets[ticket_id]["customer_id"], erp):
        raise HTTPException(422, "proposal violates policy; fix it by hand instead")
    applied = []
    for i in approval.action_indexes:
        action = result.proposal.actions[i].model_dump()
        if action["type"] in NEEDS_APPROVAL or action["type"] == "contact_carrier":
            applied.append(erp.apply_action({"ticket_id": ticket_id, **action}, approval.approved_by))
    return {"applied": applied}


@app.get("/api/audit")
def audit() -> list[dict[str, Any]]:
    return erp.audit_log


dist = Path(__file__).parent.parent / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
