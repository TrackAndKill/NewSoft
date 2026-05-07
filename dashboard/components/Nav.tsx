"use client";

import { usePathname } from "next/navigation";

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

export default function Nav() {
  const pathname = usePathname() || "/";
  return (
    <nav>
      {NAV.map(([href, label]) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <a key={href} href={href} className={active ? "active" : ""}>
            {label}
          </a>
        );
      })}
    </nav>
  );
}
