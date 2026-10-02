import { useState } from "react";
import { api, fmt, shortId, type Json } from "../api";
import { PassFail, Verdict } from "./ui";

const BUILT: Record<string, { label: string; cls: string }> = {
  human: { label: "human", cls: "bg-slate-100 border-slate-300 text-slate-700" },
  agent_approved: { label: "agent · approved", cls: "bg-indigo-50 border-indigo-300 text-indigo-700" },
  agent_unapproved: { label: "agent · unapproved", cls: "bg-rose-50 border-rose-300 text-rose-700" },
};

export function Certificate({ cert }: { cert: Json }) {
  const [verify, setVerify] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false);
  const decisive = (cert.rule_trace as Json[]).filter((t) => t.outcome === cert.verdict);
  const tables = new Map<string, string>();
  for (const b of cert.built_by as Json[]) {
    const t = (b.node as string).split(".").slice(0, 2).join(".");
    const prev = tables.get(t);
    if (!prev || b.status !== "human") tables.set(t, b.status);
  }
  return (
    <div className="card p-4 space-y-4 text-sm" data-testid="certificate">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="label">Trust certificate</div>
          <div className="font-mono text-xs text-slate-500">{shortId(cert.cert_id)}</div>
        </div>
        <Verdict verdict={cert.verdict} />
      </div>
      <ul className="space-y-1 text-xs text-slate-600">
        {decisive.map((t: Json, i: number) => (
          <li key={i}>
            <span className="font-semibold">Rule {t.rule}:</span> {t.reason}
          </li>
        ))}
      </ul>

      <div>
        <div className="label mb-1">Metrics</div>
        {(cert.metrics as Json[]).map((m) => (
          <div key={m.name} className="flex items-center justify-between">
            <span className="font-medium">{m.name}</span>
            <span className={m.certified ? "text-emerald-700" : "text-amber-700"}>
              {m.certified ? `✓ certified by ${m.certified_by}` : "not certified"}
            </span>
          </div>
        ))}
      </div>

      <div>
        <div className="label mb-1">Policies applied</div>
        {(cert.policy_decisions as Json[]).length === 0 && <div className="text-slate-500">None</div>}
        {(cert.policy_decisions as Json[]).map((d, i) => (
          <div key={i}>{d.sentence}</div>
        ))}
      </div>

      <div>
        <div className="label mb-1">Freshness &amp; quality</div>
        {Object.entries(cert.freshness as Record<string, Json>).map(([fqn, f]) => (
          <div key={fqn} className="flex flex-wrap items-center justify-between gap-1">
            <span className="font-mono text-xs">{fqn}</span>
            <span className="flex items-center gap-1">
              <PassFail ok={f.pass} label={f.pass ? `fresh · ${fmt(f.age_hours)}h` : `stale · ${fmt(f.age_hours)}h`} />
              <PassFail ok={(cert.quality[fqn]?.score ?? 0) >= 0.9} label={`quality ${fmt(cert.quality[fqn]?.score)}`} />
              {f.refresh_status === "held" && <PassFail ok={false} label="refresh held" />}
            </span>
          </div>
        ))}
      </div>

      <div>
        <div className="label mb-1">Built by</div>
        <div className="flex flex-wrap gap-1">
          {[...tables.entries()].map(([t, status]) => (
            <span key={t} title={BUILT[status]?.label} className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${BUILT[status]?.cls}`}>
              {t}
            </span>
          ))}
        </div>
        <div className="mt-1 flex gap-3 text-[11px] text-slate-500">
          {Object.values(BUILT).map((b) => (
            <span key={b.label} className="flex items-center gap-1">
              <span className={`inline-block h-2.5 w-2.5 rounded-sm border ${b.cls}`} /> {b.label}
            </span>
          ))}
        </div>
        <div className="mt-1 text-xs text-slate-500">
          Agent-authored, unapproved share of path: {(cert.agent_share_unapproved * 100).toFixed(0)}%
        </div>
        {(cert.agent_authored as Json[]).length > 0 && (
          <ul className="mt-2 space-y-1 text-xs">
            {(cert.agent_authored as Json[]).map((a) => (
              <li key={a.artifact_id} className="rounded bg-slate-50 p-1.5">
                <span className="font-mono">{a.target_fqn}</span> · {a.artifact_kind} by <b>{a.agent}</b> · approved by{" "}
                <b>{a.approved_by ?? "nobody"}</b>
                {a.gate_evidence?.objects && (
                  <div className="mt-1 text-slate-500">
                    Gate evidence:{" "}
                    {Object.entries(a.gate_evidence.objects as Record<string, Json>)
                      .map(([f, o]) => `${f.replace("dp.", "")} ${fmt(o.divergence)} ≤ ${o.threshold}`)
                      .join(" · ")}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              setVerify(await api(`/certs/${cert.cert_id}/verify`));
            } finally {
              setBusy(false);
            }
          }}
        >
          Verify
        </button>
        {verify && (
          <span className="flex gap-1">
            <PassFail ok={verify.signature_valid} label={verify.signature_valid ? "signature valid" : "signature invalid"} />
            <PassFail ok={verify.chain_intact} label={verify.chain_intact ? "chain intact" : "chain broken"} />
          </span>
        )}
      </div>
    </div>
  );
}
