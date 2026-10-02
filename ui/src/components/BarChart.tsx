import type { Json } from "../api";

/** Simple bar chart for one measure over one dimension. */
export function BarChart({ rows, dim, measure }: { rows: Json[]; dim: string; measure: string }) {
  const data = rows.slice(0, 31).map((r) => ({ k: String(r[dim]), v: Number(r[measure] ?? 0) }));
  const max = Math.max(1e-9, ...data.map((d) => d.v));
  const w = 640;
  const barH = 22;
  const h = data.length * (barH + 6) + 8;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full max-w-3xl" role="img" aria-label={`${measure} by ${dim}`}>
      {data.map((d, i) => {
        const y = 4 + i * (barH + 6);
        const bw = Math.max(1, (d.v / max) * (w - 230));
        return (
          <g key={d.k}>
            <text x={110} y={y + barH * 0.7} textAnchor="end" className="fill-slate-600" fontSize={12}>
              {d.k}
            </text>
            <rect x={120} y={y} width={bw} height={barH} rx={3} className="fill-accent" opacity={0.85} />
            <text x={126 + bw} y={y + barH * 0.7} fontSize={12} className="fill-slate-700">
              {d.v.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
