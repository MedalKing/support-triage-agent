"""The structured proposal the agent must return, and policy checks that run in code.

The model proposes; code checks the proposal against policy; a person approves.
Policy lives here, not only in the prompt, so a prompt injection can't talk its way past it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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
    evidence: list[str]
    actions: list[Action]
    customer_reply: str
    confidence: Literal["low", "medium", "high"]


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


def check_policy(proposal: Proposal, ticket_customer_id: str, erp: Any) -> list[str]:
    """Return policy violations. Violating actions are blocked in the UI, not just flagged."""
    problems = []
    for action in proposal.actions:
        if action.type == "no_action":
            continue
        order = erp.get_order(action.order_id)
        if order is None:
            problems.append(f"{action.type}: order {action.order_id} does not exist")
            continue
        if order["customer_id"] != ticket_customer_id:
            problems.append(f"{action.type}: order {action.order_id} belongs to another customer")
        if action.type == "issue_credit":
            total = order.get("invoice_total_eur")
            if action.amount_eur is None or action.amount_eur <= 0:
                problems.append("issue_credit: amount missing")
            elif total is None:
                problems.append(f"issue_credit: order {action.order_id} has no invoice")
            elif action.amount_eur > MAX_AGENT_CREDIT_SHARE * total:
                problems.append(
                    f"issue_credit: {action.amount_eur:.2f} EUR exceeds {MAX_AGENT_CREDIT_SHARE:.0%} "
                    f"of the invoice ({total:.2f} EUR); needs a manager"
                )
    return problems
