export const API =
  typeof window === "undefined"
    ? process.env.INTERNAL_API_URL || "http://127.0.0.1:8000"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
  approvals: (status = "pending") => req<any[]>(`/api/approvals?status=${status}`),
  decide: (id: number, approve: boolean) =>
    req<any>(`/api/approvals/${id}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approve, decided_by: "founder" }),
    }),
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
  kill: () => req<any>("/api/system/kill", { method: "POST" }),
  setSystem: (patch: Record<string, unknown>) =>
    req<any>("/api/system", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    }),
};
