"""MCP server exposing read-only ERP lookups and runbook search to the agent.

Run standalone over stdio:  python -m support_agent.erp_server
There are deliberately no write tools here: the agent can look things up and propose
actions, but only a person can make changes (least privilege). With a customer id, the
lookups only return that customer's orders and shipments, so a ticket can't steer the
agent into another customer's data.

    python -m support_agent.erp_server --customer C-201
"""

from __future__ import annotations

import argparse
import json

from mcp.server.mcpserver import MCPServer

from .erp import ERP
from .retrieval import load_runbooks


def build_server(erp: ERP | None = None, customer_id: str | None = None) -> MCPServer:
    erp = erp or ERP()
    runbooks = load_runbooks()
    server = MCPServer(name="erp", instructions="Read-only ERP lookups and support runbooks.")

    def dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    def allowed(order: dict | None) -> bool:
        return order is not None and (customer_id is None or order["customer_id"] == customer_id)

    @server.tool()
    def get_order(order_id: str) -> str:
        """Get an order with its lines, dates, carrier, shipment id, price list and customer."""
        order = erp.get_order(order_id)
        # Another customer's order looks the same as a missing one: nothing leaks either way.
        return dump(order) if allowed(order) else dump({"error": f"order {order_id} not found"})

    @server.tool()
    def list_customer_orders(customer_id: str) -> str:
        """List the ticket customer's orders (id, status, promised date, SKUs). Use when an order id is unknown."""
        if not allowed({"customer_id": customer_id}):
            return dump({"error": "only the ticket's own customer can be listed"})
        return dump(erp.list_customer_orders(customer_id))

    @server.tool()
    def get_shipment_events(shipment_id: str) -> str:
        """Get the carrier tracking events for a shipment, oldest first."""
        events = erp.get_shipment_events(shipment_id)
        owner = erp.get_order_by_shipment(shipment_id)
        if events is None or not allowed(owner):
            return dump({"error": f"shipment {shipment_id} not found"})
        return dump(events)

    @server.tool()
    def get_stock(sku: str) -> str:
        """Get on-hand stock and the next restock date for a SKU."""
        stock = erp.get_stock(sku)
        return dump(stock) if stock else dump({"error": f"sku {sku} not found"})

    @server.tool()
    def get_price_list(price_list_id: str) -> str:
        """Get a price list with its validity dates and unit prices."""
        pl = erp.get_price_list(price_list_id)
        return dump(pl) if pl else dump({"error": f"price list {price_list_id} not found"})

    @server.tool()
    def search_runbooks(query: str) -> str:
        """Search the support runbooks. Always check the relevant runbook before proposing actions."""
        hits = runbooks.search(query, k=2)
        return dump([{"runbook": d.doc_id, "score": round(s, 2), "text": d.text} for d, s in hits])

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", help="only serve this customer's orders and shipments")
    build_server(customer_id=parser.parse_args().customer).run("stdio")
