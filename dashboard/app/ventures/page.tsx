"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function VenturesPage() {
  const [ventures, setVentures] = useState<any[]>([]);
  useEffect(() => {
    api.ventures().then(setVentures);
  }, []);
  return (
    <>
      <h1>Ventures</h1>
      {ventures.length === 0 && (
        <div className="muted">
          No ventures yet. The CEO charters one when the Board votes FUND on a memo.
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>Created</th>
            <th>Name</th>
            <th>Slug</th>
            <th>Status</th>
            <th>Spend today</th>
          </tr>
        </thead>
        <tbody>
          {ventures.map((v) => (
            <tr key={v.id}>
              <td className="muted">{new Date(v.created_at).toLocaleDateString()}</td>
              <td>
                <a href={`/ventures/${v.slug}`}>
                  <strong>{v.name}</strong>
                </a>
              </td>
              <td className="muted">{v.slug}</td>
              <td>
                <span className="pill">{v.status}</span>
              </td>
              <td className="muted">LLM ${Number(v.llm_spend_today_usd || 0).toFixed(4)} · Money ${Number(v.money_spend_today_usd || 0).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
