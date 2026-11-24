import { useEffect, useRef, useState } from "react";
import { approve, listTickets, triage, Ticket, TriageResult } from "./api";

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [provider, setProvider] = useState("claude");
  const [result, setResult] = useState<TriageResult | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // The ticket on screen. A triage answer for a ticket the reviewer has left is dropped.
  const current = useRef<string | null>(null);

  useEffect(() => {
    listTickets().then(setTickets).catch((e) => setMessage(String(e)));
  }, []);

  function pick(t: Ticket) {
    current.current = t.id;
    setSelected(t);
    setResult(null);
    setChecked([]);
    setMessage(null);
  }

  async function run() {
    if (!selected) return;
    setBusy(true);
    setMessage(null);
    const ticketId = selected.id;
    try {
      const r = await triage(ticketId, provider);
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
              <div className="row">
                <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                  <option value="claude">Claude</option>
                  <option value="ollama">Local model (Ollama)</option>
                </select>
                <button onClick={run} disabled={busy}>
                  {busy ? "Investigating…" : "Run agent"}
                </button>
              </div>
            </section>

            {result?.error && <p className="error">{result.error}</p>}

            {result?.proposal && (
              <section className="card">
                <h2>
                  Proposal <span className="tag">{result.proposal.category}</span>{" "}
                  <span className="tag">confidence {result.proposal.confidence}</span>
                </h2>
                <p>{result.proposal.likely_cause}</p>
                <h3>Evidence</h3>
                <ul>
                  {result.proposal.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>

                <h3>Actions</h3>
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
                    <div className="muted">{a.reason}</div>
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
