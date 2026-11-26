"""One test per hole the November 2026 reviews found, so they stay closed."""

import json
import math
from datetime import date

import anyio
import pytest
from mcp import Client

from support_agent.agent import load_tickets, triage
from support_agent.erp import ERP
from support_agent.erp_server import build_server, working_days_between
from support_agent.proposal import Proposal, check_policy, drop_stray_fields, reply_problems
from support_agent.redact import Redactor
from scripted import ScriptedProvider
from test_agent import proposal
from test_agent import run as run_script

TICKETS = {t["id"]: t for t in load_tickets()}


def action(type_, order_id, amount=None, new_address=None):
    return {"type": type_, "order_id": order_id, "reason": "test", "amount_eur": amount, "new_address": new_address}


def violations(customer, *actions):
    return check_policy(Proposal.model_validate(proposal(actions=list(actions))), customer, ERP())


# --- lookups are limited to the ticket's customer ---

def call(customer_id, tool, args):
    async def go():
        async with Client(build_server(ERP(), customer_id)) as c:
            out = await c.call_tool(tool, args)
            return json.loads(out.content[0].text)
    return anyio.run(go)


def test_other_customers_order_looks_like_a_missing_one():
    assert "error" in call("C-201", "get_order", {"order_id": "SO-5003"})  # SO-5003 belongs to C-203
    assert call("C-201", "get_order", {"order_id": "SO-5001"})["order_id"] == "SO-5001"


def test_other_customers_orders_and_shipments_are_refused():
    assert "error" in call("C-201", "list_customer_orders", {"customer_id": "C-203"})
    assert "error" in call("C-201", "get_shipment_events", {"shipment_id": "SH-9003"})
    assert call("C-201", "get_shipment_events", {"shipment_id": "SH-9001"})


def test_injected_lookup_of_another_customer_never_reaches_the_model():
    ticket = TICKETS["T-1007"]  # customer C-201
    provider = ScriptedProvider([
        [("get_order", {"order_id": "SO-5003"}), ("list_customer_orders", {"customer_id": "C-203"})],
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", proposal())],
    ])
    result = anyio.run(lambda: triage(ticket, provider, ERP()))
    seen = "\n".join(provider.seen)
    assert "SO-5003\", \"customer_id" not in seen and "Tunturitie" not in seen  # C-203's order and address
    assert result.proposal is not None


# --- policy ---

def test_split_credits_are_capped_per_order():
    # Four credits of 25 % each used to pass one by one.
    assert violations("C-203", *[action("issue_credit", "SO-5003", 117.5)] * 4)
    assert not violations("C-203", action("issue_credit", "SO-5003", 60.0))


@pytest.mark.parametrize("amount", [math.nan, math.inf, -5.0, 0.0, None])
def test_credit_amount_must_be_a_positive_number(amount):
    assert violations("C-203", action("issue_credit", "SO-5003", amount))


def test_address_change_after_pickup_is_blocked():
    assert violations("C-201", action("update_delivery_address", "SO-5005", new_address="Satamatie 2, Kemi"))


def test_redirect_only_with_carriers_that_allow_it():
    # Opus asked ArcticPost for a redirect on T-1009 in 3 of 3 runs; the runbook says only NordFreight does them.
    assert "does not redirect" in violations("C-203", action("contact_carrier", "SO-5007", new_address="Koskikatu 10, Rovaniemi"))[0]
    assert not violations("C-203", action("contact_carrier", "SO-5007"))  # asking about the shipment is fine
    assert not violations("C-201", action("contact_carrier", "SO-5005", new_address="Satamatie 2, Kemi"))  # NordFreight


def test_address_change_needs_an_address():
    assert violations("C-201", action("update_delivery_address", "SO-5005", new_address=" "))


def test_reship_without_stock_is_blocked():
    assert violations("C-202", action("reship_order", "SO-5002"))  # BOX-ESD-300 has 0 on hand
    assert not violations("C-204", action("reship_order", "SO-5004"))


def test_proposal_needs_evidence():
    with pytest.raises(ValueError):
        Proposal.model_validate(proposal(evidence=[]))


# --- personal data ---

def test_lowercase_and_other_formats_are_masked():
    r = Redactor()
    text = r.redact("iban fi21 1234 5600 0007 85, hetu 131052-308t, tel +358 40 1234 567 or 0442223333")
    assert "fi21" not in text and "308t" not in text and "1234 567" not in text and "0442223333" not in text


def test_dates_amounts_and_ids_are_not_masked():
    text = "SO-5001 promised 2026-11-10, 300 of 400 units, invoice INV-7003 470.0 EUR, 12000 on hand"
    assert Redactor().redact(text) == text


@pytest.mark.parametrize("reply, problem", [
    ("Please call us at [PHONE_1].", "[PHONE_1]"),        # the customer's own number offered as ours
    ("We will update you by [DATE_1].", "[DATE_1]"),     # a placeholder the model made up
    ('Kind regards,\nCustomer Support"', "quotation"),  # the model wrapped its answer in quotes
    ("   ", "empty"),
])
def test_a_reply_not_ready_to_send_is_flagged(reply, problem):
    assert problem in " ".join(reply_problems(reply))


def test_a_clean_reply_passes():
    assert reply_problems('We are on it and will update you within two working days. "Urgent" is noted.') == []


@pytest.mark.parametrize("start, end, days", [
    ("2026-11-10", "2026-11-23", 9),   # T-1001/T-1007: Tue 10 Nov to Mon 23 Nov
    ("2026-11-20", "2026-11-23", 1),   # Friday to Monday is one working day
    ("2026-11-21", "2026-11-23", 1),   # from a Saturday
    ("2026-11-23", "2026-11-23", 0),
])
def test_working_days_are_counted_in_code(start, end, days):
    assert working_days_between(date.fromisoformat(start), date.fromisoformat(end)) == days


# --- agent loop ---

def test_proposal_without_a_lookup_is_sent_back():
    provider = ScriptedProvider([[("submit_proposal", proposal())]])
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert provider.tool_results[0].is_error and result.proposal is None


def test_calls_after_an_accepted_proposal_are_not_run():
    provider = ScriptedProvider([
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", proposal()), ("get_order", {"order_id": "SO-5005"})],
    ])
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert [s.detail["arguments"] for s in result.trace if s.name == "get_order"] == [{"order_id": "SO-5001"}]


def test_failed_lookup_does_not_count_as_evidence():
    provider = ScriptedProvider([
        [("get_order", {"order_id": "SO-9999"})],
        [("submit_proposal", proposal())],
    ])
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert result.proposal is None


def test_model_errors_surface_as_provider_errors():
    from support_agent.providers import ProviderError

    class Down(ScriptedProvider):
        def step(self):
            raise ProviderError("Ollama request failed: connection refused")

    with pytest.raises(ProviderError):
        anyio.run(lambda: triage(TICKETS["T-1001"], Down([]), ERP()))


def test_amount_or_address_on_the_wrong_action_is_dropped_before_approval():
    p = Proposal.model_validate(proposal(actions=[
        action("contact_carrier", "SO-5001", amount=math.inf, new_address="Satamatie 2, Kemi"),  # a redirect
        action("reship_order", "SO-5004", amount=650.0, new_address="Somewhere 1"),
    ]))
    notes = drop_stray_fields(p)
    assert len(notes) == 3
    assert p.actions[0].amount_eur is None and p.actions[0].new_address == "Satamatie 2, Kemi"
    assert p.actions[1].amount_eur is None and p.actions[1].new_address is None


def test_blank_evidence_is_refused():
    with pytest.raises(ValueError):
        Proposal.model_validate(proposal(evidence=["   "]))


# Recorded from Haiku 5.5 on T-1008: the rest of the tool call landed in the first text field,
# and the schema's one required evidence item was filled with "x".
LEAKED = ('Overcharged 60.00 EUR.</likely_cause> <parameter name="evidence">["INV-7003 total 470.00"]')


@pytest.mark.parametrize("overrides", [
    {"likely_cause": LEAKED},
    {"customer_reply": "We will review a credit.</customer_reply>\n<parameter name=\"confidence\">high"},
    {"evidence": ["x"]},
])
def test_leaked_tool_markup_and_filler_evidence_are_refused(overrides):
    with pytest.raises(ValueError):
        Proposal.model_validate(proposal(**overrides))


def test_ordinary_angle_brackets_are_fine():
    Proposal.model_validate(proposal(likely_cause="Delay < 5 working days, so no credit; > 10 needs a manager."))


def test_a_leaked_proposal_goes_back_to_the_model():
    result, _ = run_script("T-1001", [
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", proposal(likely_cause=LEAKED, evidence=["x"]))],
        [("submit_proposal", proposal())],
    ])
    assert result.proposal is not None and "invalid_proposal" in [s.name for s in result.trace if s.kind == "note"]


def test_empty_lookup_result_is_not_data():
    provider = ScriptedProvider([
        [("list_customer_orders", {"customer_id": "C-999"})],  # refused: not the ticket's customer
        [("submit_proposal", proposal())],
    ])
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert result.proposal is None


@pytest.mark.parametrize("phone", ["040-123-4567", "+358-40-123-4567", "+358 40-123-4567", "(09) 123 4567"])
def test_dashed_and_bracketed_phone_numbers_are_masked(phone):
    assert Redactor().redact(f"call {phone} today") == "call [PHONE_1] today"


def test_malformed_ollama_responses_are_provider_errors():
    import httpx
    from support_agent.providers import OllamaProvider, ProviderError

    for body in ({"message": None}, {"message": {"tool_calls": [{"nope": 1}]}}, {"other": 1}):
        http = httpx.Client(transport=httpx.MockTransport(lambda request, body=body: httpx.Response(200, json=body)))
        provider = OllamaProvider(http=http)
        provider.start("system", "user", [])
        with pytest.raises(ProviderError):
            provider.step()



def test_get_order_reports_working_days_late_only_for_open_late_orders():
    async def go(order_id):
        async with Client(build_server(ERP(), None, date(2026, 11, 23))) as c:
            return json.loads((await c.call_tool("get_order", {"order_id": order_id})).content[0].text)
    assert anyio.run(go, "SO-5001")["working_days_late"] == 9      # shipped, promised 10 Nov
    assert "working_days_late" not in anyio.run(go, "SO-5004")      # delivered
    assert "working_days_late" not in anyio.run(go, "SO-5005")      # promised 24 Nov, not late yet


def test_a_reply_with_a_placeholder_goes_back_to_the_model():
    provider = ScriptedProvider([
        [("get_order", {"order_id": "SO-5001"})],
        [("submit_proposal", proposal(customer_reply="Call us at [PHONE_1]."))],
        [("submit_proposal", proposal(customer_reply="We will update you within two working days."))],
    ])
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert [s.name for s in result.trace if s.kind == "note"] == ["invalid_reply"]
    assert result.proposal.customer_reply == "We will update you within two working days."


def test_a_model_stuck_on_a_broken_proposal_gives_up_early():
    broken = [("submit_proposal", proposal(evidence=[]))]
    provider = ScriptedProvider([[("get_order", {"order_id": "SO-5001"})]] + [broken] * 6)
    result = anyio.run(lambda: triage(TICKETS["T-1001"], provider, ERP()))
    assert result.proposal is None and "rejected 3 times" in result.error
    assert len(provider.seen) < 6  # stopped before using up its steps
