import type { Json } from "../api";

/** Verified narrative: each sentence carries its query binding; restated sentences turn amber. */
export function Narrative({ sentences, verdict }: { sentences: Json[]; verdict?: string }) {
  if (!sentences?.length) return null;
  return (
    <ol className="space-y-1 text-sm" data-testid="narrative">
      {sentences.map((s) => {
        const restated = s.status === "restated";
        const tip = `query: ${s.query_ref}${s.plan_json?.time_window ? ` (${s.plan_json.time_window})` : ""}\ncertificate verdict: ${verdict ?? "—"}${restated ? "\nRESTATED: the data behind this sentence changed" : ""}`;
        return (
          <li key={s.sentence_no} title={tip} className={`cursor-help rounded px-2 py-1 ${restated ? "bg-amber-100 text-amber-900" : "hover:bg-slate-50"}`}>
            {s.sentence}
            {restated && <span className="ml-2 rounded bg-amber-300 px-1.5 text-[11px] font-semibold uppercase text-amber-900">restated</span>}
            <span className="ml-2 font-mono text-[10px] text-slate-400">[{s.query_ref}]</span>
          </li>
        );
      })}
    </ol>
  );
}
