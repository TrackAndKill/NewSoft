"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function MemoryPage() {
  const [query, setQuery] = useState("validation under $200");
  const [status, setStatus] = useState<any>(null);
  const [results, setResults] = useState<any[]>([]);
  async function runSearch(q = query) { setResults(await api.memorySearch(q, undefined, 10)); }
  useEffect(() => { api.memoryStatus().then(setStatus).catch(console.error); runSearch().catch(console.error); }, []);
  return (
    <>
      <h1>Memory</h1>
      <p className="muted">Vector memory is tool-called by agents; this page lets the operator search the same index.</p>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <strong>Provider: {status?.provider || "voyage"}</strong>
            <div className="muted">Embeddings: {status?.embeddings_available ? "configured" : "not configured — graceful empty results"}</div>
            <pre>{JSON.stringify(status?.counts || {}, null, 2)}</pre>
          </div>
          <form onSubmit={(e) => { e.preventDefault(); runSearch(); }} className="row">
            <input value={query} onChange={(e) => setQuery(e.target.value)} style={{ minWidth: 320 }} />
            <button type="submit">Search</button>
          </form>
        </div>
      </div>
      {results.length === 0 && <div className="muted">No memory results. If no embedding key is configured, this is expected.</div>}
      {results.map((r, i) => (
        <div key={`${r.source_kind}-${r.source_id}-${r.chunk_index}-${i}`} className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{r.source_kind} #{r.source_id}</strong>
            <span className="pill">distance {Number(r.distance).toFixed(4)}</span>
          </div>
          <p style={{ whiteSpace: "pre-wrap" }}>{r.content_chunk}</p>
        </div>
      ))}
    </>
  );
}
