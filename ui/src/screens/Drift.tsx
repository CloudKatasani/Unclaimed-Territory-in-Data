import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmt, useAction, useApi, useUser, type Json } from "../api";
import { Graph } from "../components/Graph";
import { Empty, PassFail, Pill, Section } from "../components/ui";

function Evidence({ obj }: { obj: Json }) {
  const cols = Object.entries((obj.columns ?? {}) as Record<string, Json>);
  if (!cols.length) return <div className="text-xs text-rose-700">{obj.error}</div>;
  return (
    <table className="data mt-1">
      <thead>
        <tr><th>column</th><th>baseline sum</th><th>candidate sum</th><th>null rate b/c</th><th>distinct b/c</th><th>max key diff</th></tr>
      </thead>
      <tbody>
        {cols.map(([c, m]) => (
          <tr key={c}>
            <td className="font-mono text-xs">{c}</td>
            <td>{fmt(m.baseline.sum)}</td>
            <td>{fmt(m.candidate.sum)}</td>
            <td>{fmt(m.baseline.null_rate)} / {fmt(m.candidate.null_rate)}</td>
            <td>{fmt(m.baseline.distinct)} / {fmt(m.candidate.distinct)}</td>
            <td>{fmt(m.max_key_abs_diff)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EventDetail({ id }: { id: string }) {
  const { user } = useUser();
  const d = useApi<Json>(["drift", id], `/drift/events/${id}`);
  const [drill, setDrill] = useState<string | null>(null);
  const patchId = d.data?.patch?.job_id;
  const approve = useAction(() => api(`/drift/patches/${patchId}/approve`, { method: "POST", user }));
  const reject = useAction(() => api(`/drift/patches/${patchId}/reject`, { method: "POST", user }));
  if (!d.data) return null;
  const ev = d.data.event;
  const radius = ev.blast_radius;
  const patch = d.data.patch;
  const products: string[] = (radius?.objects ?? []).filter((o: Json) => o.fqn.startsWith("dp.")).map((o: Json) => o.fqn);
  const nodes = [
    { id: radius?.source ?? ev.fqn, tone: "source", sub: "changed source" },
    ...(radius?.objects ?? []).map((o: Json) => ({ id: o.fqn, tone: `crit${o.criticality}`, sub: `criticality ${o.criticality} · ${o.kind}` })),
  ];
  return (
    <div className="space-y-4">
      <Section title="Change list">
        <ul className="space-y-1 text-sm">
          {(ev.changes as Json[]).map((c, i) => (
            <li key={i} className="flex items-center gap-2">
              <Pill tone="accent">{c.kind.replace("_", " ")}</Pill>
              {c.kind === "rename" ? (
                <span className="font-mono">{c.old_column} → {c.new_column} <span className="text-slate-500">(profile {fmt(c.profile_similarity)}, name {fmt(c.name_similarity)})</span></span>
              ) : (
                <span className="font-mono">{c.column}: {c.old_type} → {c.new_type}</span>
              )}
            </li>
          ))}
        </ul>
      </Section>
      {radius && (
        <Section title="Blast radius" right={<span className="flex gap-2 text-xs"><Pill>crit 4 regulatory</Pill><Pill>crit 3</Pill><Pill>crit 2</Pill></span>}>
          <Graph nodes={nodes} edges={radius.edges} />
        </Section>
      )}
      <Section title="Candidates (shadow-run, last 7 days vs last-known-good)">
        <div className="overflow-x-auto">
        <table className="data">
          <thead>
            <tr>
              <th>#</th><th>candidate</th><th>source</th><th>max divergence</th>
              {products.map((p) => <th key={p}>{p.replace("dp.", "")}</th>)}
              <th>gate</th>
            </tr>
          </thead>
          <tbody>
            {(d.data.candidates as Json[]).map((c) => {
              const r = c.result_json;
              return (
                <tr key={c.candidate_no} className={c.selected ? "bg-emerald-50/50" : ""}>
                  <td>{c.candidate_no}</td>
                  <td>
                    <div className="font-medium">{c.label}</div>
                    <div className="font-mono text-xs text-slate-500">{c.expression}</div>
                  </td>
                  <td>{c.source}</td>
                  <td>{r.max_divergence === null ? "—" : fmt(r.max_divergence)}</td>
                  {products.map((p) => {
                    const o = r.objects[p];
                    return (
                      <td key={p}>
                        <button className="text-left" onClick={() => setDrill(`${c.candidate_no}:${p}`)}>
                          <PassFail ok={o?.passed} label={o?.divergence === null || o?.divergence === undefined ? (o?.error?.startsWith("sentinel") ? "sentinel fail" : "build fail") : `${fmt(o.divergence)} ${o.passed ? "≤" : ">"} ${o.threshold}`} />
                        </button>
                      </td>
                    );
                  })}
                  <td>{c.selected ? <PassFail ok label="selected" /> : <PassFail ok={c.passed} label={c.passed ? "pass" : "rejected"} />}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        {drill && (() => {
          const [n, p] = drill.split(":");
          const c = (d.data.candidates as Json[]).find((x) => String(x.candidate_no) === n);
          const o = c?.result_json.objects[p];
          return (
            <div className="mt-3 rounded-md border border-slate-200 p-3">
              <div className="text-sm font-semibold">Evidence: candidate {n} on {p}</div>
              {o?.worst_metric && <div className="text-xs text-slate-500">worst metric: {o.worst_metric}</div>}
              <Evidence obj={o ?? {}} />
            </div>
          );
        })()}
      </Section>
      {patch && (
        <Section title="Patch">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Pill>status: {patch.status}</Pill>
            {patch.merged_products?.map((p: string) => <Pill key={p}>refreshed: {p}</Pill>)}
            {patch.held_products?.map((p: string) => <Pill key={p}>held: {p}</Pill>)}
            {patch.approved_by && <Pill>approved by {patch.approved_by}</Pill>}
          </div>
          {patch.status === "awaiting_approval" && (
            <div className="mt-3 flex items-center gap-2">
              {user === "raj" || user === "admin" ? (
                <>
                  <button className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}>Approve for criticality-4 products</button>
                  <button className="btn" onClick={() => reject.mutate()}>Reject</button>
                </>
              ) : (
                <span className="text-xs text-slate-500">A data engineer must approve the patch for regulator-reported products.</span>
              )}
            </div>
          )}
        </Section>
      )}
    </div>
  );
}

export function Drift() {
  const events = useApi<Json[]>(["drift-events"], "/drift/events");
  const simulate = useAction(() => api("/drift/simulate", { method: "POST" }));
  const [params] = useSearchParams();
  const [sel, setSel] = useState<string | null>(params.get("event"));
  const current = sel ?? events.data?.[0]?.event_id ?? null;
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_1fr]">
      <Section
        title="Drift events"
        right={<button className="btn" disabled={simulate.isPending} onClick={() => simulate.mutate()}>{simulate.isPending ? "Healing…" : "Simulate vendor change"}</button>}
      >
        {events.data?.length ? (
          <ul className="space-y-2">
            {events.data.map((e) => (
              <li key={e.event_id}>
                <button className={`w-full rounded-md border p-2 text-left text-sm ${current === e.event_id ? "border-accent bg-accent-soft" : "border-slate-200"}`} onClick={() => setSel(e.event_id)}>
                  <div className="font-mono">{e.fqn}</div>
                  <div className="text-xs text-slate-500">{(e.changes_json as Json[]).map((c) => c.kind).join(" + ")} · {e.status}</div>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No drift detected.</Empty>
        )}
      </Section>
      <div className="min-w-0">{current && <EventDetail id={current} />}</div>
    </div>
  );
}
