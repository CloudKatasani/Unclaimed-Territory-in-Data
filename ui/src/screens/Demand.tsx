import { useState } from "react";
import { api, useAction, useApi, useUser, type Json } from "../api";
import { Chain, Empty, Pill, Section } from "../components/ui";

const GAP: Record<string, string> = {
  missing_join_path: "Missing join path",
  missing_attribute: "Missing attribute",
  missing_grain: "Missing grain",
  missing_domain: "Missing domain",
};

function ContractView({ id }: { id: string }) {
  const c = useApi<Json>(["contract", id], `/contracts/${id}`);
  const [diff, setDiff] = useState(false);
  if (!c.data) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center gap-2 text-xs">
        <button className={`btn ${!diff ? "border-accent text-accent" : ""}`} onClick={() => setDiff(false)}>YAML</button>
        <button className={`btn ${diff ? "border-accent text-accent" : ""}`} onClick={() => setDiff(true)}>Diff vs last version</button>
        <span className="text-slate-500">v{c.data.version} · {c.data.status}</span>
      </div>
      <pre className="code max-h-96">
        {diff
          ? (c.data.diff as string[]).map((l, i) => (
              <div key={i} className={l.startsWith("+") ? "text-emerald-300" : l.startsWith("-") ? "text-rose-300" : ""}>{l}</div>
            ))
          : c.data.spec_yaml}
      </pre>
    </div>
  );
}

function IntentCard({ it }: { it: Json }) {
  const { user } = useUser();
  const [open, setOpen] = useState(false);
  const draft = useAction(() => api(`/demand/intents/${it.intent_id}/draft`, { method: "POST" }));
  const approve = useAction(() => api(`/contracts/${it.contract_id}/approve`, { method: "POST", user }));
  const reject = useAction(() => api(`/contracts/${it.contract_id}/reject`, { method: "POST", user }));
  const askers = it.required_elements?.score_breakdown?.askers ?? [];
  const isApprover = user === "raj" || user === "admin";
  return (
    <div className="card p-4" data-testid="intent-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-base font-semibold">{it.label}</div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs">
            <Pill>{GAP[it.gap_type] ?? it.gap_type}</Pill>
            <Pill>status: {it.status}</Pill>
            {it.contract_status && <Pill>contract: {it.contract_status}</Pill>}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold text-accent">{it.demand_score}</div>
          <div className="label">demand score</div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <div className="label mb-1">Askers</div>
          <div className="flex gap-1">{askers.map((a: string) => <Pill key={a}>{a}</Pill>)}</div>
          <div className="label mb-1 mt-3">Questions</div>
          <ul className="space-y-1 text-sm">
            {(it.questions as Json[]).map((q) => (
              <li key={q.question_id} className="flex items-start gap-2">
                <span className={q.resolved_by_product ? "text-emerald-600" : "text-slate-400"}>{q.resolved_by_product ? "✓" : "•"}</span>
                <span>
                  <span className="text-slate-500">{q.asked_by}:</span> {q.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="label mb-1">Candidate join path</div>
          <Chain items={it.join_path?.tables ?? []} />
          <div className="label mb-1 mt-3">Sources found</div>
          <ul className="font-mono text-xs text-slate-600">
            {(it.candidate_sources as Json[]).map((s) => (
              <li key={s.column}>
                {s.concept}: {s.column}
              </li>
            ))}
          </ul>
          <div className="label mb-1 mt-3">Closure</div>
          <div className="text-sm">
            {it.closure.resolved} / {it.closure.total} questions resolved
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
        {!it.contract_id && (
          <button className="btn-primary" disabled={draft.isPending} onClick={() => draft.mutate()}>
            {draft.isPending ? "Drafting…" : "Draft contract"}
          </button>
        )}
        {it.contract_id && (
          <button className="btn" onClick={() => setOpen(!open)}>
            {open ? "Hide contract" : "View contract"}
          </button>
        )}
        {it.contract_id && it.contract_status === "draft" && isApprover && (
          <>
            <button className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}>
              {approve.isPending ? "Approving & building…" : "Approve"}
            </button>
            <button className="btn" onClick={() => reject.mutate()}>Reject</button>
          </>
        )}
        {it.contract_id && it.contract_status === "draft" && !isApprover && (
          <span className="text-xs text-slate-500">Awaiting approval by a data engineer</span>
        )}
        {(draft.error || approve.error) && <span className="text-sm text-rose-600">{String((draft.error || approve.error)?.message)}</span>}
      </div>
      {open && it.contract_id && <ContractView id={it.contract_id} />}
    </div>
  );
}

export function Demand() {
  const intents = useApi<Json[]>(["intents"], "/demand/intents");
  const pending = useApi<Json[]>(["unresolved"], "/demand/unresolved");
  const mine = useAction(() => api("/demand/mine", { method: "POST" }));
  return (
    <div className="space-y-4">
      <Section
        title="Unresolved questions awaiting mining"
        right={
          <button className="btn-primary" disabled={mine.isPending || !pending.data?.length} onClick={() => mine.mutate()}>
            {mine.isPending ? "Mining…" : "Mine demand"}
          </button>
        }
      >
        {pending.data?.length ? (
          <ul className="space-y-1 text-sm">
            {pending.data.map((q) => (
              <li key={q.question_id}>
                <span className="text-slate-500">{q.asked_by}:</span> {q.text} <Pill>{q.outcome}</Pill>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No unresolved questions without an intent.</Empty>
        )}
      </Section>
      {(intents.data ?? []).map((it) => (
        <IntentCard key={it.intent_id} it={it} />
      ))}
      {intents.data?.length === 0 && <Empty>No demand intents yet.</Empty>}
    </div>
  );
}
