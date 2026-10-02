import { useCallback, useEffect, useRef, useState } from "react";
import { api, useApi, type Json } from "../api";

export const CHANNEL = "tessera-conductor";

/** Demo conductor (docs/08): open in a second window with /conduct?conductor=1. */
export function Conductor() {
  const steps = useApi<Json[]>(["demo-steps"], "/demo/steps");
  const [busy, setBusy] = useState<number | "reset" | null>(null);
  const [current, setCurrent] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const channel = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    channel.current = new BroadcastChannel(CHANNEL);
    return () => channel.current?.close();
  }, []);
  useEffect(() => {
    const next = (steps.data ?? []).find((s) => !s.done);
    if (next) setCurrent(next.n);
  }, [steps.data]);

  const run = useCallback(
    async (n: number) => {
      setBusy(n);
      try {
        const r = await api(`/demo/steps/${n}/run`, { method: "POST" });
        channel.current?.postMessage({ navigate: r.navigate });
        setLog((l) => [`step ${n} → ${r.navigate}`, ...l].slice(0, 20));
        setCurrent(Math.min(12, n + 1));
        await steps.refetch();
      } catch (e) {
        setLog((l) => [`step ${n} failed: ${(e as Error).message}`, ...l]);
      } finally {
        setBusy(null);
      }
    },
    [steps],
  );
  const reset = useCallback(
    async (to?: number) => {
      setBusy("reset");
      try {
        await api(`/demo/reset${to !== undefined ? `?step=${to}` : ""}`, { method: "POST" });
        channel.current?.postMessage({ navigate: "/market?user=priya", reload: true });
        setLog((l) => [`reset${to !== undefined ? ` and replayed 0–${to}` : ""}`, ...l]);
        await steps.refetch();
      } finally {
        setBusy(null);
      }
    },
    [steps],
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (busy !== null || (e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "n") run(current);
      if (e.key === "r") reset();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, current, run, reset]);

  return (
    <div className="min-h-full bg-slate-900 p-6 text-slate-100">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-lg font-semibold">Tessera · demo conductor</div>
          <div className="text-xs text-slate-400">Keys: <b>n</b> run next step · <b>r</b> reset. The projector window follows along.</div>
        </div>
        <div className="flex gap-2">
          <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" disabled={busy !== null} onClick={() => reset()}>
            {busy === "reset" ? "Resetting…" : "Reset"}
          </button>
          <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" disabled={busy !== null} onClick={() => reset(current - 1)}>
            Reset to before step {current}
          </button>
        </div>
      </div>
      <ol className="space-y-2">
        {(steps.data ?? []).map((s) => (
          <li key={s.n} className={`rounded-lg border p-3 ${s.n === current ? "border-indigo-400 bg-slate-800" : "border-slate-700"}`} data-testid={`step-${s.n}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm">
                  <span className="mr-2 font-mono text-slate-400">{s.n}</span>
                  <b>{s.screen}</b> · {s.title} <span className="text-xs text-slate-400">({s.user}; idea {s.idea})</span>
                </div>
                <div className="mt-1 text-base italic text-indigo-200">“{s.talk}”</div>
              </div>
              <button className="shrink-0 rounded bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40" disabled={busy !== null} onClick={() => run(s.n)}>
                {busy === s.n ? "Running…" : s.done ? "Show" : "Run"}
              </button>
            </div>
          </li>
        ))}
      </ol>
      <pre className="mt-4 max-h-40 overflow-auto text-xs text-slate-400">{log.join("\n")}</pre>
    </div>
  );
}
