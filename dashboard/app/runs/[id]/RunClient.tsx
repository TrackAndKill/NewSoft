"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function Pretty({ value }: { value: any }) {
  return <pre style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

export default function RunClient({ id }: { id: string }) {
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.agentRun(id).then(setRun).catch((e) => setError(String(e)));
  }, [id]);

  if (error) return <div className="card danger">{error}</div>;
  if (!run) return <div className="card">Loading run #{id}…</div>;

  return (
    <>
      <h1>Agent run #{run.id}</h1>
      <div className="card">
        <div className="grid3">
          <div><div className="muted">Agent</div><strong>{run.agent}</strong></div>
          <div><div className="muted">Model</div><strong>{run.model}</strong></div>
          <div><div className="muted">Status</div><strong>{run.status}</strong></div>
        </div>
        <p className="muted">
          {run.input_tokens} input tok / {run.output_tokens} output tok · ${Number(run.cost_usd || 0).toFixed(4)} · {run.duration_ms}ms · {new Date(run.started_at).toLocaleString()}
        </p>
      </div>

      <h2>System</h2>
      <div className="card"><Pretty value={run.system_prompt} /></div>

      <h2>Messages</h2>
      <div className="card"><Pretty value={run.input_messages} /></div>

      <h2>Output</h2>
      <div className="card"><Pretty value={run.output_text || run.error || ""} /></div>

      <h2>Anthropic tool-use blocks</h2>
      <div className="card"><Pretty value={run.tool_calls || []} /></div>

      <h2>Executed tool calls</h2>
      <div className="card"><Pretty value={run.tool_call_rows || []} /></div>
    </>
  );
}
