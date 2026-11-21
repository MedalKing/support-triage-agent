"""MCP server exposing read-only ERP lookups and runbook search to the agent.

Run standalone over stdio:  python -m support_agent.erp_server
There are deliberately no write tools here: the agent can look things up and propose
actions, but only a person can make changes (least privilege).
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .erp import ERP
from .retrieval import load_runbooks


def build_server(erp: ERP | None = None) -> MCPServer:
    erp = erp or ERP()
    runbooks = load_runbooks()
    server = MCPServer(name="erp", instructions="Read-only ERP lookups and support runbooks.")

    def dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    @server.tool()
    def get_order(order_id: str) -> str:
        """Get an order with its lines, dates, carrier, shipment id, price list and customer."""
        order = erp.get_order(order_id)
        return dump(order) if order else dump({"error": f"order {order_id} not found"})

    @server.tool()
    def list_customer_orders(customer_id: str) -> str:
        """List a customer's orders (id, status, promised date, SKUs). Use when an order id is unknown."""
        return dump(erp.list_customer_orders(customer_id))

    @server.tool()
    def get_shipment_events(shipment_id: str) -> str:
        """Get the carrier tracking events for a shipment, oldest first."""
        events = erp.get_shipment_events(shipment_id)
        return dump(events) if events is not None else dump({"error": f"shipment {shipment_id} not found"})

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
    build_server().run("stdio")
