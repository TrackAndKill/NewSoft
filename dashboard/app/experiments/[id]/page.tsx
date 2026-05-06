import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ExperimentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await api.experiment(id);
  const outreach = await api.outreach(id);
  const exp = data.experiment;
  return (
    <>
      <h1>Experiment #{exp.id}</h1>
      <p className="muted">Memo #{exp.memo_id} · current stage {exp.current_stage || "n/a"} · status {exp.status}</p>
      {(data.stages || []).map((st: any) => (
        <div key={st.id} className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>Stage {st.stage_index}: {st.stage_name}</h2>
            <span className="pill">{st.status}</span>
          </div>
          {st.approval_id && <p><a href="/approvals">Approval #{st.approval_id}</a></p>}
          {st.result_md ? <pre style={{ whiteSpace: "pre-wrap" }}>{st.result_md}</pre> : <p className="muted">No result yet.</p>}
          <details><summary>Design JSON</summary><pre>{JSON.stringify(st.design_json, null, 2)}</pre></details>
          {st.result_json && <details><summary>Result JSON</summary><pre>{JSON.stringify(st.result_json, null, 2)}</pre></details>}
        </div>
      ))}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Outreach sends</h2>
          <a href={`/outreach?experiment_id=${exp.id}`}>Open outreach view</a>
        </div>
        <table>
          <thead><tr><th>Created</th><th>Hash</th><th>Domain</th><th>Subject</th><th>Status</th></tr></thead>
          <tbody>
            {(outreach.items || []).map((row: any) => (
              <tr key={row.id}>
                <td className="muted">{new Date(row.created_at).toLocaleString()}</td>
                <td><code>{row.recipient_hash?.slice(0, 10)}…</code></td>
                <td>{row.recipient_domain}</td>
                <td>{row.subject}</td>
                <td><span className="pill">{row.status}</span>{row.dry_run ? <span className="pill">dry-run</span> : null}</td>
              </tr>
            ))}
            {(outreach.items || []).length === 0 ? <tr><td className="muted">No outreach sends for this experiment.</td><td /><td /><td /><td /></tr> : null}
          </tbody>
        </table>
      </div>
    </>
  );
}
