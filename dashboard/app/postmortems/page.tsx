"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function PostmortemsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [loopResult, setLoopResult] = useState<any>(null);
  useEffect(() => {
    api.postmortems().then(setItems);
  }, []);
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>Postmortems</h1>
          <p className="muted">Shutdown learning archive. Kill loop only files approvals; it never auto-kills.</p>
        </div>
        <button onClick={() => api.triggerKillLoop().then(setLoopResult)}>Run kill loop now</button>
      </div>
      {loopResult && <pre className="card" style={{ fontSize: 12 }}>{JSON.stringify(loopResult, null, 2)}</pre>}
      {items.length === 0 && <div className="muted">No postmortems yet.</div>}
      {items.map((pm) => (
        <div key={pm.id} className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>{pm.venture?.name || `Venture #${pm.venture_id}`}</strong>
              <div className="muted">Postmortem #{pm.id} · {new Date(pm.created_at).toLocaleString()}</div>
            </div>
            {pm.venture?.slug && <Link href={`/ventures/${pm.venture.slug}`}>Open venture</Link>}
          </div>
          <h3>Lessons</h3>
          <pre style={{ whiteSpace: "pre-wrap" }}>{pm.lessons_md}</pre>
        </div>
      ))}
    </>
  );
}
