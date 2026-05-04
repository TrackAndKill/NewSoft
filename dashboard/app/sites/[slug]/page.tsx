import Link from "next/link";
import { api } from "@/lib/api";

export default async function SitePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const data = await api.site(slug);
  const { site, venture, content, leads = [] } = data;
  const liveUrl = site.domain ? `https://${site.domain}` : null;
  return (
    <>
      <h1>{site.slug}</h1>
      <div className="row" style={{ gap: 8 }}>
        <span className="pill">{site.status}</span>
        {venture && <Link href={`/ventures/${venture.slug}`}>{venture.name}</Link>}
        {liveUrl && <a href={liveUrl} target="_blank">Live site</a>}
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Deployment</h2>
        <table><tbody>
          <tr><th>Domain</th><td>{site.domain || "—"}</td></tr>
          <tr><th>Deploy dir</th><td><code>{site.deploy_dir}</code></td></tr>
          <tr><th>Deployed</th><td>{site.deployed_at ? new Date(site.deployed_at).toLocaleString() : "—"}</td></tr>
          <tr><th>Last error</th><td>{site.last_error || "—"}</td></tr>
          <tr><th>Signup URL</th><td><code>{`/api/public/sites/${site.slug}/signup`}</code></td></tr>
        </tbody></table>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Leads ({leads.length})</h2>
        {leads.length === 0 ? <div className="muted">No leads yet.</div> : (
          <table><thead><tr><th>Email</th><th>Source</th><th>IP hash</th><th>Created</th></tr></thead><tbody>
            {leads.map((l: any) => <tr key={l.id}><td>{l.email}</td><td>{l.source}</td><td><code>{String(l.ip_hash).slice(0,12)}…</code></td><td>{new Date(l.created_at).toLocaleString()}</td></tr>)}
          </tbody></table>
        )}
      </div>
      {content && <div className="card"><h2 style={{ marginTop: 0 }}>Content</h2><div className="muted">Latest landing page: {content.html?.length || 0} chars</div></div>}
    </>
  );
}
