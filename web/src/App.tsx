import { useEffect, useRef, useState } from "react";
import { approve, Config, getConfig, listTickets, recorded, triage, Ticket, TriageResult } from "./api";

const PROVIDER_LABELS: Record<string, string> = {
  auto: "Claude Haiku, escalating to Opus when unclear",
  claude: "Claude Opus only",
  ollama: "Local model (Ollama)",
};

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
  }, []);

  function saveCode(code: string) {
    setAccessCode(code);
    try {
      localStorage.setItem("accessCode", code);
    } catch {
      /* private window: the code is kept for this page only */
    }
  }

  function pick(t: Ticket) {
    current.current = t.id;
    setSelected(t);
    setResult(null);
    setChecked([]);
    setMessage(null);
  }

  async function run(live: boolean) {
    if (!selected) return;
    setBusy(true);
    setMessage(null);
    const ticketId = selected.id;
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
      if (current.current !== ticketId) return;
      setResult({ ...result, applied: [...result.applied, ...checked] });
      setChecked([]);
      setMessage(
        `Approved ${r.applied.length} action(s), recorded in the audit log (/api/audit). ` +
          "This demo only simulates the ERP: address changes update the mock order, other actions are logged.",
      );
    } catch (e) {
      if (current.current === ticketId) setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  const blocked = (result?.policy_violations.length ?? 0) > 0;
  const hasRecording = !!selected && !!config?.recorded.includes(selected.id);

  return (
    <div className="layout">
      <aside>
        <h1>Tickets</h1>
        {tickets.map((t) => (
          <button key={t.id} className={selected?.id === t.id ? "ticket active" : "ticket"} onClick={() => pick(t)}>
            <span className="id">{t.id}</span>
            {t.subject}
          </button>
        ))}
      </aside>

      <main>
        {!selected && <p className="muted">Pick a ticket. The agent proposes; you decide.</p>}
        {selected && (
          <>
            <section className="card">
              <h2>{selected.subject}</h2>
              <p className="muted">
                {selected.id} · customer {selected.customer_id}
              </p>
              <p>{selected.body}</p>
              {hasRecording && (
                <div className="row">
                  <button onClick={() => run(false)} disabled={busy}>
                    Show recorded run
                  </button>
                  <span className="muted">Instant: a saved run of the agent on this ticket.</span>
                </div>
              )}
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
              {config?.access_code_required && (
                <p className="muted">Live runs call the model and take 20–60 seconds. They need the access code from the application.</p>
              )}
            </section>

            {result?.error && <p className="error">{result.error}</p>}

            {result?.proposal && (
              <section className="card">
                <h2>
                  Proposal <span className="tag">{result.proposal.category}</span>{" "}
                  <span className="tag">confidence {result.proposal.confidence}</span>
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
                <p className="muted">
                  Nothing happens until you press Approve selected. Free requests, such as asking the carrier for news,
                  are ticked for you. Actions that cost money or change the order are marked “needs approval” and left
                  for you to tick; each one says what to check.
                </p>
                {blocked && (
                  <div className="error">
                    Blocked by policy:
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
                    {a.amount_eur != null && <> · {a.amount_eur.toFixed(2)} EUR</>}
                    {a.new_address && <> · to {a.new_address}</>}
                    {a.needs_approval && <span className="tag warn">needs approval</span>}
                    {result.applied.includes(i) && <span className="tag">applied</span>}
                    <div className="muted">Agent's reason: {a.reason}</div>
                    {a.approval_reason && <div className="why">Why it needs approval: {a.approval_reason}</div>}
                  </label>
                ))}
                <button onClick={submit} disabled={blocked || busy || checked.length === 0}>
                  Approve selected
                </button>

                <h3>Draft reply</h3>
                <pre className="reply">{result.proposal.customer_reply}</pre>
                {result.redactions.length > 0 && (
                  <p className="muted">
                    Hidden from the model: {result.redactions.join(", ")}. Contact details are restored here for
                    you; bank accounts and identity codes stay masked.
                  </p>
                )}
              </section>
            )}

            {result && (
              <details className="card">
                <summary>
                  Agent trace ({result.trace.length} steps, {result.provider})
                </summary>
                {result.trace.map((s, i) => (
                  <div key={i} className="step">
                    <span className="tag">{s.kind}</span> <strong>{s.name}</strong>
                    <pre>{typeof s.detail === "string" ? s.detail : JSON.stringify(s.detail, null, 2)}</pre>
                  </div>
                ))}
              </details>
            )}

            {message && <p className="muted">{message}</p>}
          </>
        )}
      </main>
    </div>
  );
}
