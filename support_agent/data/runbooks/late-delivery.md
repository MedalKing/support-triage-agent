# Late delivery

Applies when the promised date has passed and the shipment is not delivered.

1. Check shipment events. An `exception` event at a carrier hub means a carrier-side delay.
2. Carrier-side delay: contact the carrier (action `contact_carrier`) and ask for a new ETA. If the customer is a key account or the delay is more than 5 working days, propose `expedite_shipment` (needs approval).
3. Do not offer credits for carrier delays below 10 working days. Compensation above that needs a manager's approval and is out of scope for agents.
4. Tell the customer what happened, what we are doing and when we will update them. Do not promise a delivery date the carrier has not confirmed.
