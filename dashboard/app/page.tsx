"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { kindClass } from "@/lib/event-styles";
import { timeAgo, fullTime } from "@/lib/format";

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

      {/* Hero: system + spend */}
      <div className="card hero">
        <div className="cols-2">
          <div>
            <div className="section-label">System</div>
            <div className="row" style={{ gap: 10, alignItems: "center" }}>
              <span className={`dot ${status?.active ? "success" : "danger"}`} aria-hidden />
              <span className="hero-num sm" style={{ letterSpacing: "-0.01em" }}>
                {status?.active ? "Active" : "Halted"}
              </span>
              <span className={`pill upper ${status?.dry_run ? "warning" : "live"}`}>
                {status?.dry_run ? "Dry-run" : "Live"}
              </span>
            </div>
            <div className="muted" style={{ marginTop: 10, fontSize: 12 }}>
              {status?.active
                ? "Scheduled rituals are running."
                : "Kill switch engaged — no agents will run."}
            </div>
          </div>

          <div>
            <div className="section-label">LLM spend today</div>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <span className="hero-num">${spendToday.toFixed(4)}</span>
              <span className="muted num" style={{ fontSize: 12 }}>
                of cap ${spendCap.toFixed(2)}
              </span>
            </div>
            <div className={`progress ${spendCls}`}>
              <div className="fill" style={{ width: `${spendPct}%` }} />
            </div>
            <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              {spendPct >= 90
                ? "Near cap — agents will start refusing runs."
                : spendPct >= 70
                ? "Approaching cap."
                : `${(100 - spendPct).toFixed(0)}% headroom remaining.`}
            </div>
          </div>
        </div>
      </div>

      {/* Costs */}
      <div className="card">
        <div className="card-head">
          <h2>Model costs</h2>
        </div>
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
          <div className="empty">
            <strong>No model spend yet today</strong>
            Agents are quiet. Trigger discovery or validator to wake them up.
          </div>
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
              <th style={{ width: 110 }}>When</th>
              <th style={{ width: 150 }}>Actor</th>
              <th style={{ width: 200 }}>Kind</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr>
                <td colSpan={4}>
                  <div className="empty" style={{ margin: 12 }}>
                    <strong>No events yet</strong>
                    Activity will appear here as agents run.
                  </div>
                </td>
              </tr>
            )}
            {events.slice(0, 30).map((e) => {
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
