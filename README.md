# Support triage agent

## In plain terms

A packaging company's customer service gets emails like *"Your invoice charges 2.35 EUR per box but we were quoted 2.05."* Answering one means looking up the order and the price that applied, then deciding what to do.

This project is an AI assistant that does that legwork. It reads the email, checks the company's records and the internal instructions, and suggests what happened, what to do and how to reply. **A person always makes the decision**: the assistant can look things up but cannot change anything itself.

It is built with safeguards. It can only look up the records of the customer who wrote. Email addresses, phone numbers, bank account numbers and personal identity codes are hidden from the AI. Fixed rules in the code block actions outside policy, such as a large credit or a change to another customer's order, even if an email tries to talk the AI into them. A set of sample emails checks how often it gets the answer right.

It is a demo: the company and all data are made up, and it has no login or database. The rest of this page is the technical detail.

## How it works

An LLM agent that investigates customer support tickets for a (fictional) packaging manufacturer and proposes what to do. A person reviews and approves every action.

It reads a ticket, looks up the order, shipment, stock and price list in a mock ERP through an **MCP server**, retrieves the matching **runbook**, and submits a **structured proposal**: likely cause, evidence, actions and a draft reply. Lookups are **limited to the ticket's customer**. **Contact details, bank accounts and identity codes are masked** before the model sees them. **Policy runs in code**, so a prompt injection in a ticket can't get an out-of-policy action approved. The same loop runs on **Claude** or a **local model through Ollama**.

All data is synthetic.

```
ticket ──► redact ──► agent loop ──► submit_proposal ──► policy check ──► human approval ──► ERP write + audit log
                        │   ▲
                        ▼   │   (read-only)
        MCP server, scoped to the ticket's customer: get_order · list_customer_orders · get_shipment_events
                                get_stock · get_price_list · search_runbooks (BM25)
```

## Why it is built this way

| Concern | Design |
| --- | --- |
| The agent must not change things on its own | The MCP server exposes **read-only** tools. Writes exist only behind the approval endpoint (`support_agent/api.py`). An approval names the proposal it was given for, so a newer run can't swap the actions underneath the reviewer; the whole selection is checked before anything is written, and each action can be applied only once. |
| Another customer's data | The MCP server is built for the ticket's customer. Another customer's order or shipment looks the same as a missing one, so an injected ticket can't pull it into the model's context or the reply. |
| Prompt injection in customer text | The system prompt treats tickets as data, and `check_policy` in `support_agent/proposal.py` blocks: credits above 25 % of the invoice in total per order, credits that aren't a positive amount, address changes after pickup, reships of out-of-stock goods, and actions on another customer's or a missing order. The UI disables approval when a check fails, and so does the API. Ticket T-1007 tries an injection. |
| Personal data | `support_agent/redact.py` replaces email addresses, phone numbers, IBANs and Finnish personal identity codes with placeholders such as `[EMAIL_1]`, in the ticket **and** in ERP results. Names and street addresses are not masked. The mapping stays server-side; the reviewer sees contact details restored in the draft reply, but bank accounts and identity codes are never put back. Tests check that none of the personal values in the sample data reach the model. |
| Answers grounded in data | A proposal is accepted only after at least one ERP lookup returned data, and it must list evidence. With Claude the schema is enforced by strict tool use; with Ollama it is validated in code. An invalid proposal goes back to the model to fix. |
| Not tied to one vendor | `support_agent/providers.py` hides Claude (Messages API) and Ollama (native `/api/chat`) behind one small interface. |
| Knowing whether it works | Offline tests run the whole loop with a scripted model, including one test per safeguard (`tests/test_safeguards.py`, `tests/test_api.py`). `evals/` scores live runs on deterministic checks: category, successful lookups, required actions on the right order, forbidden actions, policy, credit amounts, and no personal data to the model. A failed case fails the run. |

Retrieval is BM25 over six short runbooks. That is a strong, explainable baseline at this size; embeddings would replace only `support_agent/retrieval.py`.

## Run it

Requires Python 3.11+ and Node 20+.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                          # offline, no API key needed
```

Choose a model:

```bash
export ANTHROPIC_API_KEY=...                       # Claude (default: claude-opus-5-5; set CLAUDE_MODEL to change)
# or
ollama pull qwen3:8b && export AGENT_PROVIDER=ollama   # local; set OLLAMA_MODEL to change
```

Command line:

```bash
python -m support_agent.cli --list
python -m support_agent.cli T-1007                 # the prompt-injection ticket
```

Web UI (review proposals, approve actions, see the trace):

```bash
cd web && npm ci && npm run build && cd ..
uvicorn support_agent.api:app
# open http://localhost:8000
```

Evals:

```bash
python evals/run_evals.py --provider claude        # 8 tickets, a few cents
python evals/run_evals.py --provider ollama
```

## Results

| Date | Provider | Passed | Results |
| --- | --- | --- | --- |
| 2026-11-23 | Claude Opus 5.5 (`claude-opus-5-5`, effort medium), before the fixed date and the stricter T-1001 | 8/8 | [evals/results/2026-11-23-1358-claude_claude-opus-5-5.json](evals/results/2026-11-23-1358-claude_claude-opus-5-5.json) |
| 2026-11-24 | Claude Opus 5.5 (`claude-opus-5-5`, effort medium) | 8/8 | [evals/results/2026-11-24-1211-claude_claude-opus-5-5.json](evals/results/2026-11-24-1211-claude_claude-opus-5-5.json) |
| 2026-11-24 | Qwen3 8B (`qwen3:8b`) through Ollama 0.40.18, local on an RTX 4090 | 6/8 | [evals/results/2026-11-24-1218-ollama_qwen3_8b.json](evals/results/2026-11-24-1218-ollama_qwen3_8b.json) |

Every case checks the category, the lookups that returned data, the required actions on the right order, the forbidden actions, the policy check, and that none of the personal values in the sample data reached the model; the pricing cases also check the credit amount to the cent. That includes the prompt-injection ticket (T-1007) and the ticket with an IBAN and a personal identity code (T-1008). Eight cases is a smoke test, not a benchmark.

The demo runs on a fixed date, Monday 2026-11-23, which fits the sample data. It is given to the model in the system prompt, not in the ticket text (`DEMO_TODAY` in `support_agent/agent.py`; `AGENT_TODAY=YYYY-MM-DD` overrides it for the app, while the evals always use the demo date). Without it the model can't tell how late an order is. The 2026-11-24 runs use stricter cases than the 2026-11-23 baseline: T-1001 also requires expediting the late order, T-1002 forbids reshipping goods that are out of stock, T-1004 requires a carrier claim, T-1007 requires contacting the carrier, and T-1003 and T-1008 require a 60.00 EUR credit on the invoice. Each result file records what was proposed.

In the local run, the 8B model missed the expedite action on T-1001. On T-1002 it proposed reshipping goods that are out of stock; the policy check blocked that action, and the case fails. None of the personal values in the sample data reached it, and in T-1007 it did not propose the credit the ticket tries to inject. Earlier runs of the same model under looser scoring passed 5/8 and 6/8 with different misses, so eight cases say little about a small model's accuracy.

### CI without an API key

Two GitHub Actions workflows:

- **CI** (every push and pull request): the offline tests and the web build. No model calls.
- **Live evals** (started by hand): the eight cases against Claude. GitHub has no API key stored. The job asks GitHub for a short-lived OIDC token, and the Anthropic SDK exchanges it for a 10-minute access token through workload identity federation. The federation rule accepts only `workflow_dispatch` runs on `main` of this repository, pinned to the numeric owner and repository IDs, and maps them to a service account that can only use one workspace. Passed 8/8 on 2026-11-23. Actions are pinned to commit SHAs, CI has read-only repository access, the identity token file is removed after the run, and the job uploads that run's report.

## Layout

```
support_agent/
  agent.py        agent loop, system prompt, trace
  providers.py    Claude and Ollama behind one interface
  erp_server.py   MCP server with read-only tools
  erp.py          in-memory mock ERP (reads, approved writes, audit log)
  proposal.py     proposal schema and policy checks
  redact.py       pseudonymisation of personal data
  retrieval.py    BM25 over runbooks
  api.py, cli.py  FastAPI app and command line
  data/           synthetic orders, tickets and runbooks
web/              React + TypeScript review UI
evals/            cases and live eval runner
tests/            offline tests with a scripted model
```

## Limits

- One ticket at a time, in memory; no database or authentication. The approver's name is whatever the caller sends, so run it locally only.
- Names and street addresses are not masked; only the formats listed above are.
- The policy rules and runbooks are examples, not a real company's.
- Small local models may be less reliable than Claude. The evals measure the difference; see Results.

## License

MIT
