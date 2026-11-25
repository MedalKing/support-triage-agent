export type Ticket = { id: string; customer_id: string; subject: string; body: string };
export type Action = {
  type: string;
  order_id: string;
  reason: string;
  amount_eur: number | null;
  new_address: string | null;
  needs_approval: boolean;
  approval_reason: string | null;
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
  proposal_id: string;
  applied: number[];
  provider: string;
  escalation: { from: string; to: string; reasons: string[] } | null;
  recorded: boolean;
  proposal: Proposal | null;
  policy_violations: string[];
  trace: TraceStep[];
  redactions: string[];
  error: string | null;
};
export type AuditEntry = {
  ts: string;
  approved_by: string;
  ticket_id: string;
  type: string;
  order_id: string;
  amount_eur: number | null;
  new_address: string | null;
};

export type Config = {
  providers: string[];
  access_code_required: boolean;
  recorded: string[];
  // Recorded runs redone by the stronger model: why, and whether it proposed different actions.
  escalated: Record<string, { reasons: string[]; changed: boolean }>;
};

async function call<T>(path: string, init?: RequestInit, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...headers } });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? r.statusText);
  return r.json();
}

export const listTickets = () => call<Ticket[]>("/api/tickets");
export const getConfig = () => call<Config>("/api/config");
export const triage = (id: string, provider: string, accessCode: string) =>
  call<TriageResult>(`/api/tickets/${id}/triage?provider=${encodeURIComponent(provider)}`, { method: "POST" },
    accessCode ? { "X-Access-Code": accessCode } : {});
export const recorded = (id: string) => call<TriageResult>(`/api/tickets/${id}/recorded`);
export const approve = (id: string, proposal_id: string, action_indexes: number[]) =>
  call<{ applied: AuditEntry[] }>(`/api/tickets/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ proposal_id, action_indexes, approved_by: "demo reviewer" }),
  });
export const auditLog = () => call<AuditEntry[]>("/api/audit");
export const reset = () => call<{ status: string }>("/api/reset", { method: "POST" });
