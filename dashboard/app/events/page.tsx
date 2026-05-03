"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function EventsPage() {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    api.events().then(setEvents);
  }, []);
  return (
    <>
      <h1>Activity log</h1>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Actor</th>
            <th>Kind</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="muted">{new Date(e.created_at).toLocaleString()}</td>
              <td>{e.actor}</td>
              <td>
                <span className="pill">{e.kind}</span>
              </td>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
