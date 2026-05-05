"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const SIDE_EFFECTFUL = new Set([
  "register_domain",
  "configure_dns",
  "deploy_landing_page",
  "run_experiment",
  "run_experiment_stage_research",
  "run_experiment_stage_outreach_draft",
  "run_experiment_stage_validation",
  "kill_venture",
  "teardown_site",
]);

function moneyLabel(a: any) {
  const estimated = a.payload?.estimated_usd;
  if (estimated == null) return "live";
  return `live $${Number(estimated).toFixed(2)}`;
}

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
      <p className="muted">
        Side-effectful approvals can be forced to simulate or execute live per approval. Global dry-run stays sticky.
      </p>
      {items.length === 0 && <div className="muted">Nothing to approve.</div>}
      {items.map((a) => {
        const estimated = a.payload?.estimated_usd;
        const sideEffectful = SIDE_EFFECTFUL.has(a.action);
        return (
          <div key={a.id} className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{a.action}</strong>
                <div className="muted">requested by {a.requested_by}</div>
                {estimated != null && (
                  <div className="pill" style={{ marginTop: 8, color: "#ffd166" }}>
                    Estimated real-money cost: ${Number(estimated).toFixed(2)}
                  </div>
                )}
              </div>
              <div className="row">
                {sideEffectful ? (
                  <>
                    <button onClick={() => api.decide(a.id, true, false).then(load)}>Approve (simulate)</button>
                    <button onClick={() => api.decide(a.id, true, true).then(load)}>Approve ({moneyLabel(a)})</button>
                  </>
                ) : (
                  <button onClick={() => api.decide(a.id, true, null).then(load)}>Approve</button>
                )}
                <button className="danger" onClick={() => api.decide(a.id, false).then(load)}>
                  Reject
                </button>
              </div>
            </div>
            {a.rationale && <div style={{ marginTop: 8 }}>{a.rationale}</div>}
            <pre style={{ marginTop: 8, fontSize: 12 }}>{JSON.stringify(a.payload, null, 2)}</pre>
          </div>
        );
      })}
    </>
  );
}
