// Maps an event "kind" string to a CSS class for color-coding the pill.
// Returns "" when no specific category matches (renders the neutral pill).
export function kindClass(kind: string | undefined | null): string {
  if (!kind) return "";
  const k = String(kind).toLowerCase();

  if (k === "error" || k.endsWith("_error") || k.includes("failed")) return "kind-error";
  if (k === "system" || k.startsWith("kill")) return "kind-system";

  if (k.startsWith("discovery")) return "kind-discovery";
  if (k.startsWith("board") || k.startsWith("memo")) return "kind-board";

  if (k.startsWith("approval") || k.endsWith("_approval_requested") || k.endsWith("_approval_executed"))
    return "kind-approval";

  if (k.startsWith("experiment") || k.startsWith("validator")) return "kind-experiment";

  if (k.startsWith("money")) return "kind-money";
  if (k.startsWith("clarification")) return "kind-clarification";
  if (k.startsWith("digest")) return "kind-digest";
  if (k.startsWith("teardown") || k.startsWith("site")) return "kind-site";
  if (k.startsWith("copywriter")) return "kind-copywriter";
  if (k === "agent_run") return "kind-agent_run";

  if (k.startsWith("venture") || k === "goal_created") return "kind-venture";

  return "";
}
