"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function IdeasPage() {
  const [ideas, setIdeas] = useState<any[]>([]);
  useEffect(() => {
    api.ideas().then(setIdeas);
  }, []);
  return (
    <>
      <h1>Ideas</h1>
      <table>
        <thead>
          <tr>
            <th>Score</th>
            <th>Title</th>
            <th>Status</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {ideas.map((i) => (
            <tr key={i.id}>
              <td>{i.score?.toFixed(2) ?? "—"}</td>
              <td>
                <strong>{i.title}</strong>
                {i.summary && <div className="muted">{i.summary}</div>}
              </td>
              <td>
                <span className="pill">{i.status}</span>
              </td>
              <td className="muted">{new Date(i.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
