import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NewSoft",
  description: "Autonomous firm dashboard",
};

const NAV: Array<[string, string]> = [
  ["/", "Overview"],
  ["/goals", "Goals"],
  ["/ideas", "Ideas"],
  ["/memos", "Memos"],
  ["/board", "Board"],
  ["/ventures", "Ventures"],
  ["/experiments", "Experiments"],
  ["/memory", "Memory"],
  ["/postmortems", "Postmortems"],
  ["/approvals", "Approvals"],
  ["/suppressions", "Suppressions"],
  ["/outreach", "Outreach"],
  ["/clarifications", "Clarifications"],
  ["/money", "Money"],
  ["/events", "Activity"],
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <span className="brand">
            <span className="brand-dot" aria-hidden />
            NewSoft
          </span>
          <nav>
            {NAV.map(([href, label]) => (
              <a key={href} href={href}>{label}</a>
            ))}
          </nav>
        </header>
        <main style={{ padding: "28px 24px 80px", maxWidth: 1200, margin: "0 auto" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
