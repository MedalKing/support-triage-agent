"""The structured proposal the agent must return, and policy checks that run in code.

The model proposes; code checks the proposal against policy; a person approves.
Policy lives here, not only in the prompt, so a prompt injection can't talk its way past it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "carrier_delay", "partial_shipment_stockout", "pricing_error", "transport_damage",
    "address_change", "wrong_reference", "other",
]
ActionType = Literal[
    "contact_carrier", "expedite_shipment", "reship_order", "issue_credit",
    "update_delivery_address", "no_action",
]
# Actions that change something for the customer or cost money need a person's approval.
NEEDS_APPROVAL = {"expedite_shipment", "reship_order", "issue_credit", "update_delivery_address"}
MAX_AGENT_CREDIT_SHARE = 0.25  # larger credits are out of scope for the agent


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ActionType
    order_id: str
    reason: str
    amount_eur: float | None = None
    new_address: str | None = None


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    likely_cause: str
    evidence: list[str] = Field(min_length=1)
    actions: list[Action]
    customer_reply: str
    confidence: Literal["low", "medium", "high"]

    @field_validator("evidence")
    @classmethod
    def _evidence_not_blank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("evidence items must not be blank")
        return value


def _nullable(kind: str) -> dict[str, Any]:
    return {"anyOf": [{"type": kind}, {"type": "null"}]}


# Hand-written so it is valid for strict tool use (every property required, no extras).
SUBMIT_PROPOSAL_TOOL = {
    "name": "submit_proposal",
    "description": (
        "Submit your final triage proposal for a human to review. Call this exactly once, "
        "after you have checked the ERP data and the relevant runbook."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["category", "likely_cause", "evidence", "actions", "customer_reply", "confidence"],
        "properties": {
            "category": {"type": "string", "enum": list(Category.__args__)},
            "likely_cause": {"type": "string", "description": "One or two sentences."},
            "evidence": {"type": "array", "items": {"type": "string"},
                         "description": "Facts from tool results that support the cause, with ids."},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "order_id", "reason", "amount_eur", "new_address"],
                    "properties": {
                        "type": {"type": "string", "enum": list(ActionType.__args__)},
                        "order_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "amount_eur": _nullable("number"),
                        "new_address": _nullable("string"),
                    },
                },
            },
            "customer_reply": {"type": "string",
                               "description": "Draft reply to the customer. Keep placeholders like [EMAIL_1] as they are."},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
    },
}


# Which action types an amount or an address belongs to. A carrier redirect carries the new address.
TAKES_AMOUNT = {"issue_credit"}
TAKES_ADDRESS = {"update_delivery_address", "contact_carrier"}


def drop_stray_fields(proposal: Proposal) -> list[str]:
    """Remove amounts and addresses on actions they don't belong to, so they can't reach the
    audit log. Returns a note for each one removed."""
    notes = []
    for i, action in enumerate(proposal.actions):
        if action.amount_eur is not None and action.type not in TAKES_AMOUNT:
            notes.append(f"action {i} ({action.type}): dropped amount {action.amount_eur!r}")
            action.amount_eur = None
        if action.new_address is not None and action.type not in TAKES_ADDRESS:
            notes.append(f"action {i} ({action.type}): dropped address")
            action.new_address = None
    return notes


def check_policy(proposal: Proposal, ticket_customer_id: str, erp: Any, proposal_id: str | None = None) -> list[str]:
    """Return policy violations. Violating actions are blocked in the UI and at the approval API.

    The credit cap counts credits already approved on the order by other proposals, so two
    tickets about the same invoice can't add up past it.
    """
    problems = []
    credits: dict[str, float] = defaultdict(float)
    for action in proposal.actions:
        if action.type == "no_action":
            continue
        order = erp.get_order(action.order_id)
        if order is None:
            problems.append(f"{action.type}: order {action.order_id} does not exist")
            continue
        if order["customer_id"] != ticket_customer_id:
            problems.append(f"{action.type}: order {action.order_id} belongs to another customer")
            continue
        if action.type == "issue_credit":
            if action.amount_eur is None or not math.isfinite(action.amount_eur) or action.amount_eur <= 0:
                problems.append("issue_credit: amount must be a positive number")
            elif order.get("invoice_total_eur") is None:
                problems.append(f"issue_credit: order {action.order_id} has no invoice")
            else:
                credits[action.order_id] += action.amount_eur
        elif action.type == "update_delivery_address":
            if not (action.new_address or "").strip():
                problems.append("update_delivery_address: new address missing")
            events = erp.get_shipment_events(order.get("shipment_id", "")) or []
            if any(e["event"] == "picked_up" for e in events):
                problems.append(f"update_delivery_address: order {action.order_id} is already picked up; "
                                "ask the carrier for a redirect instead")
        elif action.type == "reship_order":
            for line in order["lines"]:
                stock = erp.get_stock(line["sku"])
                if not stock or stock["on_hand"] <= 0:
                    problems.append(f"reship_order: {line['sku']} is out of stock; the remainder is a backorder")
    # The cap is per order, so splitting a credit into several actions doesn't get around it.
    for order_id, proposed in credits.items():
        invoice = erp.get_order(order_id)["invoice_total_eur"]
        earlier = erp.approved_credit_eur(order_id, except_proposal=proposal_id)
        # Compare in whole cents so float rounding can't nudge an amount past the cap.
        if round((earlier + proposed) * 100) > round(MAX_AGENT_CREDIT_SHARE * invoice * 100):
            already = f" ({earlier:.2f} EUR already approved)" if earlier else ""
            problems.append(
                f"issue_credit: {earlier + proposed:.2f} EUR on {order_id}{already} exceeds "
                f"{MAX_AGENT_CREDIT_SHARE:.0%} of the invoice ({invoice:.2f} EUR); needs a manager"
            )
    return problems
