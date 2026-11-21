from fastapi.testclient import TestClient

import support_agent.api as api
from scripted import ScriptedProvider
from test_agent import proposal


def test_triage_then_approve(monkeypatch):
    script = [
        [("get_order", {"order_id": "SO-5003"})],
        [("submit_proposal", proposal(category="pricing_error", actions=[
            {"type": "issue_credit", "order_id": "SO-5003", "reason": "expired price list",
             "amount_eur": 60.0, "new_address": None}]))],
    ]
    monkeypatch.setattr(api, "make_provider", lambda name=None: ScriptedProvider(script))
    client = TestClient(api.app)
    r = client.post("/api/tickets/T-1003/triage")
    assert r.status_code == 200
    body = r.json()
    assert body["proposal"]["actions"][0]["needs_approval"] is True
    assert body["policy_violations"] == []
    r = client.post("/api/tickets/T-1003/approve", json={"action_indexes": [0], "approved_by": "reviewer"})
    assert r.status_code == 200 and r.json()["applied"][0]["amount_eur"] == 60.0
    assert client.get("/api/audit").json()[-1]["approved_by"] == "reviewer"


def test_policy_violation_cannot_be_approved(monkeypatch):
    script = [[("submit_proposal", proposal(actions=[
        {"type": "issue_credit", "order_id": "SO-5003", "reason": "x", "amount_eur": 470.0, "new_address": None}]))]]
    monkeypatch.setattr(api, "make_provider", lambda name=None: ScriptedProvider(script))
    client = TestClient(api.app)
    assert client.post("/api/tickets/T-1003/triage").json()["policy_violations"]
    r = client.post("/api/tickets/T-1003/approve", json={"action_indexes": [0], "approved_by": "reviewer"})
    assert r.status_code == 422
