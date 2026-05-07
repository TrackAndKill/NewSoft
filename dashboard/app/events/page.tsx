"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { kindClass } from "@/lib/event-styles";

export default function EventsPage() {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    api.events().then(setEvents);
  }, []);
  return (
    <>
      <div className="page-head">
        <h1>Activity log</h1>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        Rows with a run id open the full prompt / response / tool-call inspector.
      </p>
      <div className="card flat">
        <table>
          <thead>
            <tr>
              <th style={{ width: 170 }}>Time</th>
              <th style={{ width: 160 }}>Actor</th>
              <th style={{ width: 200 }}>Kind</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => {
              const runId = e.payload?.run_id || e.payload?.agent_run_id;
              return (
                <tr
                  key={e.id}
                  onClick={() => runId && (window.location.href = `/runs/${runId}`)}
                  style={{ cursor: runId ? "pointer" : "default" }}
                >
                  <td className="muted num">{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.actor}</td>
                  <td>
                    <span className={`pill ${kindClass(e.kind)}`}>{e.kind}</span>
                  </td>
                  <td>
                    {e.message}
                    {runId ? <span className="muted"> → run #{runId}</span> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
