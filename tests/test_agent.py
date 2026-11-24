from datetime import date

import anyio
import pytest

from support_agent.agent import load_tickets, today, triage
from support_agent.erp import ERP
from support_agent.proposal import check_policy, Proposal

from scripted import ScriptedProvider

TICKETS = {t["id"]: t for t in load_tickets()}


def proposal(**overrides):
    base = {
        "category": "carrier_delay",
        "likely_cause": "Held at the NordFreight hub in Tampere since 9 Nov.",
        "evidence": ["SH-9001 exception 2026-11-09 at NordFreight hub Tampere"],
        "actions": [{"type": "contact_carrier", "order_id": "SO-5001", "reason": "Ask for new ETA",
                     "amount_eur": None, "new_address": None}],
        "customer_reply": "Hi, we are on it. We will update you at [EMAIL_1].",
        "confidence": "high",
    }
    return {**base, **overrides}


def run(ticket_id, script):
    provider = ScriptedProvider(script)
    result = anyio.run(lambda: triage(TICKETS[ticket_id], provider, ERP()))
    return result, provider


def test_happy_path_uses_mcp_tools_and_returns_proposal():
    result, provider = run("T-1001", [
        [("get_order", {"order_id": "SO-5001"})],
        [("get_shipment_events", {"shipment_id": "SH-9001"}), ("search_runbooks", {"query": "late delivery"})],
        [("submit_proposal", proposal())],
    ])
    assert result.error is None
    assert result.proposal.category == "carrier_delay"
    assert result.policy_violations == []
    tools_used = [s.name for s in result.trace if s.kind == "tool"]
    assert tools_used == ["get_order", "get_shipment_events", "search_runbooks", "submit_proposal"]
    assert "linehaul capacity shortage" in provider.seen[2]


def test_model_never_sees_personal_data():
    result, provider = run("T-1008", [
        [("get_order", {"order_id": "SO-5003"})],
        [("submit_proposal", proposal(category="pricing_error", actions=[]))],
    ])
    everything = "\n".join(provider.seen)
    for secret in ["FI21 1234 5600 0007 85", "131052-308T", "mikko@laplandgear.example", "222 3333"]:
        assert secret not in everything
    # ...but the reviewer gets the reply back with real values.
    assert "mikko@laplandgear.example" in result.to_dict()["proposal"]["customer_reply"]
    assert "[EMAIL_1]" in result.to_dict(restore_pii=False)["proposal"]["customer_reply"]


def test_contact_details_in_erp_results_are_redacted_too():
    result, provider = run("T-1001", [
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", proposal())],
    ])
    assert "anna.virtanen@pohjolabakery.example" not in provider.seen[1]
    assert "[EMAIL_1]" in provider.seen[1]  # same placeholder as in the ticket


def test_invalid_proposal_is_returned_to_model_for_a_retry():
    result, provider = run("T-1001", [
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", {"category": "nonsense"})],
        [("submit_proposal", proposal())],
    ])
    assert provider.tool_results[1].is_error
    assert result.proposal is not None


def test_stopping_without_proposal_is_an_error_not_a_hang():
    result, _ = run("T-1001", [[], []])
    assert result.proposal is None and "without submitting" in result.error


def test_policy_blocks_injected_full_credit():
    erp = ERP()
    p = Proposal.model_validate(proposal(actions=[
        {"type": "issue_credit", "order_id": "SO-5001", "reason": "customer asked",
         "amount_eur": 210.0, "new_address": None}]))
    problems = check_policy(p, "C-201", erp)
    assert any("no invoice" in x for x in problems)
    p2 = Proposal.model_validate(proposal(actions=[
        {"type": "issue_credit", "order_id": "SO-5003", "reason": "x", "amount_eur": 470.0, "new_address": None}]))
    assert any("needs a manager" in x for x in check_policy(p2, "C-203", erp))


def test_policy_blocks_actions_on_another_customers_order():
    p = Proposal.model_validate(proposal(actions=[
        {"type": "reship_order", "order_id": "SO-5004", "reason": "x", "amount_eur": None, "new_address": None}]))
    assert any("another customer" in x for x in check_policy(p, "C-201", ERP()))


def test_correct_pricing_credit_passes_policy():
    p = Proposal.model_validate(proposal(category="pricing_error", actions=[
        {"type": "issue_credit", "order_id": "SO-5003", "reason": "expired price list",
         "amount_eur": 60.0, "new_address": None}]))
    assert check_policy(p, "C-203", ERP()) == []


def test_model_is_told_the_demo_date_in_the_system_prompt(monkeypatch):
    monkeypatch.delenv("AGENT_TODAY", raising=False)
    _, provider = run("T-1001", [[("submit_proposal", proposal())]])
    assert provider.system.rstrip().endswith("Today is Monday 2026-11-23.")
    assert "Today is" not in provider.seen[0]  # the ticket message carries only the ticket


def test_date_comes_from_env_or_argument(monkeypatch):
    monkeypatch.setenv("AGENT_TODAY", " 2026-12-01 ")
    _, provider = run("T-1001", [[("submit_proposal", proposal())]])
    assert provider.system.rstrip().endswith("Today is Tuesday 2026-12-01.")

    provider = ScriptedProvider([[("submit_proposal", proposal())]])
    anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP(), as_of=date(2026, 11, 30)))
    assert provider.system.rstrip().endswith("Today is Monday 2026-11-30.")


def test_bad_date_setting_gives_a_clear_error(monkeypatch):
    monkeypatch.setenv("AGENT_TODAY", "23.11.2026")
    with pytest.raises(ValueError, match="AGENT_TODAY must be a date like 2026-11-23"):
        today()
