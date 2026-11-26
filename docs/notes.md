# Engineering Notes

Detailed notes on eval observations, failure modes found during development, and keyless CI authentication.

## What failed, and what changed

The first public version had mistakes that the eval didn't catch. Reviewing it and walking through the demo surfaced them; each became a fix plus a test or eval check:

- **The reply told the customer to call their own phone number.** The model saw `[PHONE_1]`, used it as our contact line, and the app restored the real number. Now any placeholder in the reply sends it back to the model, and the restoring code is gone. This also stopped invented placeholders such as `[DATE_1]` and stray quotation marks.
- **"11 working days late" when it was 9.** Models miscount calendar days, and the runbook thresholds (5 and 10 working days) depend on it. The lookup now returns `working_days_late` computed in code, and the eval checks every "N working days late" the agent writes.
- **The sample data was inconsistent.** Haiku kept proposing a second credit on another order of the same customer, and by the runbook it was right: that order had the expired price list too. The data was fixed rather than loosening the eval. Orders also lacked an order date, which both models flagged; they have one now.
- **Haiku sometimes sent an empty evidence list repeatedly** and burned its whole step budget. A run now gives up after three rejected proposals, handing the ticket to Opus escalation sooner. The tool schema now also says `minItems: 1`, which strict tool use enforces: refused proposals fell from 9 to 1 in 24 sampled Haiku runs ([before](../evals/results/2026-11-26-haiku-sample-before-minitems.json), [after](../evals/results/2026-11-26-haiku-sample-after-minitems.json): three runs of each ticket, scored with the eval, before the ninth ticket was added).
- **Half the recorded runs escalated without needing to.** Any refused proposal counted as "the model had to be corrected", so a formatting slip that Haiku fixed on its next try sent the ticket to Opus. In 24 sampled Haiku runs that happened 7 times, every run passed its eval, and Opus changed nothing in the recorded cases. One slip the model fixes no longer escalates; two or more still do.
- **A duplicate credit was only caught because of the amounts.** Two 60 € credits on a 470 € invoice broke the 25% cap, but on a larger invoice the same complaint could have been paid twice. Now any second credit on an invoice requires a manager, and the order lookup exposes already-approved credits to the agent.
- **A proposal with no actions at all didn't escalate.** Haiku once answered a pricing error with high confidence and an empty action list; that now escalates to Opus.
- **The eval could pass an escalated run on the first model's lookups.** It now scores only the steps of the model that wrote the final proposal (the privacy check still covers both).

## Eval observations

The demo runs on a fixed date (`2026-11-23`) matching the synthetic order data. Escalation varies across runs: 2–3 of 9 tickets in the 2026-11-26 `auto` runs. The recorded `T-1006` escalation was picked for the tour from several recording attempts because it shows the second model changing the outcome; the rates above are the honest number.

In recent Haiku-only runs, Haiku failed one case per run in three different ways:
- On `T-1008`, finding the overcharge but proposing no actions.
- On `T-1006`, failing to produce a valid proposal.
- On `T-1006`, answering a question about a nonexistent order with a 60 € credit on another order at high confidence.

The first two now trigger Opus escalation in `auto`. The third is within policy (same customer, under the cap). When Haiku pairs it with a no-action for the missing order, as in the recorded tour run, the contradiction escalates; on its own it shows no signal code can check, and human approval and the eval are what stop it.

`T-1009` was added to find a case where the stronger model helps, and showed the opposite. The customer asks to redirect goods that ArcticPost has already picked up, and the runbook says only NordFreight redirects in transit. Haiku followed the runbook in 6 of 6 trial runs; Opus asked ArcticPost for a redirect in 3 of 3 (it passed in the later eval run). The rule is now a policy check, so it holds whichever model proposes. Measuring cost, latency, and accuracy across a larger case set is the next step.

Earlier result files remain in [`evals/results/`](../evals/results/); because the suite grew stricter over time, only the 2026-11-26 runs in the README table are directly comparable.

## CI without an API key

- **CI** (every push and pull request): runs the offline test suite (including checking that every recorded run passes its eval case) and the web build. No model calls.
- **Live evals** (manual `workflow_dispatch`): runs the nine cases against Claude without storing an API key in GitHub. The job requests a short-lived GitHub OIDC token, which the Anthropic SDK exchanges for a 10-minute access token via workload identity federation. The federation rule accepts only `workflow_dispatch` runs on `main` of this repository (pinned to numeric owner and repository IDs) and maps to a service account scoped to one workspace. Actions are pinned to commit SHAs, CI has read-only repository permissions, and the identity token file is deleted after the run.
