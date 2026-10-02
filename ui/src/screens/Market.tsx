import { useState } from "react";
import { api, fmt, useAction, useApi, useUser, type Json } from "../api";
import { Empty, PassFail, Pill, Section, Verdict } from "../components/ui";

const KINDS: { kind: string; title: string }[] = [
  { kind: "decision_pack", title: "Decision packs" },
  { kind: "data_product", title: "Data products" },
  { kind: "agent", title: "Agents" },
];

function EvidenceStrip({ card }: { card: Json }) {
  const e = card.evidence ?? {};
  if (card.kind === "data_product") {
    if (!e.available) return <div className="text-xs text-slate-500">Not yet published</div>;
    return (
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Verdict verdict={e.verdict} />
        <PassFail ok={e.freshness?.pass} label={e.freshness?.pass ? `fresh · ${fmt(e.freshness?.age_hours)}h` : "refresh held"} />
        <PassFail ok={(e.quality_score ?? 0) >= 0.9} label={`quality ${fmt(e.quality_score)}`} />
        {e.demand_score != null && <Pill>demand {fmt(e.demand_score)}</Pill>}
        <Pill>SLA credits 30d: {fmt(e.sla_credits_30d)}</Pill>
        {e.criticality && <Pill>criticality {e.criticality}</Pill>}
      </div>
    );
  }
  if (card.kind === "agent") {
    return (
      <div className="space-y-1 text-xs">
        {e.sentence && <div className="font-medium text-slate-700">{e.sentence}</div>}
        <div className="flex flex-wrap gap-1.5">
          {e.cost_per_run != null && <Pill>{fmt(e.cost_per_run)} credits / run</Pill>}
          {(e.required_products ?? []).map((p: string) => <Pill key={p}>{p}</Pill>)}
          {e.trial && <Pill tone="accent">trial: {e.trial.status}</Pill>}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-1.5 text-xs">
      <div className="flex items-center gap-1.5"><Verdict verdict={e.verdict} /> <span className="text-slate-500">worst of bundle</span></div>
      {(e.thresholds ?? []).map((t: Json) => (
        <div key={t.metric} className="flex flex-wrap items-center gap-1.5">
          <Pill>{t.metric} {t.comparator} {fmt(t.value)}</Pill>
          <span className="text-slate-600">{t.meaning}</span>
          {t.margin != null && <span className={t.breached ? "text-rose-700" : "text-emerald-700"}>margin {fmt(t.margin)}</span>}
        </div>
      ))}
    </div>
  );
}

export function ListingCard({ card, onOpen }: { card: Json; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="card w-full p-4 text-left hover:border-accent" data-testid={`listing-${card.listing_id}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{card.title}</div>
          <div className="font-mono text-xs text-slate-500">{card.fqn}{card.kind === "agent" ? ` · v${card.version}` : ""}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {card.status === "candidate" && <Pill tone="accent">candidate</Pill>}
          <span className="text-xs text-slate-500">{card.subscribers} subscribers</span>
        </div>
      </div>
      {card.description && <p className="mt-2 line-clamp-2 text-sm text-slate-600">{card.description}</p>}
      <div className="mt-3"><EvidenceStrip card={card} /></div>
    </button>
  );
}

export function ListingDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { user } = useUser();
  const d = useApi<Json>(["listing", id, user], `/market/listings/${encodeURIComponent(id)}?user=${user}`);
  const sub = useAction(() => api(`/market/listings/${encodeURIComponent(id)}/subscribe`, { method: "POST", user }));
  const unsub = useAction(() => api(`/market/listings/${encodeURIComponent(id)}/unsubscribe`, { method: "POST", user }));
  const l = d.data;
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/20" onClick={onClose}>
      <div className="h-full w-[640px] max-w-full overflow-auto bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()} data-testid="drawer">
        {l && (
          <div className="space-y-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-lg font-semibold">{l.title}</div>
                <div className="font-mono text-xs text-slate-500">{l.fqn}</div>
              </div>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
            <EvidenceStrip card={l} />
            <div className="flex items-center gap-2">
              {l.subscription ? (
                <>
                  <Pill tone="accent">subscribed · {l.subscription.status}</Pill>
                  <button className="btn" onClick={() => unsub.mutate()}>Unsubscribe</button>
                </>
              ) : (
                <button className="btn-primary" disabled={sub.isPending || l.status === "candidate"} onClick={() => sub.mutate()}>
                  Subscribe as {user}
                </button>
              )}
              {sub.data?.status === "pending_approval" && (
                <span className="text-xs text-amber-700">Pending: {(sub.data.missing as Json[]).map((m) => `${m.fqn} (${m.reason})`).join(", ")}</span>
              )}
            </div>
            {l.kind === "agent" && l.evidence?.trial && <TrialStrip agent={l.fqn.split(".")[1]} trial={l.evidence.trial} />}
            {l.history?.length > 0 && (
              <Section title="Evaluation history (signed)">
                <table className="data">
                  <thead><tr><th>version</th><th>accuracy</th><th>n</th><th>cost/run</th><th>evaluated</th><th>signature</th></tr></thead>
                  <tbody>
                    {l.history.map((h: Json) => (
                      <tr key={h.evaluation_id}>
                        <td>v{h.version}</td><td>{(h.accuracy * 100).toFixed(1)}%</td><td>{fmt(h.n)}</td><td>{fmt(h.avg_cost)}</td>
                        <td>{h.evaluated_at.slice(0, 10)}</td><td className="font-mono text-xs">{h.signature.slice(0, 12)}…</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            )}
            <Section title="Contract">
              <pre className="code max-h-[420px]">{l.spec_yaml}</pre>
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}

export function TrialStrip({ agent, trial }: { agent: string; trial: Json }) {
  const { user } = useUser();
  const promote = useAction(() => api(`/market/agents/${agent}/promote`, { method: "POST", user }));
  return (
    <div className="rounded-md border border-indigo-200 bg-accent-soft p-3 text-sm" data-testid="trial-strip">
      <div className="font-medium">
        Trial: {trial.days} days, {fmt(trial.questions)} questions, {fmt(trial.divergent_pct)}% divergent, {trial.cost_change_pct > 0 ? "+" : ""}
        {fmt(trial.cost_change_pct)}% cost
      </div>
      <div className="mt-1 text-xs text-slate-600">
        Non-zero-usage questions divergent: {fmt(trial.divergent_other)} · status {trial.status ?? "running"}
        {trial.gate && <> · gate: {trial.gate.passed ? "pass" : `fail (${trial.gate.reasons.join("; ")})`}</>}
      </div>
      {trial.status !== "promoted" && (user === "raj" || user === "admin") && (
        <button className="btn-primary mt-2" disabled={promote.isPending} onClick={() => promote.mutate()}>Promote</button>
      )}
      {promote.error && <div className="mt-1 text-xs text-rose-700">{String(promote.error.message)}</div>}
    </div>
  );
}

export function Market({ only }: { only?: string }) {
  const listings = useApi<Json[]>(["listings"], "/market/listings");
  const [open, setOpen] = useState<string | null>(null);
  const kinds = only ? KINDS.filter((k) => k.kind === only) : KINDS;
  return (
    <div className="space-y-6">
      {kinds.map(({ kind, title }) => {
        let cards = (listings.data ?? []).filter((c) => c.kind === kind);
        if (kind === "agent") cards = [...cards].sort((a, b) => (b.evidence?.accuracy ?? 0) - (a.evidence?.accuracy ?? 0) || (a.evidence?.cost_per_run ?? 0) - (b.evidence?.cost_per_run ?? 0));
        return (
          <div key={kind}>
            <h2 className="label mb-2">{title}</h2>
            {cards.length ? (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                {cards.map((c) => <ListingCard key={c.listing_id} card={c} onOpen={() => setOpen(c.listing_id)} />)}
              </div>
            ) : (
              <Empty>Nothing listed.</Empty>
            )}
          </div>
        );
      })}
      {open && <ListingDrawer id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
