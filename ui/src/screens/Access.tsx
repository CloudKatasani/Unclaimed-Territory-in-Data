import { fmt, useApi, useUser, type Json } from "../api";
import { Empty, Pill, Section } from "../components/ui";

export function Access() {
  const { user } = useUser();
  const bundles = useApi<Json[]>(["access", user], `/access?user=${user}`);
  return (
    <div className="space-y-4">
      {(bundles.data ?? []).length === 0 && <Empty>No subscriptions yet. Subscribe to an agent or decision pack in the marketplace.</Empty>}
      {(bundles.data ?? []).map((b) => (
        <Section key={b.bundle_id} title={`Bundle · ${b.listing_id}`} right={<Pill tone={b.status === "active" ? "accent" : "slate"}>{b.status}</Pill>}>
          {b.missing?.length > 0 && <div className="mb-2 text-sm text-amber-700">Missing grants: {b.missing.map((m: Json) => `${m.fqn} (${m.reason})`).join(", ")}</div>}
          <table className="data">
            <thead><tr><th>data product</th><th>scope</th><th>purpose</th><th>expires</th><th>status</th></tr></thead>
            <tbody>
              {b.leases.map((l: Json) => (
                <tr key={l.lease_id}>
                  <td className="font-mono text-xs">{l.fqn}</td>
                  <td>{l.scope_json.row_filter ?? "all rows"}</td>
                  <td>{l.purpose}</td>
                  <td>{l.expires_at.slice(0, 10)}</td>
                  <td>{l.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {b.budget && b.budget.agent && (
            <div className="mt-3">
              <div className="label mb-1">Budget · {b.budget.agent}</div>
              <div className="h-2 w-full rounded bg-slate-100">
                <div className="h-2 rounded bg-accent" style={{ width: `${Math.min(100, (b.budget.runs_used / Math.max(1, b.budget.max_runs_per_day)) * 100)}%` }} />
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {fmt(b.budget.runs_used)} / {fmt(b.budget.max_runs_per_day)} runs today · {fmt(b.budget.credits_used)} credits used · max {fmt(b.budget.max_credits_per_run)} per run
              </div>
            </div>
          )}
        </Section>
      ))}
    </div>
  );
}
