"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function ApprovalsPage() {
  const [items, setItems] = useState<any[]>([]);
  async function load() {
    setItems(await api.approvals("pending"));
  }
  useEffect(() => {
    load();
  }, []);
  return (
    <>
      <h1>Pending approvals</h1>
      {items.length === 0 && <div className="muted">Nothing to approve.</div>}
      {items.map((a) => (
        <div key={a.id} className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>{a.action}</strong>
              <div className="muted">requested by {a.requested_by}</div>
            </div>
            <div className="row">
              <button onClick={() => api.decide(a.id, true).then(load)}>Approve</button>
              <button className="danger" onClick={() => api.decide(a.id, false).then(load)}>
                Reject
              </button>
            </div>
          </div>
          {a.rationale && <div style={{ marginTop: 8 }}>{a.rationale}</div>}
          <pre style={{ marginTop: 8, fontSize: 12 }}>{JSON.stringify(a.payload, null, 2)}</pre>
        </div>
      ))}
    </>
  );
}
