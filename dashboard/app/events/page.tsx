"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { kindClass } from "@/lib/event-styles";
import { timeAgo, fullTime } from "@/lib/format";

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
              <th style={{ width: 130 }}>When</th>
              <th style={{ width: 160 }}>Actor</th>
              <th style={{ width: 200 }}>Kind</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr>
                <td colSpan={4}>
                  <div className="empty" style={{ margin: 12 }}>
                    <strong>No activity yet</strong>
                    Events will populate here as agents run.
                  </div>
                </td>
              </tr>
            )}
            {events.map((e) => {
              const runId = e.payload?.run_id || e.payload?.agent_run_id;
              return (
                <tr
                  key={e.id}
                  onClick={() => runId && (window.location.href = `/runs/${runId}`)}
                  style={{ cursor: runId ? "pointer" : "default" }}
                >
                  <td className="muted num" title={fullTime(e.created_at)}>
                    {timeAgo(e.created_at)}
                  </td>
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
