import { useEffect, useState } from "react";
import { EvalCase, EvalRun, evalReport, evalRuns } from "./api";

// What each deterministic check means, in a few words.
const CHECKS: Record<string, string> = {
  proposal: "gave a proposal",
  category: "right category",
  tools: "made the required lookups",
  looked_up_order: "looked up the right order",
  required_actions: "proposed the required actions",
  forbidden_actions: "no forbidden actions",
  only_allowed_actions: "only allowed actions",
  credit_amount: "exact credit amount",
  policy: "passes the policy check",
  no_pii_to_model: "no personal data to the model",
  reply_ready: "reply ready to send",
  working_days: "right number of working days late",
};

const money = (v: number | null | undefined, digits = 4) => (v == null ? "–" : `$${v.toFixed(digits)}`);

// Admin and demo only. A production deployment would keep this behind an admin role; the people
// triaging tickets would not see it.
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
      <div className="admin-banner" role="note">
        <span className="admin-badge">Admin · demo only</span>
        <span>
          Eval results for people assessing this demo. In production this page would sit behind an admin role; the
          people triaging tickets would never see it.
        </span>
        <button className="link small" onClick={onBack}>
          ← Back to the app
        </button>
      </div>

      <h1>Evals</h1>
      <p className="lead small-lead">
        Each run sends all {run?.total ?? 9} sample tickets through the agent and scores the outcome with code, not
        with another model: category, lookups, allowed actions, exact amounts, policy, and that no personal data reached
        the model. Prices are Claude API list prices.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="table-wrap">
        <table className="evals">
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
              <tr key={r.id} className={r.id === selected ? "selected" : ""} onClick={() => setSelected(r.id)}>
                <td>
                  <button className="link small" onClick={() => setSelected(r.id)}>
                    {r.label}
                  </button>
                  {r.prompt_cache != null && (
                    <span className="tag">{r.prompt_cache ? "cache on" : "cache off"}</span>
                  )}
                </td>
                <td className="muted">
                  {r.date} {r.time}
                </td>
                <td className={r.passed === r.total ? "ok" : "bad"}>
                  {r.passed}/{r.total}
                </td>
                <td>{r.escalated ?? 0}</td>
                <td>{money(r.cost_per_ticket)}</td>
                <td>{r.median_seconds == null ? "–" : `${r.median_seconds} s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {run && (
        <section className="card">
          <h2>
            {run.label} <span className="muted small">{run.date} {run.time}</span>
          </h2>
          <p className="muted small">
            <a href={`/api/evals/${run.id}`} target="_blank" rel="noreferrer">
              Raw report (JSON)
            </a>{" "}
            · evals/results/{run.id}
          </p>
          {!cases && <p className="muted">Loading…</p>}
          {cases && (
            <div className="table-wrap">
              <table className="evals">
                <thead>
                  <tr>
                    <th>Ticket</th>
                    <th>Result</th>
                    <th>Proposed</th>
                    <th>Escalated because</th>
                    <th>Cost</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => {
                    const failed = Object.entries(c.checks ?? {}).filter(([, ok]) => !ok).map(([k]) => CHECKS[k] ?? k);
                    return (
                      <tr key={c.ticket}>
                        <td>{c.ticket}</td>
                        <td>
                          {c.passed ? (
                            <span className="ok">✓ pass</span>
                          ) : (
                            <>
                              <span className="bad">✗ fail</span>
                              <div className="muted small">missed: {failed.join(", ") || c.error}</div>
                            </>
                          )}
                        </td>
                        <td className="small">
                          {c.proposed ? (
                            <>
                              <code>{c.proposed.category}</code>
                              <div className="muted">
                                {c.proposed.actions
                                  .map((a) => `${a.type}${a.amount_eur != null ? ` ${a.amount_eur.toFixed(2)} €` : ""}`)
                                  .join(", ") || "no actions"}
                              </div>
                            </>
                          ) : (
                            <span className="muted">{c.error ?? "no proposal"}</span>
                          )}
                        </td>
                        <td className="small muted">{c.escalation ? c.escalation.reasons.join("; ") : "–"}</td>
                        <td className="small">{money(c.usage?.cost_usd)}</td>
                        <td className="small">{c.seconds == null ? "–" : `${c.seconds.toFixed(0)} s`}</td>
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
