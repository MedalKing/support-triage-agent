import { useEffect, useRef, useState } from "react";
import Evals from "./Evals";
import {
  approve, AuditEntry, auditLog, Config, decline, getConfig, listTickets, markDone, moveToInbox, recorded, reset, triage, Ticket,
  TraceStep, TriageResult,
} from "./api";

const PROVIDER_LABELS: Record<string, string> = {
  auto: "Auto (Haiku → Opus)",
  claude: "Claude (single)",
  ollama: "Ollama (local)",
};

const SCENARIOS: Record<string, string> = {
  "T-1001": "late delivery",
  "T-1002": "out of stock",
  "T-1003": "wrong price → credit",
  "T-1004": "damaged goods",
  "T-1005": "address change",
  "T-1006": "unknown order",
  "T-1007": "prompt injection",
  "T-1008": "IBAN + duplicate credit",
  "T-1009": "unsupported redirect",
  "T-1010": "compensation demand",
  "T-1011": "credit over the cap",
};

type TourStep = { ticket: string; title: string; also?: string[]; cue?: string };

const TOUR: TourStep[] = [
  { ticket: "T-1007", title: "Prompt injection" },
  {
    ticket: "T-1003",
    also: ["T-1008"],
    title: "Policy block",
    cue: "Approve the 60 € credit, then open T-1008.",
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

const score = (e: { reasons: string[]; changed: boolean }) =>
  2 * Number(e.changed) + Number(e.reasons.some((r) => r.includes("confidence")));

function escalationStep(config: Config | null): TourStep | null {
  const entries = Object.entries(config?.escalated ?? {})
    .filter(([id]) => !TOUR_TICKETS.has(id))
    .sort(([, a], [, b]) => score(b) - score(a));
  const [ticket] = entries[0] ?? [];
  if (!ticket) return null;
  return { ticket, title: "Model escalation" };
}

function confidenceClass(confidence: string): string {
  if (confidence === "high") return "tag ok";
  if (confidence === "medium") return "tag warn";
  return "tag declined";
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
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [view, setView] = useState<"inbox" | "archive">("inbox");
  const [query, setQuery] = useState("");
  const [touring, setTouring] = useState(false);
  const [mobileList, setMobileList] = useState(false);
  // The admin-only Evals page, opened from small links; it replaces the whole app while open.
  const [showEvals, setShowEvals] = useState(false);
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

  const refreshTickets = () => listTickets().then(setTickets).catch((e) => setMessage(String(e)));

  function saveCode(code: string) {
    setAccessCode(code);
    try {
      localStorage.setItem("accessCode", code);
    } catch {
      /* private window */
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
    setDeclining(false);
    setReason("");
    setMobileList(false);
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
      if (live) refreshTickets();
      if (current.current !== ticketId) return;
      setResult(r);
      setChecked(
        r.proposal?.actions.flatMap((a, i) =>
          a.needs_approval || a.type === "no_action" || r.applied.includes(i) || String(i) in r.declined ? [] : [i],
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
    setMessage(null);
    try {
      await approve(ticketId, result.proposal_id, checked);
      await Promise.all([auditLog().then(setAudit), refreshTickets()]);
      if (current.current !== ticketId) return;
      setResult({ ...result, applied: [...result.applied, ...checked] });
      setChecked([]);
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      if (current.current === ticketId) setBusy(false);
    }
  }

  async function submitDecline() {
    if (!selected || !result || !reason.trim()) return;
    const ticketId = selected.id;
    setBusy(true);
    setMessage(null);
    try {
      await decline(ticketId, result.proposal_id, checked, reason.trim());
      await Promise.all([auditLog().then(setAudit), refreshTickets()]);
      if (current.current !== ticketId) return;
      const declined = { ...result.declined };
      for (const i of checked) declined[String(i)] = reason.trim();
      setResult({ ...result, declined });
      setChecked([]);
      setDeclining(false);
      setReason("");
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      if (current.current === ticketId) setBusy(false);
    }
  }

  function startTour(id: string) {
    setTouring(true);
    open(id);
  }

  function skipTour() {
    setTouring(false);
    setView("inbox");
    open((inbox[0] ?? tickets[0]).id);
    setMobileList(true);
  }

  async function setDone(done: boolean) {
    if (!selected || busy) return;
    const ticketId = selected.id;
    setBusy(true);
    try {
      await (done ? markDone(ticketId) : moveToInbox(ticketId));
      await refreshTickets();
      if (current.current === ticketId) setMessage(null);
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
    if (changed && !window.confirm("Reset demo? Clears approvals, declines, and archive.")) return;
    try {
      await reset();
      setAudit([]);
      await refreshTickets();
      setView("inbox");
      setQuery("");
      setTouring(false);
      goHome();
    } catch (e) {
      setMessage(`Reset failed: ${e}`);
    }
  }

  const inbox = tickets.filter((t) => !t.archived);
  const archive = tickets.filter((t) => t.archived);
  const changed = audit.length > 0 || archive.length > 0;
  const ready = tickets.length > 0 && config !== null;
  const q = query.trim().toLowerCase();
  const shown = (view === "inbox" ? inbox : archive).filter(
    (t) => !q || [t.id, t.subject, t.body, t.customer_name, SCENARIOS[t.id] ?? ""].some((f) => f.toLowerCase().includes(q)),
  );
  const isArchived = !!selected && archive.some((t) => t.id === selected.id);
  const at = tickets.findIndex((t) => t.id === selected?.id);
  const nextInInbox = [...tickets.slice(at + 1), ...tickets.slice(0, Math.max(at, 0))].find((t) => !t.archived);
  const undecided = result?.proposal?.actions.filter(
    (a, i) => a.type !== "no_action" && !result.applied.includes(i) && !(String(i) in result.declined),
  ).length ?? 0;

  const outcome = (id: string) => {
    const mine = audit.filter((e) => e.ticket_id === id);
    const a = mine.filter((e) => e.decision === "approved").length;
    const d = mine.filter((e) => e.decision === "declined").length;
    const parts = [a && `✓${a}`, d && `✕${d}`].filter(Boolean);
    return parts.length ? parts.join(" ") : "done";
  };

  const blocked = (result?.policy_violations.length ?? 0) > 0;
  const actionable = !!result?.proposal?.actions.some((a) => a.type !== "no_action");
  const escalation = escalationStep(config);
  const tour = [...TOUR, ...(escalation ? [escalation] : [])];
  const creditApproved = !!result?.proposal?.actions.some((a, i) => a.type === "issue_credit" && result.applied.includes(i));
  const next = !touring
    ? null
    : selected?.id === "T-1007" && result?.proposal
      ? { id: "T-1003", label: "Next: T-1003 →" }
      : selected?.id === "T-1003" && result?.proposal
        ? creditApproved
          ? { id: "T-1008", label: "Next: T-1008 →" }
          : { id: "", label: "Approve credit to continue" }
      : selected?.id === "T-1008" && blocked && escalation
        ? { id: escalation.ticket, label: `Next: ${escalation.ticket} →` }
        : null;

  if (showEvals) {
    return <Evals onBack={() => setShowEvals(false)} />;
  }

  if (!selected) {
    return (
      <div className="landing">
        <p className="eyebrow">
          Support triage agent <span className="tag">demo</span>
        </p>
        <h1>An AI agent proposes. A person decides.</h1>
        <p className="lead">
          Read-only lookups, policy enforced in code, human approval on every action.
        </p>
        <div className="row start">
          <button className="primary" onClick={() => startTour(tour[0].ticket)} disabled={!ready}>
            Start tour →
          </button>
          <button className="secondary big" onClick={skipTour} disabled={!ready}>
            Inbox ({inbox.length || 9})
          </button>
        </div>
        <ol className="steps">
          {tour.map((step) => (
            <li key={step.ticket}>
              <button className="link" onClick={() => startTour(step.ticket)} disabled={!ready}>
                {step.title}{" "}
                <span className="muted small">
                  ({step.also?.length ? `${step.ticket} → ${step.also[0]}` : step.ticket})
                </span>
              </button>
            </li>
          ))}
        </ol>
        <p className="admin-link">
          <button className="link small" onClick={() => setShowEvals(true)}>
            Evals
          </button>{" "}
          <span className="tag admin">admin</span>
        </p>
        {changed && (
          <p className="reset-inline">
            <button className="link small" onClick={startOver}>Reset demo</button>
          </p>
        )}
        {message && <p className="error">{message}</p>}
      </div>
    );
  }

  const tourIndex = touring ? tour.findIndex((s) => s.ticket === selected.id || s.also?.includes(selected.id)) : -1;
  const activeStep = tourIndex >= 0 ? tour[tourIndex] : null;
  const tourCue =
    !touring
      ? null
      : selected.id === "T-1003" && creditApproved
        ? "Credit approved — open T-1008."
        : selected.id === "T-1008"
          ? null
          : activeStep?.cue;

  return (
    <div className={`layout ${mobileList ? "mobile-list" : "mobile-detail"}`}>
      <aside>
        <h1>
          <button className="home" onClick={goHome} title="Start page">
            Support triage agent
          </button>
        </h1>
        <label className="search">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-4-4" />
          </svg>
          <input
            type="search"
            placeholder="Search tickets"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={view === "inbox" ? "Search inbox" : "Search archive"}
          />
        </label>
        {view === "archive" && (
          <div className="listhead">
            <button className="link small" onClick={() => setView("inbox")}>‹ Inbox</button>
            <span>Archive</span>
          </div>
        )}

        <div className="list">
          {shown.map((t) => (
            <button key={t.id} className={selected.id === t.id ? "ticket active" : "ticket"} onClick={() => open(t.id)}>
              <span className="meta">
                <Badge id={t.customer_id} name={t.customer_name} />
                <span className="who">{t.customer_name}</span>
                <span className="when">{when(t.received)}</span>
              </span>
              <span className="subject">{t.subject}</span>
              <span className="meta">
                <span className="who">
                  {t.id} · {view === "inbox" ? SCENARIOS[t.id] : outcome(t.id)}
                </span>
                {view === "inbox" && config?.escalated[t.id] && (
                  <span className="badge-up" title="Escalated to stronger model">
                    ↑
                  </span>
                )}
              </span>
            </button>
          ))}
          {query && shown.length === 0 && <p className="empty muted small">No matches.</p>}
          {!query && view === "inbox" && inbox.length === 0 && (
            <p className="empty muted small">
              <strong>Inbox zero</strong>
            </p>
          )}
          {!query && view === "archive" && archive.length === 0 && (
            <p className="empty muted small">Archive empty.</p>
          )}

          {view === "archive" && audit.length > 0 && (
            <section className="audit">
              <h2>
                Audit log <span className="tag">simulated</span>
              </h2>
              {audit.map((e, i) => (
                <div key={i} className="small entry">
                  <span className={e.decision === "declined" ? "mark err" : "mark ok"} aria-label={e.decision}>
                    {e.decision === "declined" ? "✕" : "✓"}
                  </span>{" "}
                  <code className="action-type">{e.type}</code> · {e.order_id}
                  {e.amount_eur != null && <> · {e.amount_eur.toFixed(2)}&nbsp;€</>} <span className="muted">({e.ticket_id})</span>
                  {e.decline_reason && <div className="muted">“{e.decline_reason}”</div>}
                </div>
              ))}
            </section>
          )}
        </div>

        {view === "inbox" && (
          <button className="archive-link" onClick={() => setView("archive")}>
            <span>Archive</span>
            <span className="muted">{archive.length} ›</span>
          </button>
        )}
        <div className="side-footer">
          <button className="link small reset" onClick={startOver} disabled={busy}>
            Reset
          </button>
          <button className="link small reset" onClick={() => setShowEvals(true)} title="Admin and demo only">
            Evals <span className="tag admin">admin</span>
          </button>
        </div>
      </aside>

      <main>
        <div className="mobile-nav">
          <button
            className="link"
            onClick={() => {
              setMobileList(true);
              window.scrollTo(0, 0);
            }}
          >
            ‹ {view === "inbox" ? `Inbox (${inbox.length})` : `Archive (${archive.length})`}
          </button>
          <button className="link small muted" onClick={goHome}>
            Home
          </button>
        </div>
        {touring && (
          <div className="tourbar">
            <div className="stepper">
              {tour.map((step, i) => (
                <button
                  key={step.ticket}
                  className={i === tourIndex ? "step-pill active" : "step-pill"}
                  onClick={() => open(step.ticket)}
                >
                  <span className="step-num">{i + 1}</span>
                  {step.title}
                </button>
              ))}
              <button className="link small end-tour" onClick={() => setTouring(false)} title="End tour" aria-label="End tour">
                ✕
              </button>
            </div>
            {tourCue && <p className="cue">{tourCue}</p>}
          </div>
        )}

        <section className="card">
          <h2>{selected.subject}</h2>
          <p className="muted">
            {selected.id} · {selected.customer_id} · {SCENARIOS[selected.id]}
          </p>
          <p className="ticket-body">{selected.body}</p>
          {busy && !result && <p className="muted">Loading…</p>}
          {!busy && !result && message && <button onClick={() => run(false)}>Retry</button>}
          <details className="live">
            <summary>Live run</summary>
            <div className="row">
              <select value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="Model provider">
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
                  aria-label="Access code"
                />
              )}
              <button onClick={() => run(true)} disabled={busy}>
                {busy ? "Running…" : "Run"}
              </button>
            </div>
          </details>
        </section>

        {result?.error && <p className="error">{result.error}</p>}

        {result?.proposal && (
          <section className="card">
            <div className="card-head">
              <h2>
                Proposal <span className="tag">{result.proposal.category}</span>
                <span className={confidenceClass(result.proposal.confidence)} title="Model confidence">
                  {result.proposal.confidence}
                </span>
              </h2>
              <div className="meta-pills">
                <span className={result.recorded ? "tag" : "tag ok"}>{result.recorded ? "recorded" : "live"}</span>
                <span className="muted small">{modelName(result.provider)}</span>
              </div>
            </div>

            {result.escalation && (
              <div className="escalation">
                <div className="esc-head">
                  <strong>
                    {modelName(result.escalation.from)} → {modelName(result.escalation.to)}
                  </strong>
                  <span className="esc-reason">{result.escalation.reasons.join(" · ")}</span>
                </div>
                <FirstAttempt result={result} />
              </div>
            )}

            <p>{result.proposal.likely_cause}</p>

            <h3>Evidence</h3>
            <ul className="evidence">
              {result.proposal.evidence.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>

            <h3>Actions</h3>
            {blocked && (
              <div className="error">
                <strong>Policy block</strong>
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
                    disabled={busy || result.applied.includes(i) || String(i) in result.declined}
                    checked={checked.includes(i) || result.applied.includes(i)}
                    onChange={(e) => setChecked(e.target.checked ? [...checked, i] : checked.filter((x) => x !== i))}
                  />
                )}
                <code className="action-type">{a.type}</code> · {a.order_id}
                {a.amount_eur != null && <> · {a.amount_eur.toFixed(2)}&nbsp;€</>}
                {a.new_address && <> · {a.new_address}</>}
                {a.needs_approval && !blocked && !result.applied.includes(i) && !(String(i) in result.declined) && (
                  <>
                    {" "}
                    <span className="tag warn">approval required</span>
                  </>
                )}
                {result.applied.includes(i) && (
                  <>
                    {" "}
                    <span className="tag ok">applied</span>
                  </>
                )}
                {String(i) in result.declined && (
                  <>
                    {" "}
                    <span className="tag declined">declined</span>
                    <div className="muted">“{result.declined[String(i)]}”</div>
                  </>
                )}
                <div className="muted">{a.reason}</div>
                {a.approval_reason && <div className="why">{a.approval_reason}</div>}
              </label>
            ))}

            {isArchived ? (
              <div className="done">
                <div className="row">
                  <span className="tag ok">archived</span>
                  {nextInInbox && !next && (
                    <button onClick={() => open(nextInInbox.id)}>Next: {nextInInbox.id} →</button>
                  )}
                  <button className="secondary" onClick={() => setDone(false)}>Move to inbox</button>
                </div>
              </div>
            ) : undecided === 0 ? (
              <div className="row actions-bar">
                <button onClick={() => setDone(true)} disabled={busy}>Mark done</button>
              </div>
            ) : null}

            {actionable && !declining && undecided > 0 && (
              <div className="row actions-bar">
                <button onClick={submit} disabled={blocked || busy || checked.length === 0}>
                  Approve
                </button>
                <button className="secondary" onClick={() => setDeclining(true)} disabled={busy || checked.length === 0}>
                  Decline…
                </button>
              </div>
            )}

            {declining && (
              <div className="decline">
                <textarea
                  id="decline-reason"
                  aria-label="Reason for declining"
                  value={reason}
                  maxLength={500}
                  rows={2}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Reason for declining…"
                />
                <div className="row">
                  <button onClick={submitDecline} disabled={busy || !reason.trim() || checked.length === 0}>
                    Decline
                  </button>
                  <button className="secondary" onClick={() => setDeclining(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {message && <p className="error">{message}</p>}

            {next && (
              <button className="next" onClick={() => next.id && open(next.id)} disabled={!next.id}>
                {next.label}
              </button>
            )}

            <h3>Draft reply</h3>
            {blocked ? (
              <details>
                <summary className="muted">Held (policy block) · Show draft</summary>
                <pre className="reply">{result.proposal.customer_reply}</pre>
              </details>
            ) : (
              <pre className="reply">{result.proposal.customer_reply}</pre>
            )}

            {result.redactions.length > 0 && (
              <div className="redactions">
                <span className="muted small">Redacted</span>
                {result.redactions.map((r) => (
                  <code key={r} className="chip">
                    {r}
                  </code>
                ))}
              </div>
            )}
          </section>
        )}

        {result && <Trace result={result} />}

        {message && !result?.proposal && <p className="error">{message}</p>}
      </main>
    </div>
  );
}

// The demo's "today" is Monday 2026-11-23: today's emails show the time, older ones the weekday.
const DEMO_TODAY = "2026-11-23";
function when(received: string): string {
  const [day, time] = received.split("T");
  if (day === DEMO_TODAY) return time;
  return new Date(`${day}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short" });
}

function Badge({ id, name }: { id: string; name: string }) {
  const words = name.split(/\s+/).filter((w) => !/^(ltd|oy|ab|oyj|inc)$/i.test(w));
  const initials = words.slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const hue = [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return (
    <span className="badge" style={{ background: `hsl(${hue} 45% 42%)` }} aria-hidden="true">
      {initials}
    </span>
  );
}

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
  if (!first) return <div className="muted small">Attempt 1: no valid proposal</div>;
  const actions = first.actions
    .map(
      (a) =>
        `${a.type} ${a.order_id}${a.amount_eur != null ? ` (${a.amount_eur.toFixed(2)} €)` : ""}${
          a.new_address ? ` → ${a.new_address}` : ""
        }`,
    )
    .join(", ");
  return (
    <div className="muted small">
      Attempt 1: {actions || "none"} · {first.confidence}
    </div>
  );
}

function Trace({ result }: { result: TriageResult }) {
  const attempts: { title: string; steps: TraceStep[] }[] = [];
  let steps: TraceStep[] = [];
  let title = result.escalation ? `Attempt 1 · ${modelName(result.escalation.from)}` : modelName(result.provider);
  for (const s of result.trace) {
    if (s.kind === "note" && s.name === "first_attempt") continue;
    if (s.kind === "note" && s.name === "escalated") {
      attempts.push({ title, steps });
      steps = [];
      title = `Attempt 2 · ${modelName(result.provider)}`;
      continue;
    }
    steps.push(s);
  }
  attempts.push({ title, steps });
  const count = attempts.reduce((n, a) => n + a.steps.length, 0);
  return (
    <details className="card">
      <summary className="trace-summary">
        Trace <span className="tag">{count} steps</span>
        {result.escalation && <span className="tag warn">2 attempts</span>}
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
