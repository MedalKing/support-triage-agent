"""The structured proposal the agent must return, and policy checks that run in code.

The model proposes; code checks the proposal against policy; a person approves.
Policy lives here, not only in the prompt, so a prompt injection can't talk its way past it.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

Category = Literal[
    "carrier_delay", "partial_shipment_stockout", "pricing_error", "transport_damage",
    "address_change", "wrong_reference", "other",
]
ActionType = Literal[
    "contact_carrier", "expedite_shipment", "reship_order", "issue_credit",
    "update_delivery_address", "no_action",
]
# Actions that change something for the customer or cost money need a person's approval.
# The reason is shown to the reviewer next to the action, so a new user knows what to check.
APPROVAL_REASONS = {
    "expedite_shipment": "Faster freight costs us extra. Check that the delay is long enough to justify it.",
    "reship_order": "Sends new goods at our cost. Check the quantity and that the first delivery really fell short.",
    "issue_credit": "Gives the customer money back on the invoice. Check the amount against the invoice.",
    "update_delivery_address": "Changes where the order goes. Check the new address with the customer's message.",
}
NEEDS_APPROVAL = set(APPROVAL_REASONS)
REDIRECT_REASON = "Sends the goods somewhere else. Check the new address with the customer's message."
MAX_AGENT_CREDIT_SHARE = 0.25  # larger credits are out of scope for the agent
# Carriers that accept a new address while the goods are in transit (runbook: address-change).
REDIRECT_CARRIERS = {"NordFreight"}


# Tags such as </likely_cause> or <parameter name="evidence">. Letters right after "<" so that
# "x < 5" in ordinary text doesn't match.
MARKUP = re.compile(r"</?[A-Za-z_][\w:-]*(?:\s[^<>]*)?>")
MIN_EVIDENCE_CHARS = 8  # "x" is filler, not evidence


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

    @model_validator(mode="after")
    def _well_formed(self) -> "Proposal":
        # Haiku sometimes writes the rest of its tool call into the first text field
        # ("...</likely_cause> <parameter name=\"evidence\">[...]"), so the real fields end up
        # empty or filled with filler. Each field must hold only its own text.
        fields = {"likely_cause": self.likely_cause, "customer_reply": self.customer_reply,
                  **{f"evidence[{i}]": e for i, e in enumerate(self.evidence)},
                  **{f"actions[{i}].reason": a.reason for i, a in enumerate(self.actions)}}
        leaked = [name for name, text in fields.items() if MARKUP.search(text)]
        if leaked:
            raise ValueError(f"tool-call markup inside {', '.join(leaked)}; put each value in its own field, "
                             "as plain text")
        # Checked after the markup, so that a model whose evidence leaked into another field is told where it went.
        if not self.evidence:
            raise ValueError("evidence is empty; list the facts from your tool results, with their ids, in the "
                             "evidence field itself")
        if any(len(item.strip()) < MIN_EVIDENCE_CHARS for item in self.evidence):
            raise ValueError("each evidence item must be a fact from a tool result, with its id")
        return self


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
                         "description": "At least one fact from tool results that supports the cause, with ids."},
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
                               "description": "Draft reply to the customer, plain text ready to send, without placeholders."},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
    },
}


PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_ ]*\]")
QUOTES = '"\u201c\u201d'


def reply_problems(reply: str) -> list[str]:
    """Problems that would make the draft reply wrong to send as it is.

    A placeholder in the reply is either the customer's own contact detail (they don't need it,
    and offering it as ours is wrong) or one the model made up. A stray quotation mark is an
    artefact of the model wrapping its answer in quotes. Both rules are deliberately blunt: an
    inch mark (12" boxes) or a bracketed word would also be sent back once, which is cheap.
    """
    problems = []
    tags = sorted(set(PLACEHOLDER.findall(reply)))
    if tags:
        problems.append(f"remove the placeholders {', '.join(tags)}; the reply must be ready to send")
    text = reply.strip()
    if not text:
        problems.append("the reply is empty")
    elif text[0] in QUOTES or text[-1] in QUOTES or sum(text.count(q) for q in QUOTES) % 2:
        problems.append("remove the stray quotation marks around or in the reply")
    return problems


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

    Credits: an order that already has an approved credit from another proposal (another
    ticket, or an earlier run of this one) needs a manager for the next one, whatever the
    amounts. A first credit must stay within the cap, counting all its actions together.
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
        elif action.type == "contact_carrier" and (action.new_address or "").strip():
            if order.get("carrier") not in REDIRECT_CARRIERS:
                problems.append(f"contact_carrier: {order.get('carrier')} does not redirect goods in transit, "
                                f"so {action.order_id} can't be sent to a new address")
        elif action.type == "reship_order":
            for line in order["lines"]:
                stock = erp.get_stock(line["sku"])
                if not stock or stock["on_hand"] <= 0:
                    problems.append(f"reship_order: {line['sku']} is out of stock; the remainder is a backorder")
    for order_id, proposed in credits.items():
        # A credit on an invoice that already got one from another ticket may be the same
        # complaint twice. That is a manager's call, whatever the amounts.
        earlier_entries = erp.approved_credits(order_id, except_proposal=proposal_id)
        if earlier_entries:
            done = ", ".join(f"{e['amount_eur']:.2f} EUR on {e['ticket_id']}" for e in earlier_entries)
            problems.append(f"issue_credit: possible duplicate, {order_id} already has an approved credit "
                            f"({done}); a second credit needs a manager")
            continue
        # The cap is per order, so splitting a credit into several actions doesn't get around it.
        # Compare in whole cents so float rounding can't nudge an amount past the cap.
        invoice = erp.get_order(order_id)["invoice_total_eur"]
        if round(proposed * 100) > round(MAX_AGENT_CREDIT_SHARE * invoice * 100):
            problems.append(f"issue_credit: {proposed:.2f} EUR on {order_id} exceeds "
                            f"{MAX_AGENT_CREDIT_SHARE:.0%} of the invoice ({invoice:.2f} EUR); needs a manager")
    return problems
