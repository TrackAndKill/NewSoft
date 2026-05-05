"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function pillColor(status: string) {
  if (status === "done") return "#7ee787";
  if (status === "pending_approval") return "#ffd166";
  if (status === "failed") return "#ff6b6b";
  if (status === "running") return "#79c0ff";
  return "#8a9099";
}

export default function ExperimentsPage() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api.experiments().then(setItems).catch(console.error); }, []);
  return (
    <>
      <h1>Experiments</h1>
      <p className="muted">Stage-gated validation: research_only → outreach_draft → validation_run. Stage 3 remains approval-gated.</p>
      {items.length === 0 && <div className="muted">No experiments yet.</div>}
      {items.map((exp) => (
        <a key={exp.id} href={`/experiments/${exp.id}`} className="card" style={{ display: "block", color: "inherit" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>Experiment #{exp.id} / memo #{exp.memo_id}</strong>
            <span className="pill">{exp.status}</span>
          </div>
          <div className="row" style={{ marginTop: 12, flexWrap: "wrap" }}>
            {(exp.stages || []).map((st: any) => (
              <span key={st.id} className="pill" style={{ color: pillColor(st.status) }}>{st.stage_index}. {st.stage_name}: {st.status}</span>
            ))}
          </div>
        </a>
      ))}
    </>
  );
}
