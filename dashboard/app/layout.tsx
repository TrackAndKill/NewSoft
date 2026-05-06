import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NewSoft",
  description: "Autonomous firm dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header style={{ padding: "16px 24px", borderBottom: "1px solid #222", display: "flex", gap: 24, flexWrap: "wrap" }}>
          <strong>NewSoft</strong>
          <a href="/">Overview</a>
          <a href="/goals">Goals</a>
          <a href="/ideas">Ideas</a>
          <a href="/memos">Memos</a>
          <a href="/board">Board</a>
          <a href="/ventures">Ventures</a>
          <a href="/experiments">Experiments</a>
          <a href="/memory">Memory</a>
          <a href="/postmortems">Postmortems</a>
          <a href="/approvals">Approvals</a>
          <a href="/suppressions">Suppressions</a>
          <a href="/outreach">Outreach</a>
          <a href="/clarifications">Clarifications</a>
          <a href="/money">Money</a>
          <a href="/events">Activity</a>
        </header>
        <main style={{ padding: 24, maxWidth: 1100, margin: "0 auto" }}>{children}</main>
      </body>
    </html>
  );
}
