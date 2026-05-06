"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

function shortHash(value: string) {
  return value ? `${value.slice(0, 10)}…` : "";
}

export default function SuppressionsPage() {
  const [data, setData] = useState<any>({ items: [], domain_blocks: [], breakdown: {} });
  const [email, setEmail] = useState("");
  const [domain, setDomain] = useState("");
  const [reason, setReason] = useState("manual");
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const next = await api.suppressions(filter || undefined);
    setData(next);
  }

  useEffect(() => {
    refresh().catch((err) => setError(String(err)));
  }, [filter]);

  async function submitEmail(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.createSuppression(email, reason, note);
      setEmail("");
      setNote("");
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function submitDomain(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.createDomainBlock(domain, reason, note);
      setDomain("");
      setNote("");
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <>
      <h1>Suppressions</h1>
      <p className="muted">Manual and webhook-created blocks. Raw emails are never shown here; only hashes and domains.</p>
      {error ? <div className="card" style={{ borderColor: "#ff6b6b" }}>{error}</div> : null}

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Breakdown</h2>
        <div className="row" style={{ flexWrap: "wrap" }}>
          {Object.entries(data.breakdown || {}).map(([k, v]) => (
            <span className="pill" key={k}>{k}: {String(v)}</span>
          ))}
          {Object.keys(data.breakdown || {}).length === 0 ? <span className="muted">No suppressions yet.</span> : null}
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Add suppression</h2>
        <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
          <form onSubmit={submitEmail} className="row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <input placeholder="recipient@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            <input placeholder="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
            <input placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            <button>Suppress email</button>
          </form>
          <form onSubmit={submitDomain} className="row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <input placeholder="example.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
            <button>Block domain</button>
          </form>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ marginTop: 0 }}>Recipients</h2>
          <input placeholder="filter reason" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <table>
          <thead><tr><th>Created</th><th>Hash</th><th>Domain</th><th>Reason</th><th>Source</th></tr></thead>
          <tbody>
            {(data.items || []).map((row: any) => (
              <tr key={row.id}>
                <td className="muted">{new Date(row.created_at).toLocaleString()}</td>
                <td><code>{shortHash(row.recipient_hash)}</code></td>
                <td>{row.recipient_domain}</td>
                <td><span className="pill">{row.reason}</span></td>
                <td>{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Domain blocks</h2>
        <table>
          <thead><tr><th>Created</th><th>Domain</th><th>Reason</th><th>Source</th></tr></thead>
          <tbody>
            {(data.domain_blocks || []).map((row: any) => (
              <tr key={row.id}>
                <td className="muted">{new Date(row.created_at).toLocaleString()}</td>
                <td>{row.domain}</td>
                <td><span className="pill">{row.reason}</span></td>
                <td>{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
