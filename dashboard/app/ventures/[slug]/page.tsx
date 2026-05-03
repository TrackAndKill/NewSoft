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
  const { venture, memo, idea, reviews } = data;
  return (
    <>
      <h1>{venture.name}</h1>
      <div className="row" style={{ gap: 8 }}>
        <span className="pill">{venture.status}</span>
        <span className="muted">{venture.slug}</span>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Charter</h2>
        <pre style={{ whiteSpace: "pre-wrap" }}>{venture.charter}</pre>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Board votes</h2>
        <table>
          <thead>
            <tr>
              <th>Partner</th>
              <th>Vote</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {reviews.map((r: any) => (
              <tr key={r.id}>
                <td>{r.persona}</td>
                <td>
                  <span className="pill" style={{ color: VOTE_COLOR[r.vote] || "inherit" }}>
                    {r.vote.toUpperCase()}
                  </span>
                </td>
                <td>{r.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {memo && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Investment memo</h2>
          <pre style={{ whiteSpace: "pre-wrap" }}>{memo.content}</pre>
        </div>
      )}

      {idea && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Origin idea</h2>
          <div>
            <strong>{idea.title}</strong>
            {idea.score != null && <span className="muted"> (score {idea.score.toFixed(2)})</span>}
          </div>
          {idea.summary && <div className="muted" style={{ marginTop: 6 }}>{idea.summary}</div>}
        </div>
      )}
    </>
  );
}
