import pytest
from fastapi.testclient import TestClient

import support_agent.api as api
from support_agent.agent import triage as agent_triage
from scripted import ScriptedProvider
from test_agent import proposal

CREDIT = {"type": "issue_credit", "order_id": "SO-5003", "reason": "expired price list",
          "amount_eur": 60.0, "new_address": None}


@pytest.fixture
def client(monkeypatch):
    # Fresh sessions and limits per test: the app keeps both in memory.
    monkeypatch.setattr(api, "sessions", api.OrderedDict())
    monkeypatch.setattr(api, "limiter", api.Limiter())
    for var in ("DEMO_ACCESS_CODE", "DEMO_PROVIDERS", "DEMO_RUNS_PER_HOUR", "DEMO_RUNS_PER_DAY"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(api.app)


def use_script(monkeypatch, script):
    async def fake(ticket, erp, provider=None, **kwargs):
        return await agent_triage(ticket, ScriptedProvider(script), erp)
    monkeypatch.setattr(api, "run_triage", fake)


def script_for(*actions):
    return [[("get_order", {"order_id": "SO-5003"})],
            [("submit_proposal", proposal(category="pricing_error", actions=list(actions)))]]


def triage(client, monkeypatch, *actions):
    use_script(monkeypatch, script_for(*actions))
    r = client.post("/api/tickets/T-1003/triage")
    assert r.status_code == 200
    return r.json()


def approve(client, proposal_id, indexes, approved_by="reviewer"):
    return client.post("/api/tickets/T-1003/approve",
                       json={"proposal_id": proposal_id, "action_indexes": indexes, "approved_by": approved_by})


def test_triage_then_approve(client, monkeypatch):
    body = triage(client, monkeypatch, CREDIT)
    assert body["proposal"]["actions"][0]["needs_approval"] is True
    assert "money back" in body["proposal"]["actions"][0]["approval_reason"]
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


def test_a_second_credit_on_an_invoice_needs_a_manager_even_when_small(client, monkeypatch):
    # Within the cap together (20 + 20 of 117.50 EUR), but it may be the same complaint twice.
    body = triage(client, monkeypatch, {**CREDIT, "amount_eur": 20.0})
    assert approve(client, body["proposal_id"], [0]).status_code == 200
    use_script(monkeypatch, [[("get_order", {"order_id": "SO-5003"})],
                             [("submit_proposal", proposal(category="pricing_error",
                                                           actions=[{**CREDIT, "amount_eur": 20.0}]))]])
    second = client.post("/api/tickets/T-1008/triage").json()
    assert any("possible duplicate" in v for v in second["policy_violations"])
    # The agent could see it coming: the lookup shows the credit already approved.
    seen = [s["detail"]["result"] for s in second["trace"] if s["name"] == "get_order"]
    assert '"approved_credits": [{"ticket_id": "T-1003", "amount_eur": 20.0}]' in seen[0]


def test_credit_cap_counts_credits_approved_on_other_tickets(client, monkeypatch):
    # T-1003 and T-1008 are about the same invoice (470 EUR, cap 117.50 EUR).
    body = triage(client, monkeypatch, {**CREDIT, "amount_eur": 100.0})
    assert approve(client, body["proposal_id"], [0]).status_code == 200
    script = [[("get_order", {"order_id": "SO-5003"})],
              [("submit_proposal", proposal(category="pricing_error", actions=[{**CREDIT, "amount_eur": 60.0}]))]]
    use_script(monkeypatch, script)
    second = client.post("/api/tickets/T-1008/triage").json()
    assert any("possible duplicate" in v and "100.00 EUR on T-1003" in v for v in second["policy_violations"])
    r = client.post("/api/tickets/T-1008/approve",
                    json={"proposal_id": second["proposal_id"], "action_indexes": [0], "approved_by": "reviewer"})
    assert r.status_code == 422


def test_own_approved_credit_is_not_counted_twice(client, monkeypatch):
    carrier = {**CREDIT, "type": "contact_carrier", "amount_eur": None}
    body = triage(client, monkeypatch, {**CREDIT, "amount_eur": 100.0}, carrier)
    assert approve(client, body["proposal_id"], [0]).status_code == 200
    assert approve(client, body["proposal_id"], [1]).status_code == 200


def test_indexes_must_be_integers(client, monkeypatch):
    body = triage(client, monkeypatch, CREDIT)
    assert approve(client, body["proposal_id"], [False]).status_code == 422
    assert approve(client, body["proposal_id"], ["0"]).status_code == 422


def test_unknown_ticket_is_404(client):
    r = client.post("/api/tickets/T-9999/approve", json={"proposal_id": "x", "action_indexes": [0], "approved_by": "r"})
    assert r.status_code == 404


def test_two_approvals_at_once_apply_the_action_once(client, monkeypatch):
    import anyio
    import httpx

    body = triage(client, monkeypatch, CREDIT)
    payload = {"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "reviewer"}
    codes = []

    async def go():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     cookies={api.COOKIE: client.cookies.get(api.COOKIE)}) as c:
            async def one():
                codes.append((await c.post("/api/tickets/T-1003/approve", json=payload)).status_code)
            async with anyio.create_task_group() as tg:
                tg.start_soon(one)
                tg.start_soon(one)

    anyio.run(go)
    assert sorted(codes) == [200, 409]
    assert len(client.get("/api/audit").json()) == 1

