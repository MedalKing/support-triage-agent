# Short or partial delivery

Applies when the customer received fewer units than ordered.

1. Compare ordered quantity with `qty_shipped` on the order line and the pickup note on the shipment.
2. If stock was short at dispatch (`on_hand` 0), the remainder is backordered. Check `next_restock` and tell the customer the expected restock date. The dispatch date for the remainder is set only after the stock arrives, so don't promise one, or tracking, yet.
3. If stock is available, propose `reship_order` for the missing quantity (needs approval).
4. Never propose a credit for a backorder that will be shipped.
