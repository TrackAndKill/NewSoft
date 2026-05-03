"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function MemosPage() {
  const [memos, setMemos] = useState<any[]>([]);
  useEffect(() => {
    api.memos().then(setMemos);
  }, []);
  return (
    <>
      <h1>Investment memos</h1>
      {memos.map((m) => (
        <div key={m.id} className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>Memo #{m.id} (idea #{m.idea_id})</strong>
            <span className="pill">{m.recommendation}</span>
          </div>
          <pre style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>{m.content}</pre>
          <div className="muted" style={{ marginTop: 8 }}>
            {new Date(m.created_at).toLocaleString()}
          </div>
        </div>
      ))}
    </>
  );
}
