import { api, useAction, useApi, type Json } from "../api";
import { PassFail, Section } from "../components/ui";

export function Truth() {
  const s = useApi<Json>(["sentinels"], "/sentinels");
  const verify = useAction(() => api("/sentinels/verify", { method: "POST" }));
  const m = s.data?.matrix;
  return (
    <div className="space-y-4">
      <Section
        title="Pipeline truth — sentinel records"
        right={<button className="btn" disabled={verify.isPending} onClick={() => verify.mutate()}>Re-verify now</button>}
      >
        <p className="mb-3 text-sm text-slate-600">
          Tracer rows with reserved <span className="font-mono">SNTL-</span> keys run through every pipeline next to real data. Each product must contain exactly the rows its
          own SQL produces over the sentinels in isolation. Sentinels never reach a served answer.
        </p>
        <div className="mb-3 flex flex-wrap gap-2 text-xs">
          {Object.entries((s.data?.status ?? {}) as Record<string, Json>).map(([fqn, st]) => (
            <span key={fqn} className="flex items-center gap-1 font-mono">{fqn} <PassFail ok={st.passed} label={st.passed ? `${st.checked} rows match` : "mismatch"} /></span>
          ))}
        </div>
        {m && (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>record</th><th>source</th><th>values</th>
                  {m.products.map((p: string) => <th key={p}>{p.replace("dp.", "")}</th>)}
                </tr>
              </thead>
              <tbody>
                {m.records.map((r: Json) => (
                  <tr key={r.record_id}>
                    <td className="font-mono text-xs">{r.record_id}</td>
                    <td className="font-mono text-xs">{r.source.replace("raw.", "")}</td>
                    <td className="font-mono text-[11px] text-slate-600">
                      {r.row.meter_id ?? r.row.outage_id} {r.row.read_ts ?? `${r.row.start_ts} → ${r.row.end_ts}`}
                      {r.row.kwh_delivered !== undefined && ` · ${r.row.kwh_delivered} kWh`}
                    </td>
                    {m.products.map((p: string) => {
                      const c = r.cells[p];
                      if (!c) return <td key={p} className="text-slate-300">—</td>;
                      const title = c.rows.map((x: Json) => `expected ${JSON.stringify(x.expected)}\nactual   ${JSON.stringify(x.actual)}`).join("\n\n");
                      return (
                        <td key={p} title={title}>
                          <span className={`inline-block h-5 w-10 rounded ${c.passed ? "bg-emerald-400" : "bg-rose-500"}`} />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
