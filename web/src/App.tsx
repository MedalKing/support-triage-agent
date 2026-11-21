import { useEffect, useState } from "react";
import { approve, listTickets, triage, Ticket, TriageResult } from "./api";

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [provider, setProvider] = useState("claude");
  const [result, setResult] = useState<TriageResult | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    listTickets().then(setTickets).catch((e) => setMessage(String(e)));
  }, []);

  function pick(t: Ticket) {
    setSelected(t);
    setResult(null);
    setChecked([]);
    setMessage(null);
  }

  async function run() {
    if (!selected) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await triage(selected.id, provider);
      setResult(r);
      setChecked(r.proposal?.actions.flatMap((a, i) => (a.needs_approval ? [] : [i])) ?? []);
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!selected) return;
    try {
      const r = await approve(selected.id, checked);
      setMessage(`Applied ${r.applied.length} action(s). See /api/audit for the log.`);
    } catch (e) {
      setMessage(String(e));
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
                    <input
                      type="checkbox"
                      disabled={blocked}
                      checked={checked.includes(i)}
                      onChange={(e) => setChecked(e.target.checked ? [...checked, i] : checked.filter((x) => x !== i))}
                    />
                    <strong>{a.type}</strong> on {a.order_id}
                    {a.amount_eur != null && <> · {a.amount_eur.toFixed(2)} EUR</>}
                    {a.new_address && <> · to {a.new_address}</>}
                    {a.needs_approval && <span className="tag warn">needs approval</span>}
                    <div className="muted">{a.reason}</div>
                  </label>
                ))}
                <button onClick={submit} disabled={blocked || checked.length === 0}>
                  Approve selected
                </button>

                <h3>Draft reply</h3>
                <pre className="reply">{result.proposal.customer_reply}</pre>
                {result.redactions.length > 0 && (
                  <p className="muted">
                    Hidden from the model: {result.redactions.join(", ")}. Restored here for you only.
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
