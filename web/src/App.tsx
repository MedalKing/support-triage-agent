import { useEffect, useRef, useState } from "react";
import {
  approve, AuditEntry, auditLog, Config, getConfig, listTickets, recorded, reset, triage, Ticket, TraceStep, TriageResult,
} from "./api";

const PROVIDER_LABELS: Record<string, string> = {
  auto: "Claude Haiku, escalating to Opus when code sees trouble",
  claude: "Claude, one model",
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
  "T-1008": "bank details + second credit",
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
    text: "Approve the 60 € credit on T-1003, then open T-1008, a second email about the same invoice. Code blocks the second credit and asks for a manager, whatever the model says.",
  },
];
const TOUR_TICKETS = new Set(["T-1003", "T-1007", "T-1008"]);

// "claude:claude-haiku-5-5" -> "Claude Haiku 5.5"
function modelName(provider: string): string {
  const id = provider.split(":").pop() ?? provider;
  const m = id.match(/^claude-([a-z]+)-(\d+)-(\d+)$/);
  if (!m) return id;
  return `Claude ${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}`;
}

// The escalation step points at a recorded run that really escalated, preferring one where
// the stronger model changed the proposed actions and the reason is easy to follow, and not
// one of the tour's own tickets.
const score = (e: { reasons: string[]; changed: boolean }) =>
  2 * Number(e.changed) + Number(e.reasons.some((r) => r.includes("confidence")));

function escalationStep(config: Config | null): TourStep | null {
  const entries = Object.entries(config?.escalated ?? {})
    .filter(([id]) => !TOUR_TICKETS.has(id))
    .sort(([, a], [, b]) => score(b) - score(a));
  const [ticket, e] = entries[0] ?? [];
  if (!ticket) return null;
  return {
    ticket,
    title: "A cheap model first, a stronger one when code sees trouble",
    text: `Claude Haiku handles each ticket first. On this one, code saw that ${e.reasons.join(" and ")}, so Claude Opus redid the run${
      e.changed ? " and proposed different actions" : ""
    }. The trace shows both attempts.`,
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

  function open(id: string) {
    const t = tickets.find((x) => x.id === id);
    if (!t) return;
    current.current = t.id;
    setSelected(t);
    setResult(null);
    setChecked([]);
    setMessage(null);
    window.scrollTo(0, 0);
    if (config?.recorded.includes(t.id)) run(false, t);
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
      // Pre-tick only actions that change nothing for the customer and aren't applied yet.
      setChecked(
        r.proposal?.actions.flatMap((a, i) =>
          a.needs_approval || a.type === "no_action" || r.applied.includes(i) ? [] : [i],
        ) ?? [],
      );
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      if (current.current === ticketId) setBusy(false);
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
      setMessage(`Approved ${r.applied.length} action(s); see the audit log.`);
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      if (current.current === ticketId) setBusy(false);
    }
  }

  function goHome() {
    current.current = null;
    setSelected(null);
    setResult(null);
    setChecked([]);
    setMessage(null);
    setBusy(false);
  }

  async function startOver() {
    try {
      await reset();
      setAudit([]);
      goHome();
    } catch (e) {
      setMessage(`Could not start over: ${e}`);
    }
  }

  const blocked = (result?.policy_violations.length ?? 0) > 0;
  const actionable = !!result?.proposal?.actions.some((a) => a.type !== "no_action");
  const escalation = escalationStep(config);
  const tour = [...TOUR, ...(escalation ? [escalation] : [])];
  const creditApproved = !!result?.proposal?.actions.some((a, i) => a.type === "issue_credit" && result.applied.includes(i));
  // The next step of the tour, offered where the visitor is.
  const next =
    selected?.id === "T-1007" && result?.proposal
      ? { id: "T-1003", label: "Next: step 2, a credit and then a second one →" }
      : selected?.id === "T-1003" && creditApproved
      ? { id: "T-1008", label: "Next: open T-1008, the same refund asked for again →" }
      : selected?.id === "T-1008" && blocked && escalation
        ? { id: escalation.ticket, label: `Next: a run Claude Opus redid (${escalation.ticket}) →` }
        : null;

  // The first screen is a calm landing page; the ticket list appears once you are inside.
  if (!selected) {
    return (
      <div className="landing">
        <p className="eyebrow">Support triage agent · a demo</p>
        <h1>An AI agent proposes. A person decides.</h1>
        <p className="lead">
          It reads customer emails for a made-up packaging company, looks up the order and proposes what to do. It can't
          change anything itself: you approve each action, and rules in code block what's out of policy.
        </p>
        <button className="primary" onClick={() => open(tour[0].ticket)} disabled={!tickets.length}>
          Start the 3-minute tour →
        </button>
        <ol className="steps">
          {tour.map((step) => (
            <li key={step.ticket}>
              <button className="link" onClick={() => open(step.ticket)}>
                {step.title}
              </button>
            </li>
          ))}
        </ol>
        <p className="muted small">
          Recorded runs, instant and free. <button className="link small" onClick={() => open(tickets[0]?.id)}>Browse all 8
          emails</button>
        </p>
        {message && <p className="error">{message}</p>}
      </div>
    );
  }

  const tourIndex = tour.findIndex((s) => s.ticket === selected.id);

  return (
    <div className="layout">
      <aside>
        <h1>
          <button className="home" onClick={goHome}>
            Support triage agent
          </button>
        </h1>
        {tickets.map((t) => (
          <button key={t.id} className={selected.id === t.id ? "ticket active" : "ticket"} onClick={() => open(t.id)}>
            {t.subject}
            <span className="id">
              {t.id} · {SCENARIOS[t.id]}
              {config?.escalated[t.id] && " · escalated"}
            </span>
          </button>
        ))}

        {audit.length > 0 && (
          <section className="audit">
            <h2>Audit log</h2>
            {audit.map((e, i) => (
              <div key={i} className="small entry">
                <strong>{e.type}</strong> on {e.order_id}
                {e.amount_eur != null && <> · {e.amount_eur.toFixed(2)} €</>} <span className="muted">({e.ticket_id})</span>
              </div>
            ))}
            <p className="muted small">The order system is simulated: actions are logged, only address changes edit the order.</p>
            <button className="secondary" onClick={startOver} disabled={busy}>
              Start over
            </button>
          </section>
        )}
      </aside>

      <main>
        {selected && (
          <>
            <div className="tourbar">
              <button className="link" onClick={goHome}>
                ← Start
              </button>
              {tourIndex >= 0 && (
                <p>
                  <strong>
                    Tour · step {tourIndex + 1} of {tour.length}: {tour[tourIndex].title}.
                  </strong>{" "}
                  {tour[tourIndex].text}
                </p>
              )}
            </div>
            <section className="card">
              <h2>{selected.subject}</h2>
              <p className="muted">
                {selected.id} · customer {selected.customer_id} · {SCENARIOS[selected.id]}
              </p>
              <p className="ticket-body">{selected.body}</p>
              {busy && !result && <p className="muted">Loading…</p>}
              {!busy && !result && message && (
                <button onClick={() => run(false)}>Try again</button>
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
                  {result.recorded ? "Recorded run" : "Live run"} by {modelName(result.provider)}
                </p>
                {result.escalation && (
                  <div className="escalation">
                    Redone by {modelName(result.escalation.to)} after {modelName(result.escalation.from)}, because{" "}
                    {result.escalation.reasons.join(" and ")}.
                    <FirstAttempt result={result} /> The trace below shows both attempts.
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
                {actionable && (
                  <p className="muted small">
                    Nothing happens until you press Approve. Free requests, such as asking the carrier for news, are
                    ticked for you. Actions that cost money or change the order are left for you to tick, with what to
                    check.
                  </p>
                )}
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
                    {a.needs_approval && !blocked && !result.applied.includes(i) && (
                      <span className="tag warn">tick to approve</span>
                    )}
                    {result.applied.includes(i) && <span className="tag">applied</span>}
                    <div className="muted">Agent's reason: {a.reason}</div>
                    {a.approval_reason && <div className="why">What to check: {a.approval_reason}</div>}
                  </label>
                ))}
                {actionable && (
                  <button onClick={submit} disabled={blocked || busy || checked.length === 0}>
                    Approve selected
                  </button>
                )}
                {message && <p className="notice">{message}</p>}
                {next && (
                  <button className="next" onClick={() => open(next.id)}>
                    {next.label}
                  </button>
                )}

                <h3>Draft reply</h3>
                {blocked ? (
                  <details>
                    <summary className="muted">
                      Held back: a manager must decide on the blocked action before anyone replies. Show the draft
                    </summary>
                    <pre className="reply">{result.proposal.customer_reply}</pre>
                  </details>
                ) : (
                  <pre className="reply">{result.proposal.customer_reply}</pre>
                )}
                {result.redactions.length > 0 && (
                  <p className="muted small">
                    Hidden from the model: {result.redactions.join(", ")}. The model worked with these placeholders only;
                    the reply needs none of them.
                  </p>
                )}
              </section>
            )}

            {result && <Trace result={result} />}

            {message && !result?.proposal && <p className="muted">{message}</p>}
          </>
        )}
      </main>
    </div>
  );
}

// What the first model proposed before the run was redone, in one line.
function FirstAttempt({ result }: { result: TriageResult }) {
  const cut = result.trace.findIndex((s) => s.kind === "note" && s.name === "escalated");
  const first = result.trace
    .slice(0, cut)
    .find((s) => s.name === "submit_proposal")?.detail as
    | {
        actions: { type: string; order_id: string; amount_eur: number | null; new_address: string | null }[];
        confidence: string;
      }
    | undefined;
  if (!first) return <> {modelName(result.escalation!.from)} gave no usable proposal.</>;
  const actions = first.actions
    .map(
      (a) =>
        `${a.type} on ${a.order_id}${a.amount_eur != null ? ` (${a.amount_eur.toFixed(2)} €)` : ""}${
          a.new_address ? ` to ${a.new_address}` : ""
        }`,
    )
    .join(", ");
  return (
    <>
      {" "}
      {modelName(result.escalation!.from)} had proposed: {actions || "nothing"}, with confidence {first.confidence}.
    </>
  );
}

// The trace, split into attempts when a run was escalated.
function Trace({ result }: { result: TriageResult }) {
  const attempts: { title: string; steps: TraceStep[] }[] = [];
  let steps: TraceStep[] = [];
  let title = result.escalation ? `Attempt 1: ${modelName(result.escalation.from)}` : modelName(result.provider);
  for (const s of result.trace) {
    if (s.kind === "note" && s.name === "first_attempt") continue;
    if (s.kind === "note" && s.name === "escalated") {
      attempts.push({ title, steps });
      steps = [];
      title = `Attempt 2: ${modelName(result.provider)}`;
      continue;
    }
    steps.push(s);
  }
  attempts.push({ title, steps });
  const count = attempts.reduce((n, a) => n + a.steps.length, 0);
  return (
    <details className="card">
      <summary>
        What the agent did ({count} steps{result.escalation ? ", two attempts" : ""})
      </summary>
      {attempts.map((a, i) => (
        <section key={i} className="attempt">
          <h3>{a.title}</h3>
          {a.steps.map((s, j) => (
            <Step key={j} step={s} />
          ))}
        </section>
      ))}
    </details>
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
  if (isCall) {
    return (
      <details className="step">
        <summary>
          <span className="tag">lookup</span> <strong>{step.name}</strong>{" "}
          <span className="muted small">{JSON.stringify(d.arguments)}</span>
          {d.ok === false && <span className="tag warn">no data</span>}
        </summary>
        <pre>{pretty(d.result)}</pre>
      </details>
    );
  }
  const label = step.kind === "tool" ? "proposal" : step.kind;
  return (
    <details className="step" open={step.kind === "note"}>
      <summary>
        <span className="tag">{label}</span> <strong>{step.name}</strong>
      </summary>
      <pre>{pretty(d)}</pre>
    </details>
  );
}
