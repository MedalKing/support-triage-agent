// archived: the reviewer is done with it. Everything else is the inbox.
export type Ticket = {
  id: string;
  received: string; // "2026-11-23T08:12", the demo's own clock
  customer_id: string;
  customer_name: string;
  subject: string;
  body: string;
  archived: boolean;
};
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
  declined: Record<string, string>; // action index -> the reviewer's reason
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
  decision: "approved" | "declined";
  approved_by?: string;
  declined_by?: string;
  decline_reason?: string;
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
  call<{ applied: AuditEntry[]; archived: boolean }>(`/api/tickets/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ proposal_id, action_indexes, approved_by: "demo reviewer" }),
  });
export const decline = (id: string, proposal_id: string, action_indexes: number[], reason: string) =>
  call<{ declined: AuditEntry[]; archived: boolean }>(`/api/tickets/${id}/decline`, {
    method: "POST",
    body: JSON.stringify({ proposal_id, action_indexes, declined_by: "demo reviewer", reason }),
  });
export const markDone = (id: string) => call<{ archived: boolean }>(`/api/tickets/${id}/archive`, { method: "POST" });
export const moveToInbox = (id: string) =>
  call<{ archived: boolean }>(`/api/tickets/${id}/unarchive`, { method: "POST" });
export const auditLog = () => call<AuditEntry[]>("/api/audit");
export const reset = () => call<{ status: string }>("/api/reset", { method: "POST" });
