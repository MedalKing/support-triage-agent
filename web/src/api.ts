export type Ticket = { id: string; customer_id: string; subject: string; body: string; triaged: boolean };
export type Action = {
  type: string;
  order_id: string;
  reason: string;
  amount_eur: number | null;
  new_address: string | null;
  needs_approval: boolean;
};
export type Proposal = {
  category: string;
  likely_cause: string;
  evidence: string[];
  actions: Action[];
  customer_reply: string;
  confidence: "low" | "medium" | "high";
};
export type TraceStep = { kind: "model" | "tool" | "note"; name: string; detail: unknown };
export type TriageResult = {
  ticket_id: string;
  provider: string;
  proposal: Proposal | null;
  policy_violations: string[];
  trace: TraceStep[];
  redactions: string[];
  error: string | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? r.statusText);
  return r.json();
}

export const listTickets = () => call<Ticket[]>("/api/tickets");
export const triage = (id: string, provider: string) =>
  call<TriageResult>(`/api/tickets/${id}/triage?provider=${provider}`, { method: "POST" });
export const approve = (id: string, action_indexes: number[]) =>
  call<{ applied: unknown[] }>(`/api/tickets/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ action_indexes, approved_by: "demo reviewer" }),
  });
