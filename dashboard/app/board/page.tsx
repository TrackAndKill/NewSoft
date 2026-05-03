"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const VOTE_COLOR: Record<string, string> = {
  fund: "#7ee787",
  explore: "#d2a8ff",
  pass: "#ff6b6b",
};

export default function BoardPage() {
  const [reviews, setReviews] = useState<any[]>([]);
  async function load() {
    setReviews(await api.boardReviews());
  }
  useEffect(() => {
    load();
  }, []);
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Board reviews</h1>
        <button onClick={() => api.triggerBoard().then(() => setTimeout(load, 2000))}>
          Run board now
        </button>
      </div>
      {reviews.length === 0 && <div className="muted">No reviews yet.</div>}
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Memo</th>
            <th>Partner</th>
            <th>Vote</th>
            <th>Rationale</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map((r) => (
            <tr key={r.id}>
              <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
              <td>#{r.memo_id}</td>
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
    </>
  );
}
