import type { ReactNode } from "react";

const VERDICT: Record<string, { label: string; cls: string }> = {
  release: { label: "Release", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  release_with_warning: { label: "Release with warning", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  block: { label: "Blocked", cls: "bg-rose-50 text-rose-700 border-rose-200" },
};

export function Verdict({ verdict }: { verdict: string }) {
  const v = VERDICT[verdict] ?? { label: verdict, cls: "bg-slate-50 text-slate-600 border-slate-200" };
  return <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${v.cls}`}>{v.label}</span>;
}

export function PassFail({ ok, label }: { ok: boolean | null | undefined; label?: string }) {
  const cls = ok ? "text-emerald-700 bg-emerald-50 border-emerald-200" : "text-rose-700 bg-rose-50 border-rose-200";
  return <span className={`inline-block rounded border px-1.5 py-0.5 text-xs font-semibold ${cls}`}>{label ?? (ok ? "pass" : "fail")}</span>;
}

export function Pill({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "accent" }) {
  const cls = tone === "accent" ? "bg-accent-soft text-accent border-indigo-200" : "bg-slate-100 text-slate-700 border-slate-200";
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}>{children}</span>;
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="card min-w-0 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="label">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-sm text-slate-500 italic">{children}</div>;
}

export function Chain({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {items.map((t, i) => (
        <span key={t} className="flex items-center gap-1.5">
          <Pill tone="accent">{t.replace(/^raw\./, "")}</Pill>
          {i < items.length - 1 && <span className="text-slate-400">→</span>}
        </span>
      ))}
    </div>
  );
}

export function DataTable({ columns, rows, max = 50 }: { columns: string[]; rows: Record<string, unknown>[]; max?: number }) {
  return (
    <div className="overflow-auto">
      <table className="data">
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{typeof r[c] === "number" ? (r[c] as number).toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(r[c] ?? "—")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > max && <div className="mt-2 text-xs text-slate-500">Showing {max} of {rows.length.toLocaleString()} rows</div>}
    </div>
  );
}
