"""Web API for the review UI.

    uvicorn support_agent.api:app --reload

GET  /api/tickets/{id}/recorded  replays a saved run instantly, at no cost.
POST /api/tickets/{id}/triage    runs the agent live.
POST /api/tickets/{id}/approve   applies the actions a person approved.

Actions that break policy are refused here as well as in the UI. An approval names the
proposal it was given for, so a newer triage can't swap the actions underneath the
reviewer, and each action can be decided only once: applied, or declined with a reason.

Each visitor gets their own session (a cookie): their own copy of the sample ERP, their
own proposals and their own audit log, so visitors never see or undo each other's approvals.

Public use (set by environment variables):
- DEMO_ACCESS_CODE: live runs need this code; recorded runs stay open to everyone.
- DEMO_PROVIDERS: which providers the page offers (default "auto,claude,ollama").
- DEMO_RUNS_PER_HOUR: live runs per visitor per hour (default 10).
- DEMO_RUNS_PER_DAY: live runs for everyone together per UTC day (default 200), a cost cap.
There is no login, so `approved_by` is whatever the caller sends.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, field_validator

from .agent import TraceStep, TriageResult, load_tickets, ticket_message
from .erp import ERP
from .escalation import run_triage
from .proposal import Proposal, check_policy
from .providers import ProviderError
from .redact import Redactor

app = FastAPI(title="Support triage agent")
tickets = {t["id"]: t for t in load_tickets()}
RECORDED_DIR = Path(__file__).parent / "data" / "recorded"
COOKIE = "triage_session"
MAX_SESSIONS = 1000  # oldest sessions are dropped first, so memory stays bounded


@dataclass
class Session:
    erp: ERP = field(default_factory=ERP)
    results: dict[str, TriageResult] = field(default_factory=dict)
    # Emails the reviewer is done with. The inbox is every other email; the goal is an empty one.
    archived: set[str] = field(default_factory=set)


sessions: OrderedDict[str, Session] = OrderedDict()


def session(request: Request, response: Response) -> Session:
    sid = request.cookies.get(COOKIE)
    if sid not in sessions:
        sid = secrets.token_urlsafe(18)
        sessions[sid] = Session()
        if len(sessions) > MAX_SESSIONS:
            sessions.popitem(last=False)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", secure=secure)
    sessions.move_to_end(sid)
    return sessions[sid]


# --- limits on live runs -------------------------------------------------------------

def providers_offered() -> list[str]:
    return [p.strip() for p in os.environ.get("DEMO_PROVIDERS", "auto,claude,ollama").split(",") if p.strip()]


class Limiter:
    """Live runs per visitor per hour, and for everyone together per day."""

    def __init__(self) -> None:
        self.recent: dict[str, deque[float]] = {}
        self.day, self.today = "", 0

    def check(self, visitor: str) -> None:
        per_hour = int(os.environ.get("DEMO_RUNS_PER_HOUR", "10"))
        per_day = int(os.environ.get("DEMO_RUNS_PER_DAY", "200"))
        now, day = time.time(), datetime.now(timezone.utc).date().isoformat()
        if day != self.day:
            self.day, self.today = day, 0
        if self.today >= per_day:
            raise HTTPException(429, "The demo's live runs for today are used up. Recorded runs still work.")
        recent = self.recent.setdefault(visitor, deque())
        while recent and recent[0] < now - 3600:
            recent.popleft()
        if len(recent) >= per_hour:
            raise HTTPException(429, f"At most {per_hour} live runs per hour. Recorded runs still work.")
        recent.append(now)
        self.today += 1


limiter = Limiter()


def visitor_id(request: Request) -> str:
    # Behind a hosting proxy the visitor's address is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "") if os.environ.get("DEMO_TRUST_PROXY") else ""
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def check_access_code(code: str | None) -> None:
    expected = os.environ.get("DEMO_ACCESS_CODE")
    if expected and not hmac.compare_digest((code or "").strip().encode(), expected.encode()):
        raise HTTPException(403, "Live runs need the access code. Recorded runs work without one.")


# --- endpoints -----------------------------------------------------------------------

def recorded_escalations() -> dict[str, dict[str, Any]]:
    """Which recorded runs were redone by the stronger model, why, and whether the second
    model proposed different actions. The page's tour uses it, so it always points at a run
    that really escalated, whichever that was when recorded."""
    out = {}
    for path in sorted(RECORDED_DIR.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not saved.get("escalation"):
            continue
        names = [s["name"] for s in saved["trace"]]
        before = saved["trace"][:names.index("escalated")] if "escalated" in names else []
        first = next((s["detail"] for s in before if s["name"] == "submit_proposal"), None)
        final = saved["proposal"]
        # no_action on a different order id is still no action, so it doesn't count as a change.
        actions = lambda p: sorted(json.dumps([a["type"], None if a["type"] == "no_action" else a["order_id"],  # noqa: E731
                                               a["amount_eur"], a["new_address"]]) for a in p["actions"]) if p else None
        out[path.stem] = {"reasons": saved["escalation"]["reasons"],
                          "changed": actions(first) != actions(final)}
    return out


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "providers": providers_offered(),
        "access_code_required": bool(os.environ.get("DEMO_ACCESS_CODE")),
        "recorded": sorted(p.stem for p in RECORDED_DIR.glob("*.json")),
        "escalated": recorded_escalations(),
    }


@app.get("/api/tickets")
def list_tickets(s: Session = Depends(session)) -> list[dict[str, Any]]:
    return [{**t, "archived": t["id"] in s.archived} for t in tickets.values()]


def undecided(result: TriageResult | None) -> list[int]:
    """Actions on this proposal that still wait for an approve or a decline."""
    if result is None or result.proposal is None:
        return []
    return [i for i, a in enumerate(result.proposal.actions)
            if a.type != "no_action" and i not in result.applied and i not in result.declined]


def archive_if_decided(ticket_id: str, s: Session) -> bool:
    """Once every action on an email is decided, the email leaves the inbox by itself."""
    if not undecided(s.results.get(ticket_id)):
        s.archived.add(ticket_id)
    return ticket_id in s.archived


@app.post("/api/tickets/{ticket_id}/triage")
async def live_triage(ticket_id: str, request: Request, provider: str | None = None,
                      x_access_code: str | None = Header(default=None),
                      s: Session = Depends(session)) -> dict[str, Any]:
    ticket = tickets.get(ticket_id)
    if ticket is None:
        raise HTTPException(404, "ticket not found")
    name = (provider or providers_offered()[0]).lower()
    if name not in providers_offered():
        raise HTTPException(400, f"provider {name!r} is not offered here; use one of {providers_offered()}")
    check_access_code(x_access_code)
    limiter.check(visitor_id(request))
    erp = s.erp
    try:
        result = await run_triage(ticket, erp, name)
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:  # unknown provider or a bad AGENT_TODAY
        raise HTTPException(400, str(e)) from e
    if s.erp is not erp:  # the visitor pressed Start over while the run was going
        raise HTTPException(409, "The demo was reset while this ran. Run it again.")
    s.results[ticket_id] = result
    s.archived.discard(ticket_id)  # a new proposal is a new decision to make
    return result.to_dict()


@app.get("/api/tickets/{ticket_id}/recorded")
def recorded_triage(ticket_id: str, s: Session = Depends(session)) -> dict[str, Any]:
    """A saved run, loaded into this visitor's session so it can be reviewed and approved."""
    ticket = tickets.get(ticket_id)
    path = RECORDED_DIR / f"{ticket_id}.json"
    if ticket is None or not path.exists():
        raise HTTPException(404, "no recorded run for this ticket")
    result = s.results.get(ticket_id)
    if result is not None and result.recorded and result.proposal is not None:
        # Coming back to a ticket keeps what was approved on it; only the policy check is
        # redone, since approvals on other tickets may have changed the answer.
        result.policy_violations = check_policy(result.proposal, ticket["customer_id"], s.erp, result.proposal_id)
        return result.to_dict()
    result = load_recorded(ticket, json.loads(path.read_text(encoding="utf-8")), s.erp)
    s.results[ticket_id] = result
    return result.to_dict()


def load_recorded(ticket: dict[str, Any], saved: dict[str, Any], erp: ERP) -> TriageResult:
    proposal = None
    if saved["proposal"] is not None:
        raw = dict(saved["proposal"])
        raw["actions"] = [{k: v for k, v in a.items() if k not in ("needs_approval", "approval_reason")}
                          for a in raw["actions"]]
        proposal = Proposal.model_validate(raw)
    redactor = Redactor()
    redactor.redact(ticket_message(ticket))
    result = TriageResult(
        ticket["id"], saved["provider"], proposal, [],
        [TraceStep(t["kind"], t["name"], t["detail"]) for t in saved["trace"]], redactor,
        escalation=saved.get("escalation"), recorded=True, saved_redactions=saved.get("redactions"),
    )
    if proposal is not None:  # policy is checked again against this visitor's ERP
        result.policy_violations = check_policy(proposal, ticket["customer_id"], erp, result.proposal_id)
    result.error = saved.get("error")
    return result


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


class Approval(BaseModel):
    proposal_id: str
    action_indexes: list[StrictInt] = Field(min_length=1)
    approved_by: str = Field(min_length=1, max_length=100)
    _check = field_validator("approved_by")(_not_blank)


class Decline(BaseModel):
    proposal_id: str
    action_indexes: list[StrictInt] = Field(min_length=1)
    declined_by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)  # required: a "no" without a reason teaches nobody anything
    _check = field_validator("declined_by", "reason")(_not_blank)


def selected_actions(ticket_id: str, proposal_id: str, indexes: list[int], s: Session) -> TriageResult:
    """Check a reviewer's selection before anything is written; return the result it belongs to."""
    if ticket_id not in tickets:
        raise HTTPException(404, "ticket not found")
    result = s.results.get(ticket_id)
    if result is None or result.proposal is None:
        raise HTTPException(409, "triage the ticket first")
    if proposal_id != result.proposal_id:
        raise HTTPException(409, "the proposal has changed since you loaded it; review it again")
    actions = result.proposal.actions
    if len(set(indexes)) != len(indexes):
        raise HTTPException(422, "an action is selected twice")
    for i in indexes:
        if not 0 <= i < len(actions):
            raise HTTPException(422, f"no action {i} in this proposal")
        if actions[i].type == "no_action":
            raise HTTPException(422, f"action {i} is no_action; there is nothing to decide")
        if i in result.applied:
            raise HTTPException(409, f"action {i} was already applied")
        if i in result.declined:
            raise HTTPException(409, f"action {i} was already declined")
    return result


# `async def` with no await inside: the check and the write run in one step on the event loop,
# so two approvals of the same action can't both pass the check.
@app.post("/api/tickets/{ticket_id}/approve")
async def approve(ticket_id: str, approval: Approval, s: Session = Depends(session)) -> dict[str, Any]:
    result = selected_actions(ticket_id, approval.proposal_id, approval.action_indexes, s)
    if check_policy(result.proposal, tickets[ticket_id]["customer_id"], s.erp, result.proposal_id):
        raise HTTPException(422, "proposal violates policy; fix it by hand instead")
    actions = result.proposal.actions
    applied = []
    for i in approval.action_indexes:
        action = actions[i].model_dump()
        applied.append(s.erp.apply_action({"ticket_id": ticket_id, "proposal_id": result.proposal_id, **action},
                                          approval.approved_by))
        result.applied.add(i)
    return {"applied": applied, "archived": archive_if_decided(ticket_id, s)}


# Declining works on blocked proposals too: saying no to an out-of-policy action is a decision.
@app.post("/api/tickets/{ticket_id}/decline")
async def decline(ticket_id: str, choice: Decline, s: Session = Depends(session)) -> dict[str, Any]:
    result = selected_actions(ticket_id, choice.proposal_id, choice.action_indexes, s)
    declined = []
    for i in choice.action_indexes:
        action = result.proposal.actions[i].model_dump()
        declined.append(s.erp.record_decline({"ticket_id": ticket_id, "proposal_id": result.proposal_id, **action},
                                             choice.declined_by, choice.reason))
        result.declined[i] = choice.reason
    return {"declined": declined, "archived": archive_if_decided(ticket_id, s)}


@app.post("/api/tickets/{ticket_id}/archive")
def archive(ticket_id: str, s: Session = Depends(session)) -> dict[str, bool]:
    """Mark an email done by hand, for proposals with nothing to approve. Open actions have
    to be approved or declined first, so nothing is left undecided by archiving."""
    if ticket_id not in tickets:
        raise HTTPException(404, "ticket not found")
    if ticket_id not in s.results:
        raise HTTPException(409, "triage the ticket first")
    if undecided(s.results[ticket_id]):
        raise HTTPException(409, "approve or decline every action first")
    s.archived.add(ticket_id)
    return {"archived": True}


@app.post("/api/tickets/{ticket_id}/unarchive")
def unarchive(ticket_id: str, s: Session = Depends(session)) -> dict[str, bool]:
    if ticket_id not in tickets:
        raise HTTPException(404, "ticket not found")
    s.archived.discard(ticket_id)
    return {"archived": False}


@app.get("/api/audit")
def audit(s: Session = Depends(session)) -> list[dict[str, Any]]:
    return s.erp.audit_log


@app.post("/api/reset")
def reset(s: Session = Depends(session)) -> dict[str, str]:
    """Start this visitor's demo over: a fresh copy of the sample ERP, no proposals, empty log,
    every email back in the inbox."""
    s.erp, s.results, s.archived = ERP(), {}, set()
    return {"status": "reset"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


dist = Path(__file__).parent.parent / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
