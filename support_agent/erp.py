"""A tiny in-memory stand-in for an ERP system, loaded from synthetic JSON data.

Read functions are exposed to the agent through the MCP server. Write functions are not:
they run only after a person approves a proposed action (see api.py).
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"


class ERP:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        if data is None:
            data = json.loads((DATA_DIR / "orders.json").read_text(encoding="utf-8"))
        self._data = copy.deepcopy(data)
        self.audit_log: list[dict[str, Any]] = []

    # --- read side (exposed to the agent) ---

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        order = self._data["orders"].get(order_id)
        if order is None:
            return None
        customer = self._data["customers"][order["customer_id"]]
        return {"order_id": order_id, **order, "customer": {"id": order["customer_id"], **customer}}

    def list_customer_orders(self, customer_id: str) -> list[dict[str, Any]]:
        return [
            {"order_id": oid, "status": o["status"], "promised_date": o["promised_date"],
             "skus": [line["sku"] for line in o["lines"]]}
            for oid, o in self._data["orders"].items()
            if o["customer_id"] == customer_id
        ]

    def get_shipment_events(self, shipment_id: str) -> list[dict[str, Any]] | None:
        return self._data["shipments"].get(shipment_id)

    def get_stock(self, sku: str) -> dict[str, Any] | None:
        stock = self._data["stock"].get(sku)
        return None if stock is None else {"sku": sku, **stock}

    def get_price_list(self, price_list_id: str) -> dict[str, Any] | None:
        pl = self._data["price_lists"].get(price_list_id)
        return None if pl is None else {"price_list_id": price_list_id, **pl}

    # --- write side (only after human approval) ---

    def apply_action(self, action: dict[str, Any], approved_by: str) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "approved_by": approved_by,
            **action,
        }
        self.audit_log.append(entry)
        if action["type"] == "update_delivery_address" and action.get("new_address"):
            self._data["orders"][action["order_id"]]["delivery_address"] = action["new_address"]
        return entry
