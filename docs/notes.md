# Engineering Notes

Detailed notes on eval observations, failure modes found during development, and keyless CI authentication.

## What failed, and what changed

The first public version had mistakes that the eval didn't catch. Reviewing it and walking through the demo surfaced them; each became a fix plus a test or eval check:

- **The reply told the customer to call their own phone number.** The model saw `[PHONE_1]`, used it as our contact line, and the app restored the real number. Now any placeholder in the reply sends it back to the model, and the restoring code is gone. This also stopped invented placeholders such as `[DATE_1]` and stray quotation marks.
- **"11 working days late" when it was 9.** Models miscount calendar days, and the runbook thresholds (5 and 10 working days) depend on it. The lookup now returns `working_days_late` computed in code, and the eval checks every "N working days late" the agent writes.
- **The sample data was inconsistent.** Haiku kept proposing a second credit on another order of the same customer, and by the runbook it was right: that order had the expired price list too. The data was fixed rather than loosening the eval. Orders also lacked an order date, which both models flagged; they have one now.
- **Haiku sometimes sent an empty evidence list repeatedly** and burned its whole step budget. A run now gives up after three rejected proposals, handing the ticket to Opus escalation sooner.
- **Requiring one evidence item in the schema hid a worse error.** Adding `minItems: 1` (strict tool use enforces it) cut refused proposals from 8 to 1 in 24 sampled Haiku runs, and eval passes went from 23/24 to 24/24 ([before](../evals/results/archive/2026-11-26-haiku-sample-before-minitems.json), [after](../evals/results/archive/2026-11-26-haiku-sample-after-minitems.json)). But the empty list had been a symptom: Haiku sometimes writes the rest of its tool call as text into the first field (`...</likely_cause> <parameter name="evidence">[...]`). Forced to give one evidence item, it wrote `x`, and two of the nine recorded runs shipped with markup in the cause and the reply. The eval only scored actions and the reply's placeholders, so it passed them. Now tag-like markup in any text field (including an action's order id and address), or an evidence item too short to be a fact, sends the proposal back to the model with a message that says where the evidence went; `minItems` is gone so an empty list shows up again; and recorded runs are validated when loaded, so CI fails on one like these. "Fewer refusals" was the wrong thing to celebrate. With the final checks, a [27-run Haiku sample](../evals/results/2026-11-26-haiku-sample-final.json) had proposals sent back in 5 runs: 4 recovered and passed, and one (`T-1001`) was refused three times in a row, which escalates in `auto`. (That file keeps the number of refusals per run, not their messages.) Why `T-1001` leaked most turned out to be a matter of field order ([experiment](../evals/experiments/2026-11-26-t1001-field-order.json), 12 Haiku runs per variant). With the long free-text `likely_cause` before the `evidence` list, 6 of 12 runs leaked. With `evidence` first, none did. Turning strict tool use off also stopped the leaks, but caused 13 other schema errors and 2 runs without a proposal. Effort made no clear difference. The schema now asks for evidence first, and a 33-run sample over all eleven tickets had no leaks at all.
- **Half the recorded runs escalated without needing to.** Any refused proposal counted as "the model had to be corrected", so a formatting slip that Haiku fixed on its next try sent the ticket to Opus. In a 24-run Haiku sample, 5 runs had exactly one such slip and all 5 passed their eval, and Opus changed nothing in the recorded cases. One slip the model fixes no longer escalates; two or more still do.
- **A duplicate credit was only caught because of the amounts.** Two 60 € credits on a 470 € invoice broke the 25% cap, but on a larger invoice the same complaint could have been paid twice. Now any second credit on an invoice requires a manager, and the order lookup exposes already-approved credits to the agent.
- **A proposal with no actions at all didn't escalate.** Haiku once answered a pricing error with high confidence and an empty action list; that now escalates to Opus.
- **The eval could pass an escalated run on the first model's lookups.** It now scores only the steps of the model that wrote the final proposal (the privacy check still covers both).

## Eval observations

The demo runs on a fixed date (`2026-11-23`) matching the synthetic order data. Escalation varies across runs: 1–2 of 11 tickets in the current `auto` runs. The recorded `T-1006` escalation was picked for the tour from several recording attempts because it shows the second model changing the outcome; the rates above are the honest number.

In recent Haiku-only runs, Haiku failed one case per run in three different ways:
- On `T-1008`, finding the overcharge but proposing no actions.
- On `T-1006`, failing to produce a valid proposal.
- On `T-1006`, answering a question about a nonexistent order with a 60 € credit on another order at high confidence.

The first two now trigger Opus escalation in `auto`. The third is within policy (same customer, under the cap), and at first it only escalated when Haiku happened to add a no-action for the missing order. A dedicated signal now catches it: a real action on an order the customer never mentioned, by order or invoice number. In a 33-run Haiku sample it caught all three of Haiku's failures, and it fires on none of the recorded runs' correct answers.

Two tickets were added to tempt Haiku into a credit it shouldn't give. On `T-1010`, the customer demands 30% off a late order; on `T-1011`, the overcharge is above the 25% cap. In trial runs Haiku mostly followed the runbooks: 6 of 6 on `T-1011`, and on `T-1010` it once proposed a credit, which the policy check blocked and Opus redid correctly. On `T-1010` it often listed the declined discount as a no-action next to its real actions. That used to count as contradictory; a no-action next to real actions on the same order now counts as recording a declined part of the request.

`T-1009` was added to find a case where the stronger model helps, and showed the opposite. The customer asks to redirect goods that ArcticPost has already picked up, and the runbook says only NordFreight redirects in transit. Haiku followed the runbook in 6 of 6 trial runs; Opus asked ArcticPost for a redirect in 3 of 3, passed one later eval run, and failed again in the final Opus-only run, where the policy check blocked it. The rule is now a policy check, so it holds whichever model proposes. Measuring cost, latency, and accuracy across a larger case set is the next step.

Earlier result files are in [`evals/results/archive/`](../evals/results/archive/); because the suite grew stricter over time, only the current runs in [`evals/results/`](../evals/results/) are directly comparable. Its README lists them, and so does the demo's admin-only Evals page.

## CI without an API key

- **CI** (every push and pull request): runs the offline test suite (including checking that every recorded run passes its eval case) and the web build. No model calls.
- **Live evals** (manual `workflow_dispatch`): runs the eleven cases against Claude without storing an API key in GitHub. The job requests a short-lived GitHub OIDC token, which the Anthropic SDK exchanges for a 10-minute access token via workload identity federation. The federation rule accepts only `workflow_dispatch` runs on `main` of this repository (pinned to numeric owner and repository IDs) and maps to a service account scoped to one workspace. Actions are pinned to commit SHAs, CI has read-only repository permissions, and the identity token file is deleted after the run.

## From demo to production

**What carries over as-is:** the trust boundary (read-only MCP tools scoped to the sender, deterministic policy checks at both proposal and approval, proposal-bound approvals), the audit log of approvals and declines with reasons, escalation on checkable signals, per-run cost/latency tracking, and deterministic evals gating changes.

**What would change:**

1. **Real systems behind the same tools:** MCP tools call real ERP read APIs with the customer derived from the authenticated ticket, never from model input. Approved actions call write APIs with the `proposal_id` as the idempotency key.
2. **People and roles:** Reviewers sign in with SSO (recorded in the audit log). Blocked credits route to a manager queue rather than stopping at "needs a manager", and the Evals view sits behind an admin role.
3. **State and intake:** Tickets arrive from the helpdesk via webhook; proposals, decisions, and an append-only audit log live in a database. Approved draft replies push back to the helpdesk as drafts for a human to send.
4. **Privacy:** Mask names and street addresses in addition to structured identifiers, enforce trace retention periods, and review data-processing terms with the model provider.
5. **Quality over time:** Grow the eval set from sampled real tickets and reviewer declines; track pass rate, escalation rate, and cost per ticket per release; gate model upgrades on passing evals.
6. **Operations:** Alert on error and escalation spikes, enforce workspace spend limits, and pin model versions so behavior doesn't drift underneath.
