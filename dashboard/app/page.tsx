"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function money(n: any) {
  const value = Number(n || 0);
  return `$${value.toFixed(4)}`;
}

export default function Overview() {
  const [status, setStatus] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [costs, setCosts] = useState<any>(null);

  async function refresh() {
    try {
      const [s, e, c] = await Promise.all([api.status(), api.events(), api.costs()]);
      setStatus(s);
      setEvents(e);
      setCosts(c);
    } catch (err) {
      console.error(err);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const todayByAgent = costs?.today?.by_agent || {};

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
            <button onClick={() => api.triggerValidator().then(refresh)}>Run validator now</button>
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

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Model costs</h2>
        <div className="grid3">
          <div>
            <div className="muted">Today</div>
            <strong>{money(costs?.today?.total_usd)}</strong>
          </div>
          <div>
            <div className="muted">Yesterday</div>
            <strong>{money(costs?.yesterday?.total_usd)}</strong>
          </div>
          <div>
            <div className="muted">Last 7 days</div>
            <strong>{money(costs?.last_7d?.total_usd)}</strong>
          </div>
        </div>
        <h3>Today by agent</h3>
        <table>
          <tbody>
            {Object.entries(todayByAgent).length === 0 && (
              <tr><td className="muted">No model spend today yet.</td><td /></tr>
            )}
            {Object.entries(todayByAgent).map(([agent, cost]) => (
              <tr key={agent}>
                <td>{agent}</td>
                <td>{money(cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
          {events.slice(0, 30).map((e) => {
            const runId = e.payload?.run_id || e.payload?.agent_run_id;
            return (
              <tr key={e.id} onClick={() => runId && (window.location.href = `/runs/${runId}`)} style={{ cursor: runId ? "pointer" : "default" }}>
                <td className="muted">{new Date(e.created_at).toLocaleTimeString()}</td>
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
