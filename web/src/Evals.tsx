import { useEffect, useState } from "react";
import { EvalCase, EvalRun, evalReport, evalRuns } from "./api";

const CHECKS: Record<string, string> = {
  proposal: "proposal",
  category: "category",
  tools: "lookups",
  looked_up_order: "order lookup",
  required_actions: "required actions",
  forbidden_actions: "forbidden actions",
  only_allowed_actions: "allowed actions",
  credit_amount: "credit amount",
  policy: "policy",
  no_pii_to_model: "PII",
  reply_ready: "reply",
  working_days: "working days",
};

const money = (v: number | null | undefined, digits = 4) => (v == null ? "–" : `$${v.toFixed(digits)}`);

export default function Evals({ onBack }: { onBack: () => void }) {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [cases, setCases] = useState<EvalCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    evalRuns()
      .then((r) => {
        setRuns(r);
        if (r.length) setSelected(r[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setCases(null);
    evalReport(selected)
      .then((r) => setCases(r.cases))
      .catch((e) => setError(String(e)));
  }, [selected]);

  const run = runs.find((r) => r.id === selected);

  return (
    <div className="admin-page">
      <header className="admin-header">
        <button className="link back-btn" onClick={onBack}>
          ← Back
        </button>
        <div className="admin-title">
          <h1>
            Evals <span className="tag admin">admin</span>
          </h1>
          <span className="muted small">Deterministic checks · Claude API list prices</span>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <div className="table-wrap">
        <table className="evals runs-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>When</th>
              <th>Passed</th>
              <th>Escalated</th>
              <th>Cost / ticket</th>
              <th>Median time</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.id}
                className={r.id === selected ? "selected" : ""}
                onClick={() => setSelected(r.id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected(r.id);
                  }
                }}
              >
                <td className="run-name" data-label="Run">
                  <span className="run-label">{r.label}</span>
                  {r.prompt_cache != null && (
                    <span className="tag">{r.prompt_cache ? "cache on" : "cache off"}</span>
                  )}
                </td>
                <td className="muted nowrap" data-label="When">
                  {r.date} {r.time}
                </td>
                <td className={`nowrap ${r.passed === r.total ? "ok" : "bad"}`} data-label="Passed">
                  {r.passed}/{r.total}
                </td>
                <td className="nowrap" data-label="Escalated">
                  {r.escalated ?? 0}
                </td>
                <td className="nowrap" data-label="Cost">
                  {money(r.cost_per_ticket)}
                </td>
                <td className="nowrap" data-label="Time">
                  {r.median_seconds == null ? "–" : `${r.median_seconds}\u00A0s`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {run && (
        <section className="card">
          <div className="card-head">
            <h2>
              {run.label}{" "}
              <span className="muted small">
                {run.date} {run.time}
              </span>
            </h2>
            <a className="json-link small" href={`/api/evals/${run.id}`} target="_blank" rel="noreferrer">
              JSON ↗
            </a>
          </div>
          {!cases && <p className="muted">Loading…</p>}
          {cases && (
            <div className="table-wrap">
              <table className="evals cases-table">
                <thead>
                  <tr>
                    <th>Ticket</th>
                    <th>Result</th>
                    <th>Proposed</th>
                    <th>Escalated</th>
                    <th>Cost</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => {
                    const failed = Object.entries(c.checks ?? {}).filter(([, ok]) => !ok).map(([k]) => CHECKS[k] ?? k);
                    return (
                      <tr key={c.ticket}>
                        <td className="nowrap ticket-col" data-label="Ticket">
                          <strong>{c.ticket}</strong>
                        </td>
                        <td className="result-col" data-label="Result">
                          {c.passed ? (
                            <span className="ok nowrap">✓ pass</span>
                          ) : (
                            <>
                              <span className="bad nowrap">✗ fail</span>
                              <div className="muted small">{failed.join(", ") || c.error}</div>
                            </>
                          )}
                        </td>
                        <td className="small proposed-col" data-label="Proposed">
                          {c.proposed ? (
                            <>
                              <code className="action-type">{c.proposed.category}</code>
                              <div className="muted">
                                {c.proposed.actions
                                  .map((a) => `${a.type}${a.amount_eur != null ? ` ${a.amount_eur.toFixed(2)}\u00A0€` : ""}`)
                                  .join(", ") || "no actions"}
                              </div>
                            </>
                          ) : (
                            <span className="muted">{c.error ?? "no proposal"}</span>
                          )}
                        </td>
                        <td className="small muted esc-col" data-label="Escalated">
                          {c.escalation ? c.escalation.reasons.join("; ") : "–"}
                        </td>
                        <td className="small nowrap" data-label="Cost">
                          {money(c.usage?.cost_usd)}
                        </td>
                        <td className="small nowrap" data-label="Time">
                          {c.seconds == null ? "–" : `${c.seconds.toFixed(0)}\u00A0s`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
