import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmt, useAction, useApi, useUser, type Json } from "../api";
import { Empty, PassFail, Pill, Section } from "../components/ui";

const STEPS = ["drafted", "generating", "testing", "gate", "published"];

function Timeline({ timeline }: { timeline: Json[] }) {
  const seen = new Set(timeline.map((t) => t.status));
  return (
    <div className="flex items-center gap-1 text-xs">
      {STEPS.map((s, i) => (
        <span key={s} className="flex items-center gap-1">
          <span className={`rounded-full px-2 py-0.5 ${seen.has(s) ? "bg-accent text-white" : "bg-slate-100 text-slate-500"}`}>{s}</span>
          {i < STEPS.length - 1 && <span className="text-slate-300">—</span>}
        </span>
      ))}
      {seen.has("repairing") && <Pill>repaired</Pill>}
      {seen.has("failed") && <PassFail ok={false} label="failed" />}
    </div>
  );
}

function BuildDetail({ id }: { id: string }) {
  const b = useApi<Json>(["build", id], `/builds/${id}`);
  if (!b.data) return null;
  const j = b.data;
  return (
    <div className="space-y-4">
      <Section title="Generated SQL (read-only)">
        <pre className="code max-h-80">{j.sql}</pre>
      </Section>
      <Section title={`Contract-derived tests (${(j.tests_json ?? []).length})`}>
        <table className="data">
          <thead>
            <tr><th>test</th><th>kind</th><th>result</th><th>detail</th></tr>
          </thead>
          <tbody>
            {(j.tests_json ?? []).map((t: Json) => (
              <tr key={t.name}>
                <td className="font-mono text-xs">{t.name}</td>
                <td>{t.kind}</td>
                <td><PassFail ok={t.passed} /></td>
                <td className="text-xs text-slate-500">{t.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title={`Attempts (${(j.attempts_json ?? []).length})`}>
        {(j.attempts_json ?? []).map((a: Json) => (
          <div key={a.attempt} className="mb-3">
            <div className="flex items-center gap-2 text-sm">
              <b>Attempt {a.attempt + 1}</b> <PassFail ok={a.passed} /> <span className="text-xs text-slate-500">{a.attempt === 0 ? "build.model" : "build.repair"}</span>
            </div>
            {a.failures.length > 0 && (
              <ul className="ml-4 list-disc text-xs text-rose-700">
                {a.failures.map((f: Json) => (
                  <li key={f.name}>{f.name}: {f.detail}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </Section>
      <Section title="Provenance">
        <pre className="code max-h-96">{JSON.stringify(j.provenance, null, 2)}</pre>
      </Section>
    </div>
  );
}

function Migrations() {
  const { user } = useUser();
  const migs = useApi<Json[]>(["migrations"], "/migrations");
  const consumers = useApi<Json[]>(["consumers"], "/consumers");
  const acceptAll = useAction((fqn: string) => api(`/migrations/accept-all?fqn=${fqn}`, { method: "POST", user }));
  const accept = useAction((id: string) => api(`/migrations/${id}/accept`, { method: "POST", user }));
  const reject = useAction((id: string) => api(`/migrations/${id}/reject`, { method: "POST", user }));
  const runSaved = useAction((id: string) => api(`/consumers/${id}/run`, { method: "POST", user: "alice" }));
  const fqns = [...new Set((migs.data ?? []).map((m) => m.product_fqn))];
  return (
    <div className="space-y-4">
      {(migs.data ?? []).length === 0 && <Empty>No breaking changes published. Consumers registered: {(consumers.data ?? []).length}.</Empty>}
      {fqns.map((fqn) => {
        const list = (migs.data ?? []).filter((m) => m.product_fqn === fqn);
        return (
          <Section key={fqn} title={`Consumer migrations · ${fqn} v${list[0].from_version} → v${list[0].to_version}`}
            right={list.some((m) => m.status === "proposed") && (user === "raj" || user === "admin") ? (
              <button className="btn-primary" onClick={() => acceptAll.mutate(fqn)}>Accept all</button>
            ) : undefined}>
            <div className="space-y-3">
              {list.map((m) => (
                <div key={m.migration_id} className="rounded-md border border-slate-200 p-3" data-testid="migration">
                  <div className="flex items-center justify-between">
                    <div className="font-mono text-sm">{m.consumer_id}</div>
                    <div className="flex items-center gap-2">
                      <PassFail ok={m.divergence === 0} label={`shadow divergence ${fmt(m.divergence)}`} />
                      <Pill>{m.status}</Pill>
                      {m.status === "proposed" && (user === "raj" || user === "admin") && (
                        <>
                          <button className="btn" onClick={() => accept.mutate(m.migration_id)}>Accept</button>
                          <button className="btn" onClick={() => reject.mutate(m.migration_id)}>Hold on v{m.from_version}</button>
                        </>
                      )}
                      {m.consumer_id.startsWith("saved:") && (
                        <button className="btn" onClick={() => runSaved.mutate(m.consumer_id)}>Run saved question</button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
                    <pre className="code max-h-40 whitespace-pre-wrap">{m.old_text}</pre>
                    <pre className="code max-h-40 whitespace-pre-wrap">{m.new_text}</pre>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">Shadow run: {m.shadow_json.old_rows} rows on v{m.from_version} vs {m.shadow_json.new_rows} rows on v{m.to_version}</div>
                </div>
              ))}
              {runSaved.data && <div className="text-sm text-emerald-700">Alice's saved question answered with {runSaved.data.rows.length} rows · certificate {runSaved.data.certificate?.verdict}</div>}
              {runSaved.error && <div className="text-sm text-rose-700">{String(runSaved.error.message)}</div>}
            </div>
          </Section>
        );
      })}
    </div>
  );
}

export function Builds() {
  const builds = useApi<Json[]>(["builds"], "/builds");
  const [params] = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") ?? "builds");
  const [sel, setSel] = useState<string | null>(params.get("job"));
  const current = sel ?? builds.data?.[0]?.job_id ?? null;
  const tabs = (
    <div className="mb-4 flex gap-2">
      {["builds", "migrations"].map((t) => (
        <button key={t} className={`btn ${tab === t ? "border-accent text-accent" : ""}`} onClick={() => setTab(t)}>{t === "builds" ? "Build jobs" : "Consumer migrations"}</button>
      ))}
    </div>
  );
  if (tab === "migrations") return <div>{tabs}<Migrations /></div>;
  return (
    <div>
    {tabs}
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_1fr]">
      <Section title="Build jobs">
        {builds.data?.length ? (
          <ul className="space-y-2">
            {builds.data.map((b) => (
              <li key={b.job_id}>
                <button className={`w-full rounded-md border p-2 text-left ${current === b.job_id ? "border-accent bg-accent-soft" : "border-slate-200"}`} onClick={() => setSel(b.job_id)}>
                  <div className="font-mono text-sm">{b.product_fqn}</div>
                  <div className="mt-1"><Timeline timeline={b.timeline_json} /></div>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No builds yet. Approve a contract on the Demand board.</Empty>
        )}
      </Section>
      <div className="min-w-0">{current && <BuildDetail id={current} />}</div>
    </div>
    </div>
  );
}
