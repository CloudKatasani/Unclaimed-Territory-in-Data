import type { Json } from "../api";

export type GNode = { id: string; tone: string; sub?: string };

const TONES: Record<string, string> = {
  crit4: "fill-rose-50 stroke-rose-400",
  crit3: "fill-amber-50 stroke-amber-400",
  crit2: "fill-sky-50 stroke-sky-400",
  crit1: "fill-slate-50 stroke-slate-300",
  source: "fill-white stroke-slate-300",
  human: "fill-slate-100 stroke-slate-400",
  agent_approved: "fill-indigo-50 stroke-indigo-400",
  agent_unapproved: "fill-rose-50 stroke-rose-500",
};

/** Layered left-to-right DAG: column = longest distance from a source. */
export function Graph({ nodes, edges }: { nodes: GNode[]; edges: Json[] }) {
  const depth = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  for (let i = 0; i < nodes.length; i++) {
    for (const e of edges) {
      const d = (depth.get(e.src) ?? 0) + 1;
      if (depth.has(e.dst) && d > (depth.get(e.dst) ?? 0)) depth.set(e.dst, d);
    }
  }
  const cols = new Map<number, GNode[]>();
  nodes.forEach((n) => {
    const d = depth.get(n.id) ?? 0;
    cols.set(d, [...(cols.get(d) ?? []), n]);
  });
  const W = 210, H = 46, GX = 70, GY = 18;
  const pos = new Map<string, { x: number; y: number }>();
  let maxRows = 0;
  [...cols.entries()].forEach(([d, ns]) => {
    maxRows = Math.max(maxRows, ns.length);
    ns.sort((a, b) => a.id.localeCompare(b.id)).forEach((n, i) => pos.set(n.id, { x: 10 + d * (W + GX), y: 10 + i * (H + GY) }));
  });
  const width = 20 + cols.size * (W + GX);
  const height = 20 + maxRows * (H + GY);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ maxHeight: 520 }}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" className="fill-slate-400" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const a = pos.get(e.src), b = pos.get(e.dst);
        if (!a || !b) return null;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
        return <path key={i} d={`M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`} className="fill-none stroke-slate-300" strokeWidth={1.5} markerEnd="url(#arrow)" />;
      })}
      {nodes.map((n) => {
        const p = pos.get(n.id)!;
        return (
          <g key={n.id}>
            <rect x={p.x} y={p.y} width={W} height={H} rx={6} className={TONES[n.tone] ?? TONES.source} strokeWidth={1.5} />
            <text x={p.x + 10} y={p.y + 19} fontSize={12} className="fill-slate-800 font-mono">{n.id}</text>
            {n.sub && <text x={p.x + 10} y={p.y + 35} fontSize={10.5} className="fill-slate-500">{n.sub}</text>}
          </g>
        );
      })}
    </svg>
  );
}
