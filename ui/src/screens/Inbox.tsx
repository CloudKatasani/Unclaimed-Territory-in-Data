import { useState } from "react";
import { api, fmt, useAction, useApi, useUser, type Json } from "../api";
import { DataTable, Empty, Pill, Section } from "../components/ui";

export function Inbox() {
  const { user } = useUser();
  const inbox = useApi<Json>(["inbox", user], `/inbox?user=${user}`);
  const exports = useApi<Json[]>(["exports", user], `/exports?user=${user}`);
  const [open, setOpen] = useState<string | null>(null);
  const ack = useAction((id: string) => api(`/inbox/${id}/ack`, { method: "POST", user }));
  return (
    <div className="space-y-4">
      <Section title="Recall notices">
        {(inbox.data?.notices ?? []).length === 0 && <Empty>No recalls. Every number you were served still stands.</Empty>}
        {(inbox.data?.notices ?? []).map((n: Json) => (
          <div key={n.notice_id} className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3" data-testid="recall-notice">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-medium text-amber-900">{n.message}</div>
              {n.acknowledged_at ? <Pill>acknowledged</Pill> : <button className="btn" onClick={() => ack.mutate(n.notice_id)}>Acknowledge</button>}
            </div>
            <div className="mt-1 text-xs text-amber-800">Reason: {n.reason}</div>
            <button className="mt-2 text-xs text-accent underline" onClick={() => setOpen(open === n.notice_id ? null : n.notice_id)}>
              {open === n.notice_id ? "Hide" : "Open original answer"}
            </button>
            {open === n.notice_id && n.original && (
              <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                  <div className="label mb-1">As served {n.original.served_at.slice(0, 10)}</div>
                  <DataTable columns={Object.keys(n.original.result_json[0] ?? {})} rows={n.original.result_json} />
                </div>
                <div>
                  <div className="label mb-1">Changed cells</div>
                  <table className="data">
                    <thead><tr><th>where</th><th>metric</th><th>old</th><th>new</th><th>Δ%</th></tr></thead>
                    <tbody>
                      {n.delta_json.cells.map((c: Json, i: number) => (
                        <tr key={i}><td>{Object.values(c.key).join(" · ")}</td><td>{c.metric}</td><td>{fmt(c.old)}</td><td>{fmt(c.new)}</td><td>{fmt(c.pct)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        ))}
      </Section>
      <Section title="My exports and answers">
        <table className="data">
          <thead><tr><th>served</th><th>question</th><th>kind</th><th>status</th></tr></thead>
          <tbody>
            {(exports.data ?? []).map((e) => (
              <tr key={e.cert_id}>
                <td>{e.served_at.slice(0, 16).replace("T", " ")}</td>
                <td>{e.question}</td>
                <td>{e.kind}</td>
                <td>{e.restated ? <span className="font-semibold text-amber-700">Restated</span> : "current"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
