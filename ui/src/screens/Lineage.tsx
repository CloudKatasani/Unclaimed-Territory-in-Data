import { useState } from "react";
import { useApi, type Json } from "../api";
import { Graph } from "../components/Graph";
import { Section } from "../components/ui";

export function Lineage() {
  const products = useApi<Json[]>(["products"], "/products");
  const [fqn, setFqn] = useState("dp.outage_reliability");
  const g = useApi<Json>(["lineage", fqn], `/lineage/graph?fqn=${encodeURIComponent(fqn)}`);
  const nodes = (g.data?.nodes ?? []).map((n: Json) => ({
    id: n.id,
    tone: n.status,
    sub: n.status === "source" ? "source" : `${n.status.replace("_", " ")}${n.criticality ? ` · crit ${n.criticality}` : ""}`,
  }));
  return (
    <div className="space-y-4">
      <Section
        title="Lineage explorer"
        right={
          <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={fqn} onChange={(e) => setFqn(e.target.value)}>
            {(products.data ?? []).map((p) => <option key={p.fqn}>{p.fqn}</option>)}
          </select>
        }
      >
        <Graph nodes={nodes} edges={g.data?.edges ?? []} />
        <div className="mt-2 text-xs text-slate-500">Nodes are badged by provenance: human-authored, agent-authored and approved, or agent-authored and unapproved.</div>
      </Section>
      <Section title="Provenance on this path">
        <table className="data">
          <thead><tr><th>object</th><th>artifact</th><th>kind</th><th>agent</th><th>approved by</th></tr></thead>
          <tbody>
            {(g.data?.nodes ?? []).flatMap((n: Json) => (n.artifacts as Json[]).map((a) => (
              <tr key={a.artifact_id}>
                <td className="font-mono text-xs">{n.id}</td>
                <td className="font-mono text-xs">{a.artifact_id.slice(0, 10)}…</td>
                <td>{a.kind}</td>
                <td>{a.agent}</td>
                <td>{a.approved_by ?? "—"}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
