import Link from "next/link";
import { api } from "@/lib/api";

export default async function SitesPage() {
  const sites = await api.sites();
  return (
    <>
      <h1>Sites</h1>
      <p className="muted">Venture landing pages, deployment state, and captured leads.</p>
      <div className="card">
        {sites.length === 0 ? <div className="muted">No sites yet.</div> : (
          <table>
            <thead><tr><th>Site</th><th>Status</th><th>Domain</th><th>Leads</th><th>Deployed</th></tr></thead>
            <tbody>
              {sites.map((s: any) => (
                <tr key={s.id}>
                  <td><Link href={`/sites/${s.slug}`}>{s.slug}</Link></td>
                  <td><span className="pill">{s.status}</span></td>
                  <td>{s.domain ? <a href={`https://${s.domain}`} target="_blank">{s.domain}</a> : "—"}</td>
                  <td>{s.lead_count ?? 0}</td>
                  <td>{s.deployed_at ? new Date(s.deployed_at).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
