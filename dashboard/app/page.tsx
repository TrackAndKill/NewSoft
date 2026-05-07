"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { kindClass } from "@/lib/event-styles";

function money(n: any) {
  const value = Number(n || 0);
  return `$${value.toFixed(4)}`;
}

function moneyShort(n: any) {
  const v = Number(n || 0);
  if (v >= 100) return `$${v.toFixed(0)}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4)}`;
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
  const spendToday = Number(status?.spend_today_usd || 0);
  const spendCap = Number(status?.daily_spend_cap_usd || 0) || 1;
  const spendPct = Math.min(100, (spendToday / spendCap) * 100);
  const spendCls = spendPct >= 90 ? "danger" : spendPct >= 70 ? "warn" : "";

  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
        <div className="row">
          <button className="ghost" onClick={() => api.triggerDiscovery().then(refresh)}>
            Run discovery
          </button>
          <button className="ghost" onClick={() => api.triggerValidator().then(refresh)}>
            Run validator
          </button>
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

      <div className="card">
        <div
          className="row"
          style={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 24 }}
        >
          <div style={{ minWidth: 220 }}>
            <div
              className="muted"
              style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8, fontWeight: 600 }}
            >
              System
            </div>
            <div className="row" style={{ gap: 6 }}>
              <span className={`pill upper ${status?.active ? "success" : "danger"}`}>
                {status?.active ? "Active" : "Halted"}
              </span>
              <span className={`pill upper ${status?.dry_run ? "warning" : "live"}`}>
                {status?.dry_run ? "Dry-run" : "Live"}
              </span>
            </div>
          </div>

          <div style={{ flex: 1, minWidth: 280 }}>
            <div
              className="muted"
              style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 8, fontWeight: 600 }}
            >
              LLM spend today
            </div>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <span className="num" style={{ fontSize: 18, fontWeight: 600 }}>
                ${spendToday.toFixed(4)}
              </span>
              <span className="muted num" style={{ fontSize: 12 }}>
                / cap ${spendCap.toFixed(2)}
              </span>
            </div>
            <div className={`progress ${spendCls}`}>
              <div className="fill" style={{ width: `${spendPct}%` }} />
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Model costs</h2>
        <div className="stats">
          <div className="stat">
            <div className="label">Today</div>
            <div className="value">{moneyShort(costs?.today?.total_usd)}</div>
          </div>
          <div className="stat">
            <div className="label">Yesterday</div>
            <div className="value">{moneyShort(costs?.yesterday?.total_usd)}</div>
          </div>
          <div className="stat">
            <div className="label">Last 7 days</div>
            <div className="value">{moneyShort(costs?.last_7d?.total_usd)}</div>
          </div>
        </div>
        <h3>Today by agent</h3>
        {Object.entries(todayByAgent).length === 0 ? (
          <div className="empty">No model spend today yet.</div>
        ) : (
          <table>
            <tbody>
              {Object.entries(todayByAgent).map(([agent, cost]) => (
                <tr key={agent}>
                  <td>{agent}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>Recent activity</h2>
      <div className="card flat">
        <table>
          <thead>
            <tr>
              <th style={{ width: 90 }}>Time</th>
              <th style={{ width: 150 }}>Actor</th>
              <th style={{ width: 180 }}>Kind</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {events.slice(0, 30).map((e) => {
              const runId = e.payload?.run_id || e.payload?.agent_run_id;
              return (
                <tr
                  key={e.id}
                  onClick={() => runId && (window.location.href = `/runs/${runId}`)}
                  style={{ cursor: runId ? "pointer" : "default" }}
                >
                  <td className="muted num">{new Date(e.created_at).toLocaleTimeString()}</td>
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
