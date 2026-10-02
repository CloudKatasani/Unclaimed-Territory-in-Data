import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, useUser, type Json } from "../api";
import { BarChart } from "../components/BarChart";
import { Certificate } from "../components/Certificate";
import { DataTable, Pill, Section } from "../components/ui";

export function Ask() {
  const { user } = useUser();
  const suggested = useApi<string[]>(["suggested"], "/suggested-questions");
  const inbox = useApi<Json>(["inbox", user], `/inbox?user=${user}`);
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(text: string) {
    setQ(text);
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api("/ask", { method: "POST", body: { question: text }, user }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const chartable = answer && answer.rows?.length > 1 && answer.plan?.metrics?.length === 1 && answer.plan?.dimensions?.length === 1;

  return (
    <div className="space-y-4">
      {(inbox.data?.open ?? 0) > 0 && (
        <Link to={`/inbox?user=${user}`} className="block rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900" data-testid="recall-banner">
          {inbox.data.open} of your past answers {inbox.data.open === 1 ? "was" : "were"} recalled — open your inbox to see what changed.
        </Link>
      )}
      <form
        className="card p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) ask(q.trim());
        }}
      >
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-base focus:border-accent focus:outline-none"
            placeholder="Ask a question about your operations…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="ask-input"
          />
          <button className="btn-primary" disabled={busy || !q.trim()}>
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {(suggested.data ?? []).map((s) => (
            <button type="button" key={s} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:border-accent hover:text-accent" onClick={() => ask(s)}>
              {s}
            </button>
          ))}
        </div>
      </form>

      {error && <div className="card border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>}

      {answer && answer.outcome === "missing_data" && (
        <div className="card p-5" data-testid="missing">
          <div className="text-lg font-semibold">I can't answer this yet.</div>
          <p className="mt-1 text-sm text-slate-600">We've logged this — you'll be notified when it can be answered.</p>
          {answer.unmatched?.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-slate-500">Not yet covered:</span>
              {answer.unmatched.map((u: string) => (
                <Pill key={u}>{u}</Pill>
              ))}
            </div>
          )}
        </div>
      )}

      {answer && answer.outcome !== "missing_data" && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
          <div className="min-w-0 space-y-4">
            <Section
              title="Answer"
              right={
                <span className="text-xs text-slate-500">
                  {answer.plan.product} · confidence {(answer.confidence * 100).toFixed(0)}%
                  {answer.outcome === "low_confidence" && " · low confidence"}
                </span>
              }
            >
              {answer.withheld ? (
                <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{answer.message}</div>
              ) : (
                <div className="space-y-4">
                  {chartable && <BarChart rows={answer.rows} dim={answer.plan.dimensions[0]} measure={answer.plan.metrics[0]} />}
                  <DataTable columns={answer.columns} rows={answer.rows} />
                </div>
              )}
            </Section>
            <details className="card p-3 text-sm">
              <summary className="cursor-pointer text-slate-600">Governed SQL (after policy rewrite)</summary>
              <pre className="code mt-2">{answer.sql}</pre>
            </details>
          </div>
          {answer.certificate && <Certificate cert={answer.certificate} />}
        </div>
      )}
    </div>
  );
}
