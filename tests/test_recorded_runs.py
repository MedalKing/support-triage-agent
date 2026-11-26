"""The recorded runs are what most visitors see, so they must pass their own eval cases."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from support_agent.agent import load_tickets
from support_agent.api import RECORDED_DIR, load_recorded
from support_agent.erp import ERP

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_evals)

TICKETS = {t["id"]: t for t in load_tickets()}
CASES = {c["ticket"]: c for c in yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text(encoding="utf-8"))}


def loaded(ticket_id):
    saved = json.loads((RECORDED_DIR / f"{ticket_id}.json").read_text(encoding="utf-8"))
    return saved, load_recorded(TICKETS[ticket_id], saved, ERP())


@pytest.mark.parametrize("ticket_id", sorted(CASES))
def test_recorded_run_passes_its_eval_case(ticket_id):
    saved, result = loaded(ticket_id)
    result.model_inputs = [json.dumps(saved)]  # the privacy check then covers everything saved
    failed = [name for name, ok in run_evals.score(CASES[ticket_id], result).items() if not ok]
    assert failed == []


def test_the_tour_step_on_escalation_has_an_escalated_recording():
    from support_agent.api import recorded_escalations

    # The page's tour shows the first of these; without one, that step would have nothing to show.
    assert any(e["reasons"] for e in recorded_escalations().values())


def test_the_tour_step_on_policy_blocks_the_second_credit():
    from fastapi.testclient import TestClient
    from support_agent import api

    client = TestClient(api.app)
    first = client.get("/api/tickets/T-1003/recorded").json()
    credit = [i for i, a in enumerate(first["proposal"]["actions"]) if a["type"] == "issue_credit"]
    assert client.post("/api/tickets/T-1003/approve", json={
        "proposal_id": first["proposal_id"], "action_indexes": credit, "approved_by": "visitor"}).status_code == 200
    second = client.get("/api/tickets/T-1008/recorded").json()
    assert any("possible duplicate" in v for v in second["policy_violations"])


def test_coming_back_to_a_recorded_ticket_keeps_what_was_approved():
    from fastapi.testclient import TestClient
    from support_agent import api

    client = TestClient(api.app)
    first = client.get("/api/tickets/T-1003/recorded").json()
    assert client.post("/api/tickets/T-1003/approve", json={
        "proposal_id": first["proposal_id"], "action_indexes": [0], "approved_by": "visitor"}).status_code == 200
    client.get("/api/tickets/T-1008/recorded")
    again = client.get("/api/tickets/T-1003/recorded").json()
    assert again["proposal_id"] == first["proposal_id"] and again["applied"] == [0]
    assert again["policy_violations"] == []  # its own credit doesn't count against it
    r = client.post("/api/tickets/T-1003/approve", json={
        "proposal_id": again["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert r.status_code == 409  # applied once only
