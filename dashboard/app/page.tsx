"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Overview() {
  const [status, setStatus] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);

  async function refresh() {
    try {
      const [s, e] = await Promise.all([api.status(), api.events()]);
      setStatus(s);
      setEvents(e);
    } catch (err) {
      console.error(err);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <h1>Overview</h1>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div>
              System:{" "}
              <span className="pill" style={{ color: status?.active ? "#7ee787" : "#ff6b6b" }}>
                {status?.active ? "ACTIVE" : "HALTED"}
              </span>{" "}
              <span className="pill">{status?.dry_run ? "dry-run" : "LIVE"}</span>
            </div>
            <div className="muted" style={{ marginTop: 6 }}>
              Spend today: ${status?.spend_today_usd?.toFixed(4) ?? "0.0000"} / cap $
              {status?.daily_spend_cap_usd?.toFixed(2) ?? "0.00"}
            </div>
          </div>
          <div className="row">
            <button onClick={() => api.triggerDiscovery().then(refresh)}>Run discovery now</button>
            <button
              className="danger"
              onClick={() => {
                if (confirm("Engage kill switch?")) api.kill().then(refresh);
              }}
            >
              KILL
            </button>
          </div>
        </div>
      </div>

      <h2>Recent activity</h2>
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
          {events.slice(0, 30).map((e) => (
            <tr key={e.id}>
              <td className="muted">{new Date(e.created_at).toLocaleTimeString()}</td>
              <td>{e.actor}</td>
              <td>
                <span className="pill">{e.kind}</span>
              </td>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
