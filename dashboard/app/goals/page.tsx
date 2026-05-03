"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function GoalsPage() {
  const [goals, setGoals] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  async function load() {
    setGoals(await api.goals());
  }

  useEffect(() => {
    load();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    await api.createGoal(title, description);
    setTitle("");
    setDescription("");
    load();
  }

  return (
    <>
      <h1>Goals</h1>
      <form onSubmit={submit} className="card">
        <div style={{ display: "grid", gap: 8 }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Goal title" />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does success look like? Constraints, budget, timeline."
            rows={4}
          />
          <button type="submit" style={{ alignSelf: "flex-start" }}>
            Create goal
          </button>
        </div>
      </form>

      <table>
        <thead>
          <tr>
            <th>Created</th>
            <th>Title</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {goals.map((g) => (
            <tr key={g.id}>
              <td className="muted">{new Date(g.created_at).toLocaleString()}</td>
              <td>
                <strong>{g.title}</strong>
                {g.description && <div className="muted">{g.description}</div>}
              </td>
              <td>
                <span className="pill">{g.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
