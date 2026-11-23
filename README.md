# Support triage agent

An LLM agent that investigates customer support tickets for a (fictional) packaging manufacturer and proposes what to do. A person reviews and approves every action.

It reads a ticket, looks up the order, shipment, stock and price list in a mock ERP through an **MCP server**, retrieves the matching **runbook**, and submits a **structured proposal**: likely cause, evidence, actions and a draft reply. **Personal data is pseudonymised** before the model sees it. **Policy runs in code**, so a prompt injection in a ticket can't talk the agent into an action. The same loop runs on **Claude** or a **local model through Ollama**.

All data is synthetic.

```
ticket ──► redact ──► agent loop ──► submit_proposal ──► policy check ──► human approval ──► ERP write + audit log
                        │   ▲
                        ▼   │   (read-only)
                    MCP server: get_order · list_customer_orders · get_shipment_events
                                get_stock · get_price_list · search_runbooks (BM25)
```

## Why it is built this way

| Concern | Design |
| --- | --- |
| The agent must not change things on its own | The MCP server exposes **read-only** tools. Writes exist only behind the approval endpoint (`support_agent/api.py`). |
| Prompt injection in customer text | The system prompt treats tickets as data, and `check_policy` in `support_agent/proposal.py` blocks credits above 25 % of the invoice, actions on another customer's order and actions on missing orders. The UI disables approval when a check fails, and so does the API. Ticket T-1007 tries an injection. |
| Personal data | `support_agent/redact.py` replaces emails, phone numbers, IBANs and Finnish personal identity codes with placeholders such as `[EMAIL_1]`, in the ticket **and** in ERP results. The mapping stays server-side; the reviewer sees the restored reply. A test checks that no personal value reaches the model. |
| Answers grounded in data | The proposal must list evidence from tool results. Its schema is enforced with strict tool use, and an invalid proposal goes back to the model to fix. |
| Not tied to one vendor | `support_agent/providers.py` hides Claude (Messages API) and Ollama (native `/api/chat`) behind one small interface. |
| Knowing whether it works | Offline tests run the whole loop with a scripted model. `evals/` scores live runs on deterministic checks (category, tools used, required and forbidden actions, policy, no personal data to the model). |

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
| 2026-11-23 | Claude Opus 5.5 (`claude-opus-5-5`, effort medium) | 8/8 | [evals/results/2026-11-23-1358-claude_claude-opus-5-5.json](evals/results/2026-11-23-1358-claude_claude-opus-5-5.json) |

Every case checks the category, the tools called, the required and forbidden actions, the policy check, and that no personal data from the ticket or the ERP reached the model. That includes the prompt-injection ticket (T-1007) and the ticket with an IBAN and a personal identity code (T-1008). Eight cases is a smoke test, not a benchmark. A local model through Ollama has not been run yet.

### CI without an API key

Two GitHub Actions workflows:

- **CI** (every push and pull request): the offline tests and the web build. No model calls.
- **Live evals** (started by hand): the eight cases against Claude. GitHub has no API key stored. The job asks GitHub for a short-lived OIDC token, and the Anthropic SDK exchanges it for a 10-minute access token through workload identity federation. The federation rule accepts only `workflow_dispatch` runs on `main` of this repository, pinned to the numeric owner and repository IDs, and maps them to a service account that can only use one workspace. Passed 8/8 on 2026-11-23 ([run](https://github.com/MedalKing/support-triage-agent/actions/runs/45184610279)).

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

- One ticket at a time, in memory; no database or authentication.
- The policy rules and runbooks are examples, not a real company's.
- Small local models follow the tool protocol less reliably than Claude; the evals show by how much.

## License

MIT
