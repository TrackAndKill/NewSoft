export const API =
  typeof window === "undefined"
    ? process.env.INTERNAL_API_URL || "http://127.0.0.1:8000"
    : process.env.NEXT_PUBLIC_API_URL || "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  status: () => req<any>("/api/status"),
  costs: () => req<any>("/api/costs"),
  moneyStatus: () => req<any>("/api/money/status"),
  moneyTransactions: () => req<any[]>("/api/money/transactions"),
  goals: () => req<any[]>("/api/goals"),
  createGoal: (title: string, description: string) =>
    req<any>("/api/goals", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title, description }),
    }),
  ideas: () => req<any[]>("/api/ideas"),
  memos: () => req<any[]>("/api/memos"),
  events: () => req<any[]>("/api/events"),
  agentRun: (id: number | string) => req<any>(`/api/agent_runs/${id}`),
  experiments: () => req<any[]>("/api/experiments"),
  experiment: (id: number | string) => req<any>(`/api/experiments/${id}`),
  memoryStatus: () => req<any>("/api/memory/status"),
  memorySearch: (query: string, kinds?: string[], limit = 10) =>
    req<any[]>("/api/memory/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query, kinds, limit }),
    }),
  approvals: (status = "pending") => req<any[]>(`/api/approvals?status=${status}`),
  decide: (id: number, approve: boolean, execute_live?: boolean | null) =>
    req<any>(`/api/approvals/${id}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approve, decided_by: "founder", execute_live }),
    }),
  postmortems: () => req<any[]>("/api/postmortems"),
  venturePostmortem: (slug: string) => req<any>(`/api/ventures/${slug}/postmortem`),
  createPostmortem: (slug: string) => req<any>(`/api/ventures/${slug}/postmortem`, { method: "POST" }),
  triggerKillLoop: () => req<any>("/api/kill_loop/run", { method: "POST" }),
  triggerDiscovery: () => req<any>("/api/discovery/run", { method: "POST" }),
  triggerBoard: () => req<any>("/api/board/run", { method: "POST" }),
  triggerValidator: () => req<any>("/api/validator/run", { method: "POST" }),
  triggerVenture: () => req<any>("/api/venture/run", { method: "POST" }),
  triggerDigest: () => req<any>("/api/digest/run", { method: "POST" }),
  boardReviews: (memoId?: number) =>
    req<any[]>(`/api/board/reviews${memoId ? `?memo_id=${memoId}` : ""}`),
  ventures: () => req<any[]>("/api/ventures"),
  venture: (slug: string) => req<any>(`/api/ventures/${slug}`),
  venturePlan: (slug: string) => req<any>(`/api/ventures/${slug}/plan`),
  sites: () => req<any[]>("/api/sites"),
  site: (slug: string) => req<any>(`/api/sites/${slug}`),
  siteLeads: (slug: string) => req<any[]>(`/api/sites/${slug}/leads`),
  suppressions: (reason?: string) => req<any>(`/api/suppressions${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`),
  createSuppression: (email: string, reason = "manual", note = "") =>
    req<any>("/api/suppressions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, reason, note }),
    }),
  createDomainBlock: (domain: string, reason = "manual", note = "") =>
    req<any>("/api/domain_blocks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ domain, reason, note }),
    }),
  outreach: (experimentId?: number | string) =>
    req<any>(`/api/outreach${experimentId ? `?experiment_id=${experimentId}` : ""}`),
  kill: () => req<any>("/api/system/kill", { method: "POST" }),
  setSystem: (patch: Record<string, unknown>) =>
    req<any>("/api/system", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    }),
};
