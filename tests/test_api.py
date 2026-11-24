import pytest
from fastapi.testclient import TestClient

import support_agent.api as api
from support_agent.erp import ERP
from scripted import ScriptedProvider
from test_agent import proposal

CREDIT = {"type": "issue_credit", "order_id": "SO-5003", "reason": "expired price list",
          "amount_eur": 60.0, "new_address": None}


@pytest.fixture
def client(monkeypatch):
    # Fresh ERP and results per test: the app keeps both in memory.
    monkeypatch.setattr(api, "erp", ERP())
    monkeypatch.setattr(api, "results", {})
    return TestClient(api.app)


def script_for(*actions):
    return [[("get_order", {"order_id": "SO-5003"})],
            [("submit_proposal", proposal(category="pricing_error", actions=list(actions)))]]


def triage(client, monkeypatch, *actions):
    monkeypatch.setattr(api, "make_provider", lambda name=None: ScriptedProvider(script_for(*actions)))
    r = client.post("/api/tickets/T-1003/triage")
    assert r.status_code == 200
    return r.json()


def approve(client, proposal_id, indexes, approved_by="reviewer"):
    return client.post("/api/tickets/T-1003/approve",
                       json={"proposal_id": proposal_id, "action_indexes": indexes, "approved_by": approved_by})


def test_triage_then_approve(client, monkeypatch):
    body = triage(client, monkeypatch, CREDIT)
    assert body["proposal"]["actions"][0]["needs_approval"] is True
    assert body["policy_violations"] == []
    r = approve(client, body["proposal_id"], [0])
    assert r.status_code == 200 and r.json()["applied"][0]["amount_eur"] == 60.0
    assert client.get("/api/audit").json()[-1]["approved_by"] == "reviewer"


def test_policy_violation_cannot_be_approved(client, monkeypatch):
    body = triage(client, monkeypatch, {**CREDIT, "amount_eur": 470.0})
    assert body["policy_violations"]
    assert approve(client, body["proposal_id"], [0]).status_code == 422


def test_an_action_is_applied_only_once(client, monkeypatch):
    body = triage(client, monkeypatch, CREDIT)
    assert approve(client, body["proposal_id"], [0]).status_code == 200
    assert approve(client, body["proposal_id"], [0]).status_code == 409  # replay or double click
    assert len(client.get("/api/audit").json()) == 1


@pytest.mark.parametrize("indexes", [[0, 0], [-1], [5], []])
def test_bad_selections_are_refused_before_anything_is_written(client, monkeypatch, indexes):
    body = triage(client, monkeypatch, CREDIT)
    assert approve(client, body["proposal_id"], indexes).status_code == 422
    assert client.get("/api/audit").json() == []


def test_approval_is_bound_to_the_proposal_the_reviewer_saw(client, monkeypatch):
    first = triage(client, monkeypatch, CREDIT)
    triage(client, monkeypatch, {**CREDIT, "amount_eur": 100.0})  # someone re-runs the agent
    assert approve(client, first["proposal_id"], [0]).status_code == 409
    assert client.get("/api/audit").json() == []


def test_blank_approver_is_refused(client, monkeypatch):
    body = triage(client, monkeypatch, CREDIT)
    assert approve(client, body["proposal_id"], [0], approved_by="  ").status_code == 422


def test_no_action_cannot_be_approved(client, monkeypatch):
    body = triage(client, monkeypatch, {**CREDIT, "type": "no_action", "amount_eur": None})
    assert approve(client, body["proposal_id"], [0]).status_code == 422


def test_unknown_provider_is_a_client_error(client):
    assert client.post("/api/tickets/T-1003/triage?provider=gpt").status_code == 400
