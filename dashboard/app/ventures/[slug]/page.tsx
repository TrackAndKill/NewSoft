"use client";

import { useEffect, useState } from "react";
import { use } from "react";
import { api } from "@/lib/api";

const VOTE_COLOR: Record<string, string> = {
  fund: "#7ee787",
  explore: "#d2a8ff",
  pass: "#ff6b6b",
};

export default function VenturePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const [data, setData] = useState<any>(null);
  const [budget, setBudget] = useState<Record<string, string>>({});

  async function load() {
    const next = await api.venture(slug);
    setData(next);
    setBudget({
      daily_llm_cap_usd: String(next.venture.daily_llm_cap_usd ?? 5),
      daily_money_cap_usd: String(next.venture.daily_money_cap_usd ?? 10),
      total_money_cap_usd: String(next.venture.total_money_cap_usd ?? 50),
    });
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (!data) return <div className="muted">Loading…</div>;
  const { venture, memo, idea, reviews, plan, tasks = [], postmortem } = data;
  return (
    <>
      <h1>{venture.name}</h1>
      <div className="row" style={{ gap: 8 }}>
        <span className="pill">{venture.status}</span>
        <span className="muted">{venture.slug}</span>
        {venture.killed_at && <span className="pill">killed {new Date(venture.killed_at).toLocaleString()}</span>}
        {venture.kill_reason && <span className="muted">{venture.kill_reason}</span>}
      </div>

      {data.site && <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Site</h2>
        <div className="row" style={{ gap: 8 }}>
          <span className="pill">{data.site.status}</span>
          <a href={`/sites/${data.site.slug}`}>Open site ops</a>
          {data.site.domain && <a href={`https://${data.site.domain}`} target="_blank">{data.site.domain}</a>}
        </div>
      </div>}

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Budgets</h2>
        <div className="grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
          <BudgetPill label="LLM today" value={Number(venture.llm_spend_today_usd || 0)} cap={Number(venture.daily_llm_cap_usd || 5)} precision={4} />
          <BudgetPill label="Money today" value={Number(venture.money_spend_today_usd || 0)} cap={Number(venture.daily_money_cap_usd || 10)} precision={2} />
          <BudgetPill label="Money lifetime" value={Number(venture.money_spend_lifetime_usd || 0)} cap={Number(venture.total_money_cap_usd || 50)} precision={2} />
        </div>
        <div className="row" style={{ marginTop: 12, alignItems: "end" }}>
          {[
            ["daily_llm_cap_usd", "Daily LLM cap"],
            ["daily_money_cap_usd", "Daily money cap"],
            ["total_money_cap_usd", "Lifetime money cap"],
          ].map(([key, label]) => (
            <label key={key} style={{ display: "grid", gap: 4 }}>
              <span className="muted">{label}</span>
              <input value={budget[key] || ""} onChange={(e) => setBudget((prev) => ({ ...prev, [key]: e.target.value }))} style={{ width: 120 }} />
            </label>
          ))}
          <button onClick={async () => { await api.updateVentureBudgets(slug, {
            daily_llm_cap_usd: Number(budget.daily_llm_cap_usd),
            daily_money_cap_usd: Number(budget.daily_money_cap_usd),
            total_money_cap_usd: Number(budget.total_money_cap_usd),
          }); await load(); }}>Save caps</button>
        </div>
      </div>

      {venture.status === "killed" && <div className="card" style={{ marginTop: 16, borderColor: "#ff6b6b" }}>
        <h2 style={{ marginTop: 0 }}>Teardown grace</h2>
        <p className="muted">Killed ventures get a teardown_site approval after 24h. Teardown only removes live site artifacts after approval.</p>
        <div className="row">
          <button onClick={async () => { await api.reviveVenture(slug); await load(); }}>Revive + cancel pending teardown</button>
          <button onClick={async () => { await api.extendVentureGrace(slug, 24); await load(); }}>Extend grace 24h</button>
        </div>
      </div>}

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Clarifications</h2>
        <a href={`/clarifications?venture_id=${venture.id}`}>Open clarification inbox for this venture</a>
      </div>

      {postmortem && <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Postmortem</h2>
        <div className="muted">Postmortem #{postmortem.id} · {new Date(postmortem.created_at).toLocaleString()}</div>
        <h3>Lessons</h3>
        <pre style={{ whiteSpace: "pre-wrap" }}>{postmortem.lessons_md}</pre>
        <h3>Full narrative</h3>
        <pre style={{ whiteSpace: "pre-wrap" }}>{postmortem.content_md}</pre>
      </div>}

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Charter</h2>
        <pre style={{ whiteSpace: "pre-wrap" }}>{venture.charter}</pre>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Venture pod plan</h2>
        {!plan ? <div className="muted">No CTO/Engineer plan yet. The venture pod runs every 30 minutes.</div> : (
          <>
            <div className="grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
              <div><h3>30 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_30}</pre></div>
              <div><h3>60 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_60}</pre></div>
              <div><h3>90 days</h3><pre style={{ whiteSpace: "pre-wrap" }}>{plan.plan_90}</pre></div>
            </div>
            <h3>First-30-day tasks</h3>
            {tasks.length === 0 ? <div className="muted">No tasks yet.</div> : (
              <table>
                <thead><tr><th>Task</th><th>Status</th><th>Approval</th><th>Description</th></tr></thead>
                <tbody>
                  {tasks.map((t: any) => (
                    <tr key={t.id}>
                      <td>{t.title}</td>
                      <td><span className="pill">{t.status}</span></td>
                      <td>{t.needs_approval ? <span className="pill">{t.approval_action} #{t.approval_id}</span> : "—"}</td>
                      <td>{t.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Board votes</h2>
        <table>
          <thead><tr><th>Partner</th><th>Vote</th><th>Rationale</th></tr></thead>
          <tbody>
            {reviews.map((r: any) => (
              <tr key={r.id}>
                <td>{r.persona}</td>
                <td><span className="pill" style={{ color: VOTE_COLOR[r.vote] || "inherit" }}>{r.vote.toUpperCase()}</span></td>
                <td>{r.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {memo && <div className="card"><h2 style={{ marginTop: 0 }}>Investment memo</h2><pre style={{ whiteSpace: "pre-wrap" }}>{memo.content}</pre></div>}
      {idea && <div className="card"><h2 style={{ marginTop: 0 }}>Origin idea</h2><div><strong>{idea.title}</strong>{idea.score != null && <span className="muted"> (score {idea.score.toFixed(2)})</span>}</div>{idea.summary && <div className="muted" style={{ marginTop: 6 }}>{idea.summary}</div>}</div>}
    </>
  );
}


function BudgetPill({ label, value, cap, precision }: { label: string; value: number; cap: number; precision: number }) {
  const pct = cap > 0 ? value / cap : 1;
  const color = pct > 0.9 ? "#ff6b6b" : pct > 0.7 ? "#ffd166" : "#7ee787";
  return (
    <div className="pill" style={{ color }}>
      {label}: ${value.toFixed(precision)} / ${cap.toFixed(precision)} ({Math.round(pct * 100)}%)
    </div>
  );
}
