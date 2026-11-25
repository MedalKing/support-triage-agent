import { useEffect, useRef, useState } from "react";
import {
  approve, AuditEntry, auditLog, Config, getConfig, listTickets, recorded, reset, triage, Ticket, TraceStep, TriageResult,
} from "./api";

const PROVIDER_LABELS: Record<string, string> = {
  auto: "Claude Haiku, escalating to Opus when unclear",
  claude: "Claude Opus only",
  ollama: "Local model (Ollama)",
};

// What each sample ticket shows off. Shown next to the ticket in the list.
const SCENARIOS: Record<string, string> = {
  "T-1001": "late delivery",
  "T-1002": "out of stock",
  "T-1003": "wrong price → credit",
  "T-1004": "damaged goods",
  "T-1005": "address change",
  "T-1006": "order that doesn't exist",
  "T-1007": "prompt injection",
  "T-1008": "bank details + credit cap",
};

type TourStep = { ticket: string; title: string; text: string };

const TOUR: TourStep[] = [
  {
    ticket: "T-1007",
    title: "A ticket that tries to give orders",
    text: "The customer's message tells the AI to approve a 100 % credit. See what the agent proposes instead, and that it says why.",
  },
  {
    ticket: "T-1003",
    title: "Policy runs in code, not in the prompt",
    text: "Approve the 60 € credit on T-1003. Then open T-1008, a second ticket about the same invoice: code blocks a second credit, whatever the model says.",
  },
];

// The escalation step points at whichever recorded run really escalated.
function escalationStep(config: Config | null): TourStep | null {
  // A low-confidence escalation is the easiest one to follow, so it goes first.
  const entries = Object.entries(config?.escalated ?? {}).sort(
    ([, a], [, b]) => Number(!a.join().includes("confidence")) - Number(!b.join().includes("confidence")),
  );
  const [ticket, reasons] = entries[0] ?? [];
  if (!ticket) return null;
  return {
    ticket,
    title: "A cheap model first, a strong one when it's unsure",
    text: `Claude Haiku handles each ticket first. On this one Claude Opus redid the run, because ${reasons.join(
      "; ",
    )}. The first attempt is in the trace.`,
  };
}

function loadCode(): string {
  try {
    return localStorage.getItem("accessCode") ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [provider, setProvider] = useState("auto");
  const [accessCode, setAccessCode] = useState(loadCode);
  const [result, setResult] = useState<TriageResult | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  // The ticket on screen. A triage answer for a ticket the reviewer has left is dropped.
  const current = useRef<string | null>(null);

  useEffect(() => {
    listTickets().then(setTickets).catch((e) => setMessage(String(e)));
    getConfig()
      .then((c) => {
        setConfig(c);
        if (c.providers.length) setProvider(c.providers[0]);
      })
      .catch((e) => setMessage(String(e)));
    auditLog().then(setAudit).catch(() => {});
  }, []);

  function saveCode(code: string) {
    setAccessCode(code);
    try {
      localStorage.setItem("accessCode", code);
    } catch {
      /* private window: the code is kept for this page only */
    }
  }

  function pick(t: Ticket, showRecorded = false) {
    current.current = t.id;
    setSelected(t);
    setResult(null);
    setChecked([]);
    setMessage(null);
    if (showRecorded) run(false, t);
  }

  async function run(live: boolean, ticket: Ticket | null = selected) {
    if (!ticket) return;
    setBusy(true);
    setMessage(null);
    const ticketId = ticket.id;
    try {
      const r = live ? await triage(ticketId, provider, accessCode) : await recorded(ticketId);
      if (current.current !== ticketId) return;
      setResult(r);
      // Pre-tick only actions that change nothing for the customer; never no_action.
      setChecked(r.proposal?.actions.flatMap((a, i) => (a.needs_approval || a.type === "no_action" ? [] : [i])) ?? []);
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!selected || !result) return;
    const ticketId = selected.id;
    setBusy(true);
    try {
      const r = await approve(ticketId, result.proposal_id, checked);
      setAudit(await auditLog());
      if (current.current !== ticketId) return;
      setResult({ ...result, applied: [...result.applied, ...checked] });
      setChecked([]);
      const credit = r.applied.some((a) => a.type === "issue_credit");
      setMessage(
        `Approved ${r.applied.length} action(s); see the audit log.` +
          (credit && ticketId === "T-1003" ? " Now open T-1008: it asks for the same refund on the same invoice." : ""),
      );
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  function goHome() {
    current.current = null;
    setSelected(null);
    setResult(null);
    setChecked([]);
    setMessage(null);
  }

  async function startOver() {
    await reset();
    setAudit([]);
    goHome();
  }

  const blocked = (result?.policy_violations.length ?? 0) > 0;
  const hasRecording = !!selected && !!config?.recorded.includes(selected.id);
  const byId = (id: string) => tickets.find((t) => t.id === id);

  return (
    <div className="layout">
      <aside>
        <h1>
          <button className="home" onClick={goHome}>
            Support triage agent
          </button>
        </h1>
        <p className="muted small">Sample tickets to a made-up packaging company.</p>
        {tickets.map((t) => (
          <button key={t.id} className={selected?.id === t.id ? "ticket active" : "ticket"} onClick={() => pick(t, true)}>
            <span className="id">
              {t.id}{" "}
              <span className="scenario">
                {SCENARIOS[t.id]}
                {config?.escalated[t.id] && " · escalated"}
              </span>
            </span>
            {t.subject}
          </button>
        ))}

        <section className="audit">
          <h2>Audit log</h2>
          {audit.length === 0 && <p className="muted small">Nothing approved yet. Approved actions appear here.</p>}
          {audit.map((e, i) => (
            <div key={i} className="small entry">
              <strong>{e.type}</strong> on {e.order_id}
              {e.amount_eur != null && <> · {e.amount_eur.toFixed(2)} €</>} <span className="muted">({e.ticket_id})</span>
            </div>
          ))}
          {audit.length > 0 && (
            <p className="muted small">The order system is simulated: actions are logged, only address changes edit the order.</p>
          )}
          <button className="secondary" onClick={startOver}>
            Start over
          </button>
        </section>
      </aside>

      <main>
        {!selected && (
          <>
            <section className="card intro">
              <h2>An AI agent proposes; a person decides</h2>
              <p>
                Each ticket is a customer email. The agent looks up the order, the shipment and the internal instructions,
                then proposes what happened, what to do and a reply. It can read but not change anything: you approve each
                action, and rules in code block what is out of policy.
              </p>
              <ol className="flow">
                <li>Personal data masked</li>
                <li>Read-only lookups for this customer</li>
                <li>Structured proposal</li>
                <li>Policy check in code</li>
                <li>You approve</li>
              </ol>
            </section>
            <h3>Try this (3 minutes)</h3>
            {[...TOUR, escalationStep(config)].filter((s): s is TourStep => !!s).map((step, i) => (
              <button key={step.ticket} className="card tour" onClick={() => byId(step.ticket) && pick(byId(step.ticket)!, true)}>
                <span className="step-no">{i + 1}</span>
                <strong>
                  {step.title} <span className="muted">· {step.ticket}</span>
                </strong>
                <span className="muted">{step.text}</span>
              </button>
            ))}
            <p className="muted small">
              Each ticket opens a recorded run of the agent, so it is instant and free. With the access code you can also
              run it live.
            </p>
          </>
        )}
        {selected && (
          <>
            <section className="card">
              <h2>{selected.subject}</h2>
              <p className="muted">
                {selected.id} · customer {selected.customer_id} · {SCENARIOS[selected.id]}
              </p>
              <p className="ticket-body">{selected.body}</p>
              {hasRecording && !result && (
                <button onClick={() => run(false)} disabled={busy}>
                  Show the agent's run
                </button>
              )}
              <details className="live">
                <summary>Run it live instead</summary>
                <div className="row">
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {(config?.providers ?? ["auto"]).map((p) => (
                      <option key={p} value={p}>
                        {PROVIDER_LABELS[p] ?? p}
                      </option>
                    ))}
                  </select>
                  {config?.access_code_required && (
                    <input
                      type="password"
                      placeholder="Access code"
                      value={accessCode}
                      onChange={(e) => saveCode(e.target.value)}
                      aria-label="Access code for live runs"
                    />
                  )}
                  <button onClick={() => run(true)} disabled={busy}>
                    {busy ? "Investigating…" : "Run live"}
                  </button>
                </div>
                <p className="muted small">
                  Calls the model now; takes 10–60 seconds.
                  {config?.access_code_required && " Needs the access code from my job application."}
                </p>
              </details>
            </section>

            {result?.error && <p className="error">{result.error}</p>}

            {result?.proposal && (
              <section className="card">
                <h2>
                  Proposal <span className="tag">{result.proposal.category}</span>{" "}
                  <span className="tag" title="The model's own estimate">
                    model's confidence: {result.proposal.confidence}
                  </span>
                </h2>
                <p className="muted">
                  {result.recorded ? "Recorded run" : "Live run"} by {result.provider}
                </p>
                {result.escalation && (
                  <div className="escalation">
                    Escalated from {result.escalation.from} to {result.escalation.to}, because{" "}
                    {result.escalation.reasons.join("; ")}. The first attempt is in the trace below.
                  </div>
                )}
                <p>{result.proposal.likely_cause}</p>
                <h3>Evidence</h3>
                <ul>
                  {result.proposal.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>

                <h3>Actions</h3>
                <p className="muted small">
                  Nothing happens until you press Approve. Free requests, such as asking the carrier for news, are ticked
                  for you. Actions that cost money or change the order are left for you to tick, with what to check.
                </p>
                {blocked && (
                  <div className="error">
                    Blocked by policy (checked in code, not by the model):
                    <ul>
                      {result.policy_violations.map((v, i) => (
                        <li key={i}>{v}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {result.proposal.actions.map((a, i) => (
                  <label key={i} className="action">
                    {a.type !== "no_action" && (
                      <input
                        type="checkbox"
                        disabled={blocked || busy || result.applied.includes(i)}
                        checked={checked.includes(i) || result.applied.includes(i)}
                        onChange={(e) => setChecked(e.target.checked ? [...checked, i] : checked.filter((x) => x !== i))}
                      />
                    )}
                    <strong>{a.type}</strong> on {a.order_id}
                    {a.amount_eur != null && <> · {a.amount_eur.toFixed(2)} €</>}
                    {a.new_address && <> · to {a.new_address}</>}
                    {a.needs_approval && !blocked && !result.applied.includes(i) && <span className="tag warn">tick to approve</span>}
                    {result.applied.includes(i) && <span className="tag">applied</span>}
                    <div className="muted">Agent's reason: {a.reason}</div>
                    {a.approval_reason && <div className="why">What to check: {a.approval_reason}</div>}
                  </label>
                ))}
                <button onClick={submit} disabled={blocked || busy || checked.length === 0}>
                  Approve selected
                </button>
                {message && <p className="notice">{message}</p>}

                <h3>Draft reply</h3>
                <pre className="reply">{result.proposal.customer_reply}</pre>
                {result.redactions.length > 0 && (
                  <p className="muted small">
                    Hidden from the model: {result.redactions.join(", ")}. The model worked with these placeholders only;
                    the reply needs none of them.
                  </p>
                )}
              </section>
            )}

            {result && (
              <details className="card">
                <summary>
                  What the agent did ({result.trace.length} steps, {result.provider})
                </summary>
                {result.trace.map((s, i) => (
                  <Step key={i} step={s} />
                ))}
              </details>
            )}

            {message && !result?.proposal && <p className="muted">{message}</p>}
          </>
        )}
      </main>
    </div>
  );
}

function pretty(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function Step({ step }: { step: TraceStep }) {
  const d = step.detail as { arguments?: unknown; result?: unknown; ok?: boolean } | string;
  const isCall = step.kind === "tool" && typeof d === "object" && d !== null && "result" in d;
  return (
    <div className="step">
      <span className="tag">{step.kind}</span> <strong>{step.name}</strong>
      {isCall ? (
        <>
          <span className="muted small"> {JSON.stringify(d.arguments)}</span>
          {d.ok === false && <span className="tag warn">no data</span>}
          <pre>{pretty(d.result)}</pre>
        </>
      ) : (
        <pre>{pretty(d)}</pre>
      )}
    </div>
  );
}
