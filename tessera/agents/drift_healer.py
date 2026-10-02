"""Drift Healer (A2, core of ID-2): detect -> blast radius -> candidates -> shadow runs -> gate evidence."""

from __future__ import annotations

import math
from typing import Any

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from tessera import clock, ids
from tessera.config import Settings
from tessera.governance import contracts as contracts_mod
from tessera.governance import ingestion
from tessera.governance.lineage import Lineage
from tessera.jsonutil import dumps, loads
from tessera.llm.client import LLM
from tessera.orchestrator.bus import Bus
from tessera.orchestrator.notify import record_metric
from tessera.publisher.promote import rewrite_tables
from tessera.warehouse.base import Warehouse

AGENT = "drift_healer@0.1.0"
NUMERIC = {"DOUBLE", "FLOAT", "REAL", "INTEGER", "BIGINT", "HUGEINT", "SMALLINT", "DECIMAL"}
MAX_CANDIDATES = 3
PRECISION = 9  # decimal places used when comparing floating-point outputs


class PatchCandidate(BaseModel):
    label: str
    expression: str
    rationale: str = ""


class PatchCandidates(BaseModel):
    candidates: list[PatchCandidate] = Field(default_factory=list)


def patch_mapping(select_sql: str, old: str, expression: str) -> str:
    """Replace references to a drifted column in an ingestion mapping, keeping the output name."""
    tree = sqlglot.parse_one(select_sql, dialect="duckdb")
    new_expr = sqlglot.parse_one(expression, dialect="duckdb")
    assert isinstance(tree, exp.Select)
    projections = []
    for p in tree.expressions:
        if isinstance(p, exp.Column) and p.name == old:
            projections.append(exp.alias_(new_expr.copy(), old))
        elif isinstance(p, exp.Alias) and isinstance(p.this, exp.Column) and p.this.name == old:
            projections.append(exp.alias_(new_expr.copy(), p.alias))
        else:
            projections.append(p)
    tree.set("expressions", projections)
    return str(tree.sql(dialect="duckdb"))


def _is_numeric(t: str) -> bool:
    return t.upper().split("(")[0] in NUMERIC


def divergence(wh: Warehouse, base: str, cand: str, key: list[str]) -> dict[str, Any]:
    """Divergence metrics between a baseline and a candidate table (docs/03 step 6)."""
    metrics: dict[str, float] = {}
    nb = int(wh.scalar(f"SELECT count(*) FROM {base}") or 0)
    nc = int(wh.scalar(f"SELECT count(*) FROM {cand}") or 0)
    metrics["row_count_ratio"] = abs(1 - nc / nb) if nb else (0.0 if nc == 0 else 1.0)
    bcols = {c.name: c.data_type for c in wh.table_schema(base)}
    ccols = {c.name: c.data_type for c in wh.table_schema(cand)}
    per_column: dict[str, dict[str, Any]] = {}
    for col, btype in bcols.items():
        if col not in ccols:
            metrics[f"{col}.missing"] = 1.0
            continue
        q = f'"{col}"'
        # Floating-point aggregates differ in the last bits between parallel runs, so numeric values are
        # compared at a fixed precision; otherwise distinct counts flap and the gate gets false positives.
        dq = (
            f"round(CAST({q} AS DOUBLE), {PRECISION})"
            if _is_numeric(btype) and _is_numeric(ccols[col])
            else q
        )
        b = wh.rows(
            f"SELECT avg(CASE WHEN {q} IS NULL THEN 1.0 ELSE 0 END) AS nr, count(DISTINCT {dq}) AS d "
            f"FROM {base}"
        )[0]
        c = wh.rows(
            f"SELECT avg(CASE WHEN {q} IS NULL THEN 1.0 ELSE 0 END) AS nr, count(DISTINCT {dq}) AS d "
            f"FROM {cand}"
        )[0]
        col_m: dict[str, Any] = {
            "baseline": {"null_rate": float(b["nr"] or 0), "distinct": int(b["d"] or 0)},
            "candidate": {"null_rate": float(c["nr"] or 0), "distinct": int(c["d"] or 0)},
        }
        metrics[f"{col}.null_rate_delta"] = abs(float(b["nr"] or 0) - float(c["nr"] or 0))
        bd, cd = int(b["d"] or 0), int(c["d"] or 0)
        metrics[f"{col}.distinct_ratio"] = abs(1 - cd / bd) if bd else (0.0 if cd == 0 else 1.0)
        if _is_numeric(btype) and _is_numeric(ccols[col]):
            stats = "sum({q}) AS s, quantile_cont({q}, 0.5) AS p50, quantile_cont({q}, 0.95) AS p95"
            bs = wh.rows(f"SELECT {stats.format(q=q)} FROM {base}")[0]
            cs = wh.rows(f"SELECT {stats.format(q=q)} FROM {cand}")[0]
            for k, label in (("s", "sum"), ("p50", "p50"), ("p95", "p95")):
                bv, cv = float(bs[k] or 0), float(cs[k] or 0)
                scale = max(abs(bv), 1e-9)
                rel = abs(cv - bv) / scale if (abs(bv) > 1e-9 or abs(cv) > 1e-9) else 0.0
                metrics[f"{col}.{label}_rel_diff"] = 0.0 if rel < 1e-12 else rel
            col_m["baseline"].update(
                {"sum": float(bs["s"] or 0), "p50": float(bs["p50"] or 0), "p95": float(bs["p95"] or 0)}
            )
            col_m["candidate"].update(
                {"sum": float(cs["s"] or 0), "p50": float(cs["p50"] or 0), "p95": float(cs["p95"] or 0)}
            )
            if key and all(k in ccols for k in key):
                on = " AND ".join(f"b.{k} IS NOT DISTINCT FROM c.{k}" for k in key)
                r = wh.rows(
                    f"SELECT max(abs(CAST(c.{q} AS DOUBLE) - CAST(b.{q} AS DOUBLE))) AS mx, "
                    f"avg(abs(CAST(b.{q} AS DOUBLE))) AS scale FROM {base} b JOIN {cand} c ON {on}"
                )[0]
                mx, scale = float(r["mx"] or 0), float(r["scale"] or 0)
                mx = 0.0 if mx < 10**-PRECISION else mx
                metrics[f"{col}.max_key_abs_diff_norm"] = (
                    mx / scale if scale > 1e-9 else (0.0 if mx == 0 else 1.0)
                )
                col_m["max_key_abs_diff"] = mx
        per_column[col] = col_m
    clean = {k: (1e9 if math.isnan(v) or math.isinf(v) else round(v, 10)) for k, v in metrics.items()}
    score = max(clean.values()) if clean else 0.0
    worst = max(clean, key=lambda k: clean[k]) if clean else None
    return {
        "divergence": score,
        "worst_metric": worst,
        "metrics": clean,
        "columns": per_column,
        "rows": {"baseline": nb, "candidate": nc},
    }


class DriftHealer:
    def __init__(self, wh: Warehouse, llm: LLM, lineage: Lineage, bus: Bus, settings: Settings) -> None:
        self.wh = wh
        self.llm = llm
        self.lineage = lineage
        self.bus = bus
        self.settings = settings
        self.sentinel_check: Any = None  # set when sentinel records are enabled (M9)

    # -- detection -----------------------------------------------------------------------------------
    def check(self) -> list[str]:
        """Ingestion check: compare live raw schemas/profiles to snapshots; emit drift.detected."""
        out = []
        for ev in ingestion.check(self.wh):
            eid = ids.new_id(self.wh, "drift")
            self.wh.execute(
                "INSERT INTO meta.drift_events VALUES (?, ?, ?, ?, 'detected', NULL, NULL)",
                [eid, ev["table"], dumps(ev["changes"]), clock.naive_utc(clock.now())],
            )
            self.bus.emit("drift.detected", {"event_id": eid, "table": ev["table"]})
            out.append(eid)
        return out

    def event(self, event_id: str) -> dict[str, Any]:
        r = self.wh.rows("SELECT * FROM meta.drift_events WHERE event_id = ?", [event_id])[0]
        out = dict(r)
        out["changes"] = loads(r["changes_json"])
        out["blast_radius"] = loads(r["blast_radius_json"])
        return out

    # -- blast radius ----------------------------------------------------------------------------------
    def criticality(self) -> dict[str, int]:
        return {str(r["fqn"]): int(r["criticality"]) for r in self.wh.rows("SELECT * FROM meta.criticality")}

    def blast_radius(self, table: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        crit = self.criticality()
        cols = sorted({str(c.get("old_column") or c.get("column")) for c in changes if c["kind"] != "add"})
        downstream: set[str] = set()
        for col in cols:
            downstream |= set(self.lineage.downstream_columns(table, str(col)))
        objects: dict[str, dict[str, Any]] = {}
        for node in sorted(downstream):
            fqn, column = node.rsplit(".", 1)
            o = objects.setdefault(
                fqn, {"fqn": fqn, "criticality": crit.get(fqn, 2), "columns": [], "kind": self._kind(fqn)}
            )
            o["columns"].append(column)
        edges = [e for e in self.lineage.table_graph() if e["src"] in objects or e["src"] == table]
        edges = [e for e in edges if e["dst"] in objects]
        return {
            "source": table,
            "source_columns": cols,
            "columns": sorted(downstream),
            "objects": sorted(objects.values(), key=lambda o: (-o["criticality"], o["fqn"])),
            "edges": edges,
        }

    def _kind(self, fqn: str) -> str:
        r = self.wh.rows("SELECT kind FROM meta.models WHERE fqn = ?", [fqn])
        return str(r[0]["kind"]) if r else "table"

    # -- healing -------------------------------------------------------------------------------------
    def heal(self, event_id: str) -> str:
        ev = self.event(event_id)
        table = str(ev["fqn"])
        changes = ev["changes"]
        radius = self.blast_radius(table, changes)
        job_id = ids.new_id(self.wh, "patch")
        self.wh.execute(
            "UPDATE meta.drift_events SET blast_radius_json = ?, status = 'healing', job_id = ? "
            "WHERE event_id = ?",
            [dumps(radius), job_id, event_id],
        )
        rename = next((c for c in changes if c["kind"] == "rename"), None)
        tchange = next((c for c in changes if c["kind"] == "type_change"), None)
        mapping = next((o for o in radius["objects"] if o["kind"] == "view"), None)
        products = [o for o in radius["objects"] if o["kind"] == "table" and o["fqn"].startswith("dp.")]
        if rename is None or mapping is None:
            return self._hold(job_id, event_id, None, products, "no automatic patch for this change type")
        old, new = str(rename["old_column"]), str(rename["new_column"])
        mapping_sql = str(
            self.wh.rows("SELECT select_sql FROM meta.models WHERE fqn = ?", [mapping["fqn"]])[0][
                "select_sql"
            ]
        )
        old_type = str(tchange["old_type"]) if tchange else None
        candidates = [
            PatchCandidate(
                label="alias map (rename only)", expression=new, rationale=f"map {new} back to {old}"
            )
        ]
        if old_type:
            candidates.append(
                PatchCandidate(
                    label=f"alias map + cast to {old_type}",
                    expression=f"CAST({new} AS {old_type})",
                    rationale=f"restore the contract type {old_type}",
                )
            )
        llm = self.llm.complete_json(
            "drift.patch",
            {
                "table": table,
                "changes": changes,
                "mapping_fqn": mapping["fqn"],
                "mapping_sql": mapping_sql,
                "deterministic": [c.expression for c in candidates],
            },
            PatchCandidates,
        )
        for c in llm.value.candidates:
            if len(candidates) >= MAX_CANDIDATES:
                break
            if c.expression not in {x.expression for x in candidates}:
                candidates.append(c)
        sources = {"deterministic": len(candidates) - len(llm.value.candidates)}
        window = self._window()
        baseline = f"baseline_{job_id.lower()}"
        self._build_env(baseline, "lkg.head_end_vendor_feed", mapping["fqn"], mapping_sql, window, products)
        results = []
        for n, cand in enumerate(candidates, start=1):
            patched = patch_mapping(mapping_sql, old, cand.expression)
            shadow = f"shadow_{job_id.lower()}_{n}"
            errors = self._build_env(shadow, table, mapping["fqn"], patched, window, products)
            objects: dict[str, Any] = {}
            sentinel: dict[str, Any] | None = None
            if self.sentinel_check is not None and not errors:
                sentinel = self.sentinel_check(shadow, [p["fqn"] for p in products])
            for p in products:
                thr = self.settings.drift_thresholds[p["criticality"]]
                if p["fqn"] in errors:
                    objects[p["fqn"]] = {
                        "divergence": None,
                        "threshold": thr,
                        "passed": False,
                        "error": errors[p["fqn"]],
                        "criticality": p["criticality"],
                    }
                    continue
                if sentinel and not sentinel.get(p["fqn"], {}).get("passed", True):
                    objects[p["fqn"]] = {
                        "divergence": None,
                        "threshold": thr,
                        "passed": False,
                        "criticality": p["criticality"],
                        "error": "sentinel verification failed before the divergence gate",
                        "sentinel": sentinel[p["fqn"]],
                    }
                    continue
                key = self._key(p["fqn"])
                tbl = p["fqn"].split(".", 1)[1]
                d = divergence(self.wh, f"{baseline}.{tbl}", f"{shadow}.{tbl}", key)
                objects[p["fqn"]] = {
                    **d,
                    "threshold": thr,
                    "passed": d["divergence"] <= thr,
                    "criticality": p["criticality"],
                }
            passed = all(o["passed"] for o in objects.values())
            divs = [o["divergence"] for o in objects.values() if o["divergence"] is not None]
            max_div = (
                None if any(o["divergence"] is None for o in objects.values()) else max(divs, default=0.0)
            )
            result: dict[str, Any] = {
                "objects": objects,
                "max_divergence": max_div,
                "passed": passed,
                "sentinel": sentinel,
            }
            results.append((n, cand, patched, result))
            self.wh.execute(
                "INSERT INTO meta.patch_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE)",
                [
                    job_id,
                    n,
                    cand.label,
                    "deterministic" if n <= sources["deterministic"] else "llm",
                    cand.expression,
                    patched,
                    dumps(result),
                    passed,
                ],
            )
            self.wh.drop_schema(shadow)
        self.wh.drop_schema(baseline)
        passing = [r for r in results if r[3]["passed"]]
        record_metric(self.wh, "id2.candidates_generated", len(results), {"job_id": job_id})
        record_metric(self.wh, "id2.candidates_rejected", len(results) - len(passing), {"job_id": job_id})
        for n, cand, _, res in results:
            record_metric(
                self.wh,
                "id2.candidate_divergence",
                -1.0 if res["max_divergence"] is None else float(res["max_divergence"]),
                {"job_id": job_id, "candidate": n, "label": cand.label, "passed": res["passed"]},
            )
        evidence: dict[str, Any] = {
            "event_id": event_id,
            "window": [str(window[0]), str(window[1])],
            "blast_radius": radius,
            "candidates": [
                {"candidate_no": n, "label": c.label, "expression": c.expression, **r}
                for n, c, _, r in results
            ],
        }
        if not passing:
            return self._hold(job_id, event_id, evidence, products, "no candidate passed the gate")
        best = min(passing, key=lambda r: (r[3]["max_divergence"], r[0]))
        self.wh.execute(
            "UPDATE meta.patch_candidates SET selected = TRUE WHERE job_id = ? AND candidate_no = ?",
            [job_id, best[0]],
        )
        evidence["selected"] = best[0]
        self.wh.execute(
            "INSERT INTO meta.patches VALUES (?, ?, ?, 'ready_for_gate', ?, ?, NULL, '[]', '[]', NULL, ?, NULL)",
            [job_id, event_id, mapping["fqn"], best[0], dumps(evidence), clock.naive_utc(clock.now())],
        )
        self.wh.execute("UPDATE meta.drift_events SET status = 'patched' WHERE event_id = ?", [event_id])
        self.bus.emit("patch.ready_for_gate", {"job_id": job_id})
        return job_id

    def _hold(
        self,
        job_id: str,
        event_id: str,
        evidence: dict[str, Any] | None,
        products: list[dict[str, Any]],
        reason: str,
    ) -> str:
        for p in products:
            self.wh.execute(
                "UPDATE meta.products SET refresh_status = 'held', held_reason = ? WHERE fqn = ?",
                [f"drift {event_id}: {reason}", p["fqn"]],
            )
        self.wh.execute(
            "INSERT INTO meta.patches VALUES (?, ?, NULL, 'held', NULL, ?, NULL, '[]', ?, NULL, ?, NULL)",
            [
                job_id,
                event_id,
                dumps(evidence or {"reason": reason}),
                dumps([p["fqn"] for p in products]),
                clock.naive_utc(clock.now()),
            ],
        )
        self.wh.execute("UPDATE meta.drift_events SET status = 'held' WHERE event_id = ?", [event_id])
        record_metric(self.wh, "id2.refreshes_held", len(products), {"job_id": job_id})
        self.bus.emit("patch.rejected", {"job_id": job_id, "reason": reason})
        return job_id

    def _window(self) -> tuple[Any, Any]:
        r = self.wh.rows("SELECT min(read_ts) AS lo, max(read_ts) AS hi FROM lkg.head_end_vendor_feed")[0]
        return r["lo"], r["hi"]

    def _key(self, fqn: str) -> list[str]:
        found = contracts_mod.latest_for(self.wh, fqn)
        return found[0].primary_key if found else []

    def _build_env(
        self,
        schema: str,
        feed_src: str,
        mapping_fqn: str,
        mapping_sql: str,
        window: tuple[Any, Any],
        products: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Clone sources into `schema` over the gate window, apply the mapping, rebuild affected products."""
        lo, hi = window
        self.wh.drop_schema(schema)
        self.wh.execute(f"CREATE SCHEMA {schema}")
        feed_name = "head_end_vendor_feed"
        self.wh.clone_table(
            feed_src,
            f"{schema}.{feed_name}",
            where=f"read_ts >= TIMESTAMP '{lo}' AND read_ts <= TIMESTAMP '{hi}'",
        )
        for t in ("meters", "feeders"):
            self.wh.clone_table(f"raw.{t}", f"{schema}.{t}")
        self.wh.clone_table(
            "raw.outage_events",
            f"{schema}.outage_events",
            where=f"start_ts >= TIMESTAMP '{lo}' AND start_ts <= TIMESTAMP '{hi}'",
        )
        mapping = {
            f"raw.{t}": f"{schema}.{t}"
            for t in ("meters", "feeders", "outage_events", "head_end_vendor_feed", "ami_interval_reads")
        }
        errors: dict[str, str] = {}
        try:
            self.wh.execute(
                f"CREATE TABLE {schema}.{mapping_fqn.split('.', 1)[1]} AS "
                f"{rewrite_tables(mapping_sql, mapping)}"
            )
        except Exception as exc:  # noqa: BLE001
            return {p["fqn"]: f"ingestion mapping failed: {exc}" for p in products}
        for p in products:
            sql = str(
                self.wh.rows("SELECT select_sql FROM meta.models WHERE fqn = ?", [p["fqn"]])[0]["select_sql"]
            )
            try:
                self.wh.execute(
                    f"CREATE TABLE {schema}.{p['fqn'].split('.', 1)[1]} AS {rewrite_tables(sql, mapping)}"
                )
            except Exception as exc:  # noqa: BLE001
                errors[p["fqn"]] = str(exc).splitlines()[0][:300]
        return errors
