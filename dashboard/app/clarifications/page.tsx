"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function statusColor(status: string) {
  if (status === "open") return "#ffd166";
  if (status === "answered") return "#7ee787";
  if (status === "dismissed") return "#8b949e";
  return "inherit";
}

export default function ClarificationsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [status, setStatus] = useState<string>("open");

  async function load(nextStatus = status) {
    setItems(await api.clarifications(nextStatus || undefined));
  }

  useEffect(() => {
    load(status);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  async function answer(id: number) {
    const text = (answers[id] || "").trim();
    if (!text) return;
    await api.answerClarification(id, text);
    setAnswers((prev) => ({ ...prev, [id]: "" }));
    await load();
  }

  async function dismiss(id: number) {
    await api.dismissClarification(id);
    await load();
  }

  return (
    <>
      <h1>Clarifications</h1>
      <p className="muted">Async operator questions from agents. Answers are indexed into memory for future runs.</p>
      <div className="row" style={{ marginBottom: 16 }}>
        {[
          ["open", "Open"],
          ["answered", "Answered"],
          ["dismissed", "Dismissed"],
          ["", "All"],
        ].map(([value, label]) => (
          <button key={value || "all"} onClick={() => setStatus(value)} disabled={status === value}>
            {label}
          </button>
        ))}
      </div>
      {items.length === 0 && <div className="muted">No clarifications.</div>}
      {items.map((item) => (
        <div key={item.id} className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <strong>#{item.id} · {item.asked_by_agent}</strong>
              <div className="muted">{new Date(item.created_at).toLocaleString()} · priority {item.priority}</div>
              {item.venture_id && <a href={`/clarifications?venture_id=${item.venture_id}`}>venture #{item.venture_id}</a>}
            </div>
            <span className="pill" style={{ color: statusColor(item.status) }}>{item.status}</span>
          </div>
          <h3>Question</h3>
          <pre style={{ whiteSpace: "pre-wrap" }}>{item.question_md}</pre>
          {item.context_md && <><h3>Context</h3><pre style={{ whiteSpace: "pre-wrap" }}>{item.context_md}</pre></>}
          {item.answer_md ? (
            <>
              <h3>Answer</h3>
              <pre style={{ whiteSpace: "pre-wrap" }}>{item.answer_md}</pre>
              <div className="muted">answered by {item.answered_by || "operator"} {item.answered_at ? new Date(item.answered_at).toLocaleString() : ""}</div>
            </>
          ) : item.status === "open" ? (
            <div style={{ marginTop: 12 }}>
              <textarea
                value={answers[item.id] || ""}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [item.id]: e.target.value }))}
                placeholder="Answer for future agent memory…"
                style={{ width: "100%", minHeight: 100 }}
              />
              <div className="row" style={{ marginTop: 8 }}>
                <button onClick={() => answer(item.id)}>Answer + index</button>
                <button className="danger" onClick={() => dismiss(item.id)}>Dismiss</button>
              </div>
            </div>
          ) : null}
        </div>
      ))}
    </>
  );
}
