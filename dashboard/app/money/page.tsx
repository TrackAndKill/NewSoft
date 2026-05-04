import { api } from "@/lib/api";

export default async function MoneyPage() {
  const [status, transactions] = await Promise.all([api.moneyStatus(), api.moneyTransactions()]);
  return (
    <>
      <h1>Money</h1>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Spend guardrails</h2>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <span className="pill">today ${Number(status.spend_today_usd || 0).toFixed(2)} / ${Number(status.daily_cap_usd || 0).toFixed(2)}</span>
          <span className="pill">per-action cap ${Number(status.per_action_cap_usd || 0).toFixed(2)}</span>
          <span className="pill">dry-run {String(status.dry_run)}</span>
        </div>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Transactions</h2>
        {transactions.length === 0 ? <div className="muted">No money transactions yet.</div> : (
          <table>
            <thead><tr><th>Date</th><th>Action</th><th>Amount</th><th>Vendor</th><th>Status</th><th>Approval</th></tr></thead>
            <tbody>
              {transactions.map((tx: any) => (
                <tr key={tx.id}>
                  <td>{new Date(tx.created_at).toLocaleString()}</td>
                  <td>{tx.action}</td>
                  <td>${Number(tx.amount_usd || 0).toFixed(2)}</td>
                  <td>{tx.vendor}</td>
                  <td><span className="pill">{tx.status}</span></td>
                  <td>{tx.approval_id ? `#${tx.approval_id}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
