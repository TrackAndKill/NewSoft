import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "NewSoft",
  description: "Autonomous firm dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <span className="brand">
            <span className="brand-dot" aria-hidden />
            NewSoft
          </span>
          <Nav />
        </header>
        <main style={{ padding: "32px 24px 80px", maxWidth: 1200, margin: "0 auto" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
