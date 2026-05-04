"use client";

import { useEffect, useState } from "react";
import { use } from "react";
import { api } from "@/lib/api";

const VOTE_COLOR: Record<string, string> = {
  fund: "#7ee787",
  explore: "#d2a8ff",
  pass: "#ff6b6b",
};

export default function VenturePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.venture(slug).then(setData);
  }, [slug]);

  if (!data) return <div className="muted">Loading…</div>;
  const { venture, memo, idea, reviews, plan, tasks = [] } = data;
  return (
    <>
      <h1>{venture.name}</h1>
      <div className="row" style={{ gap: 8 }}>
        <span className="pill">{venture.status}</span>
        <span className="muted">{venture.slug}</span>
      </div>

      {data.site && <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Site</h2>
        <div className="row" style={{ gap: 8 }}>
          <span className="pill">{data.site.status}</span>
          <a href={`/sites/${data.site.slug}`}>Open site ops</a>
          {data.site.domain && <a href={`https://${data.site.domain}`} target="_blank">{data.site.domain}</a>}
        </div>
      </div>}

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Charter</h2>
        <pre style={{ whiteSpace: "pre-wrap" }}>{venture.charter}</pre>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Venture pod plan</h2>
        {!plan ? <div className="muted">No CTO/Engineer plan yet. The venture pod runs every 30 minutes.</div> : (
          <>
            <div className="grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
              <div><h3>30 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_30}</pre></div>
              <div><h3>60 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_60}</pre></div>
              <div><h3>90 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_90}</pre></div>
            </div>
            <h3>First-30-day tasks</h3>
            {tasks.length === 0 ? <div className="muted">No tasks yet.</div> : (
              <table>
                <thead><tr><th>Task</th><th>Status</th><th>Approval</th><th>Description</th></tr></thead>
                <tbody>
                  {tasks.map((t: any) => (
                    <tr key={t.id}>
                      <td>{t.title}</td>
                      <td><span className="pill">{t.status}</span></td>
                      <td>{t.needs_approval ? <span className="pill">{t.approval_action} #{t.approval_id}</span> : "—"}</td>
                      <td>{t.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Board votes</h2>
        <table>
          <thead><tr><th>Partner</th><th>Vote</th><th>Rationale</th></tr></thead>
          <tbody>
            {reviews.map((r: any) => (
              <tr key={r.id}>
                <td>{r.persona}</td>
                <td><span className="pill" style={{ color: VOTE_COLOR[r.vote] || "inherit" }}>{r.vote.toUpperCase()}</span></td>
                <td>{r.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {memo && <div className="card"><h2 style={{ marginTop: 0 }}>Investment memo</h2><pre style={{ whiteSpace: "pre-wrap" }}>{memo.content}</pre></div>}
      {idea && <div className="card"><h2 style={{ marginTop: 0 }}>Origin idea</h2><div><strong>{idea.title}</strong>{idea.score != null && <span className="muted"> (score {idea.score.toFixed(2)})</span>}</div>{idea.summary && <div className="muted" style={{ marginTop: 6 }}>{idea.summary}</div>}</div>}
    </>
  );
}
