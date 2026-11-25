"""Escalation from the fast model to the strong one, and the safeguards for a public demo."""

import json

import anyio
import pytest
from fastapi.testclient import TestClient

import support_agent.api as api
from support_agent.agent import load_tickets, triage
from support_agent.erp import ERP
from support_agent.escalation import escalation_reasons, triage_with_escalation
from support_agent.providers import ProviderError
from scripted import ScriptedProvider
from test_agent import proposal

TICKETS = {t["id"]: t for t in load_tickets()}
LOOKUP = [("get_order", {"order_id": "SO-5001"})]


def scripted(name, *proposal_overrides_list):
    """A provider that looks up the order, then submits each given proposal in turn."""
    def make():
        steps = [LOOKUP] + [[("submit_proposal", proposal(**o))] for o in proposal_overrides_list]
        p = ScriptedProvider(steps)
        p.name = name
        return p
    return make


def escalate(fast, strong=None):
    strong = strong or scripted("strong", {})
    return anyio.run(lambda: triage_with_escalation(TICKETS["T-1001"], ERP(), fast, strong))


# --- escalation ---

def test_a_clear_ticket_stays_with_the_fast_model():
    result = escalate(scripted("fast", {}))
    assert result.provider == "fast" and result.escalation is None


@pytest.mark.parametrize("overrides, reason", [
    ({"confidence": "medium"}, "confidence was medium"),
    ({"category": "other"}, "no runbook category fits"),
    ({"actions": []}, "no actions at all"),  # Haiku did this on T-1008 with high confidence
    ({"actions": [{"type": "contact_carrier", "order_id": "SO-5001", "reason": "x", "amount_eur": None,
                   "new_address": None},
                  {"type": "no_action", "order_id": "SO-5005", "reason": "x", "amount_eur": None,
                   "new_address": None}]}, "no action and real actions"),
    ({"actions": [{"type": "issue_credit", "order_id": "SO-5001", "reason": "x", "amount_eur": 9999.0,
                   "new_address": None}]}, "breaks policy"),
])
def test_unclear_runs_go_to_the_strong_model(overrides, reason):
    result = escalate(scripted("fast", overrides))
    assert result.provider == "strong"
    assert result.escalation["from"] == "fast" and any(reason in r for r in result.escalation["reasons"])
    names = [s.name for s in result.trace if s.kind == "note"]
    assert names[0] == "first_attempt" and "escalated" in names  # the first try stays visible


def test_a_corrected_model_is_escalated():
    result = escalate(scripted("fast", {"evidence": []}, {}))  # first proposal refused, second accepted
    assert result.provider == "strong" and "corrected" in result.escalation["reasons"][0]


def test_no_proposal_is_escalated():
    def gives_up():
        p = ScriptedProvider([LOOKUP])  # never submits
        p.name = "fast"
        return p
    assert escalate(gives_up).escalation["reasons"][0].startswith("it gave no usable proposal")


def test_a_failing_fast_model_is_escalated():
    class Down(ScriptedProvider):
        name = "fast"

        def step(self):
            raise ProviderError("refused")
    result = escalate(lambda: Down([]))
    assert result.provider == "strong" and "first model failed" in result.escalation["reasons"][0]


def test_reasons_are_empty_for_a_clean_confident_run():
    result = anyio.run(lambda: triage(TICKETS["T-1001"], scripted("fast", {})(), ERP()))
    assert escalation_reasons(result) == []


# --- the web API in public mode ---

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "sessions", api.OrderedDict())
    monkeypatch.setattr(api, "limiter", api.Limiter())
    monkeypatch.setattr(api, "RECORDED_DIR", tmp_path)
    for var in ("DEMO_ACCESS_CODE", "DEMO_PROVIDERS", "DEMO_RUNS_PER_HOUR", "DEMO_RUNS_PER_DAY"):
        monkeypatch.delenv(var, raising=False)

    async def fake(ticket, erp, provider=None, **kwargs):
        return await triage(ticket, scripted("fast", {})(), erp)
    monkeypatch.setattr(api, "run_triage", fake)
    return TestClient(api.app)


def record(tmp_path, ticket_id="T-1001"):
    result = anyio.run(lambda: triage(TICKETS[ticket_id], scripted("claude:test", {})(), ERP()))
    saved = result.to_dict()
    (tmp_path / f"{ticket_id}.json").write_text(json.dumps(saved), encoding="utf-8")
    return saved


def test_live_runs_need_the_access_code(client, monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "letmein")
    assert client.get("/api/config").json()["access_code_required"] is True
    assert client.post("/api/tickets/T-1001/triage").status_code == 403
    assert client.post("/api/tickets/T-1001/triage", headers={"X-Access-Code": "wrong"}).status_code == 403
    assert client.post("/api/tickets/T-1001/triage", headers={"X-Access-Code": "letmein"}).status_code == 200


def test_live_runs_are_limited_per_visitor_and_per_day(client, monkeypatch):
    monkeypatch.setenv("DEMO_RUNS_PER_HOUR", "2")
    assert [client.post("/api/tickets/T-1001/triage").status_code for _ in range(3)] == [200, 200, 429]
    monkeypatch.setattr(api, "limiter", api.Limiter())
    monkeypatch.setenv("DEMO_RUNS_PER_HOUR", "10")
    monkeypatch.setenv("DEMO_RUNS_PER_DAY", "1")
    assert [client.post("/api/tickets/T-1001/triage").status_code for _ in range(2)] == [200, 429]


def test_only_offered_providers_can_be_used(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDERS", "auto")
    assert client.post("/api/tickets/T-1001/triage?provider=ollama").status_code == 400
    assert client.post("/api/tickets/T-1001/triage?provider=auto").status_code == 200


def test_recorded_runs_need_no_code_and_can_be_approved(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "letmein")
    saved = record(tmp_path)
    assert "anna.virtanen" not in json.dumps(saved)  # saved without personal data
    body = client.get("/api/tickets/T-1001/recorded").json()
    assert body["recorded"] is True
    assert body["redactions"] == saved["redactions"]  # what the live run hid, not just the ticket
    r = client.post("/api/tickets/T-1001/approve",
                    json={"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert r.status_code == 200
    assert client.get("/api/tickets/T-9999/recorded").status_code == 404


def test_visitors_do_not_see_each_others_approvals(client, tmp_path):
    record(tmp_path)
    other = TestClient(api.app)
    body = client.get("/api/tickets/T-1001/recorded").json()
    client.post("/api/tickets/T-1001/approve",
                json={"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert len(client.get("/api/audit").json()) == 1
    assert other.get("/api/audit").json() == []
    # And one visitor can't approve a proposal loaded by another.
    r = other.post("/api/tickets/T-1001/approve",
                   json={"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert r.status_code == 409


def test_reset_starts_the_visitors_demo_over(client, tmp_path):
    record(tmp_path)
    body = client.get("/api/tickets/T-1001/recorded").json()
    client.post("/api/tickets/T-1001/approve",
                json={"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert len(client.get("/api/audit").json()) == 1
    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/audit").json() == []
    r = client.post("/api/tickets/T-1001/approve",
                    json={"proposal_id": body["proposal_id"], "action_indexes": [0], "approved_by": "visitor"})
    assert r.status_code == 409  # the old proposal is gone with the session's state


def test_a_run_that_ends_after_start_over_is_not_stored(client, monkeypatch):
    async def reset_meanwhile(ticket, erp, provider=None, **kwargs):
        result = await triage(ticket, scripted("fast", {})(), erp)
        for s in api.sessions.values():  # the visitor presses Start over while the model works
            s.erp, s.results = ERP(), {}
        return result
    monkeypatch.setattr(api, "run_triage", reset_meanwhile)
    assert client.post("/api/tickets/T-1001/triage").status_code == 409
    assert client.post("/api/tickets/T-1001/approve", json={
        "proposal_id": "x", "action_indexes": [0], "approved_by": "v"}).status_code == 409  # nothing stored


def test_a_first_model_that_fails_midway_still_counts_for_the_privacy_check():
    class FailsAfterLookup(ScriptedProvider):
        def step(self):
            if not self.script:
                raise ProviderError("refused")
            return super().step()

    def fails():
        p = FailsAfterLookup([LOOKUP])
        p.name = "fast"
        return p
    result = escalate(fails)
    assert result.escalation["reasons"] == ["the first model failed: refused"]
    # What the first model saw before failing is kept: the trace and its inputs.
    assert [s.name for s in result.trace][:2] == ["first_attempt", "get_order"]
    assert any("Pohjola Bakery" in text for text in result.model_inputs[:2])


def test_a_carrier_redirect_needs_a_tick_but_a_status_request_does_not():
    actions = [{"type": "contact_carrier", "order_id": "SO-5005", "reason": "ask for news",
                "amount_eur": None, "new_address": None},
               {"type": "contact_carrier", "order_id": "SO-5005", "reason": "redirect",
                "amount_eur": None, "new_address": "Satamatie 2, Kemi"}]
    script = [[("get_order", {"order_id": "SO-5005"})], [("submit_proposal", proposal(actions=actions))]]
    shown = anyio.run(lambda: triage(TICKETS["T-1005"], ScriptedProvider(script), ERP())).to_dict()
    assert [a["needs_approval"] for a in shown["proposal"]["actions"]] == [False, True]
    assert "somewhere else" in shown["proposal"]["actions"][1]["approval_reason"]
