# Delivery address change

Applies when the customer wants to change where an order is delivered.

1. Before pickup: propose `update_delivery_address` (needs approval).
2. After pickup (`picked_up` event exists): the address cannot be changed in our system. Propose `contact_carrier` to request a redirect; NordFreight allows redirects while in transit, other carriers do not.
3. Confirm the new address with the customer in the reply.
