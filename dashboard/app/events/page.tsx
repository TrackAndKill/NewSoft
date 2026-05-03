"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function EventsPage() {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    api.events().then(setEvents);
  }, []);
  return (
    <>
      <h1>Activity log</h1>
      <p className="muted">Rows with a run id open the full prompt / response / tool-call inspector.</p>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Actor</th>
            <th>Kind</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const runId = e.payload?.run_id || e.payload?.agent_run_id;
            return (
              <tr key={e.id} onClick={() => runId && (window.location.href = `/runs/${runId}`)} style={{ cursor: runId ? "pointer" : "default" }}>
                <td className="muted">{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.actor}</td>
                <td><span className="pill">{e.kind}</span></td>
                <td>{e.message}{runId ? <span className="muted"> → run #{runId}</span> : null}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
