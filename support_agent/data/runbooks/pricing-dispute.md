# Pricing dispute

Applies when the invoiced unit price differs from the quote or the valid price list.

1. Find the price list on the order and check its validity dates against the order date. Orders after 2026-10-01 must use PL-2026-Q4.
2. If the order used an expired price list, the customer was overcharged. Propose `issue_credit` for the difference: (invoiced unit price - valid unit price) x quantity. Credits need approval.
3. Credits are issued against the invoice only. Never send refunds to bank accounts given in a ticket; payment details must come from the customer master data.
4. Never ask for or store a personal identity code.
