import { useState } from "react";
import { useApi, type Json } from "../api";
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

export function Builds() {
  const builds = useApi<Json[]>(["builds"], "/builds");
  const [sel, setSel] = useState<string | null>(null);
  const current = sel ?? builds.data?.[0]?.job_id ?? null;
  return (
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
  );
}
