"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";

function shortHash(value: string) {
  return value ? `${value.slice(0, 10)}…` : "";
}

function statusStyle(status: string) {
  if (["bounced", "complained", "failed", "suppressed"].includes(status)) return { color: "#ff6b6b" };
  if (["sent", "delivered", "dry_run"].includes(status)) return { color: "#7ee787" };
  return {};
}

function OutreachInner() {
  const params = useSearchParams();
  const [data, setData] = useState<any>({ items: [], counts: [] });
  const [experimentId, setExperimentId] = useState(params.get("experiment_id") || "");
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setData(await api.outreach(experimentId || undefined));
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [experimentId]);

  return (
    <>
      <h1>Outreach</h1>
      <p className="muted">Recent outreach sends and dry-run captures. Raw recipient emails are intentionally hidden.</p>
      {error ? <div className="card" style={{ borderColor: "#ff6b6b" }}>{error}</div> : null}

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <h2 style={{ marginTop: 0 }}>Per-experiment counts</h2>
          <input placeholder="experiment id" value={experimentId} onChange={(e) => setExperimentId(e.target.value)} />
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          {(data.counts || []).map((c: any, idx: number) => (
            <span key={idx} className="pill">exp #{c.experiment_id} {c.status}: {c.count}</span>
          ))}
          {(data.counts || []).length === 0 ? <span className="muted">No outreach sends yet.</span> : null}
        </div>
      </div>

      <table>
        <thead>
          <tr><th>Created</th><th>Experiment</th><th>Recipient hash</th><th>Domain</th><th>Subject</th><th>Status</th><th>Provider</th></tr>
        </thead>
        <tbody>
          {(data.items || []).map((row: any) => (
            <tr key={row.id}>
              <td className="muted">{new Date(row.created_at).toLocaleString()}</td>
              <td><a href={`/experiments/${row.experiment_id}`}>#{row.experiment_id}</a></td>
              <td><code>{shortHash(row.recipient_hash)}</code></td>
              <td>{row.recipient_domain}</td>
              <td>{row.subject}</td>
              <td><span className="pill" style={statusStyle(row.status)}>{row.status}</span>{row.dry_run ? <span className="pill">dry-run</span> : null}</td>
              <td>{row.provider_id ? shortHash(row.provider_id) : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function OutreachPage() {
  return (
    <Suspense fallback={<p className="muted">Loading outreach…</p>}>
      <OutreachInner />
    </Suspense>
  );
}
