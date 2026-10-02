"""Ingestion check: compares each raw table's live schema and column profiles to the last snapshot."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from tessera import clock
from tessera.jsonutil import dumps, loads
from tessera.warehouse.base import Warehouse

PHYSICAL_RAW = ["raw.feeders", "raw.meters", "raw.outage_events", "raw.head_end_vendor_feed"]
NUMERIC = {"DOUBLE", "FLOAT", "REAL", "INTEGER", "BIGINT", "SMALLINT", "HUGEINT", "DECIMAL"}


def _is_numeric(t: str) -> bool:
    return t.upper().split("(")[0] in NUMERIC


def profile_column(wh: Warehouse, fqn: str, column: str, data_type: str) -> dict[str, Any]:
    c = f'"{column}"'
    base = wh.rows(f"SELECT count(*) AS n, count({c}) AS nn, approx_count_distinct({c}) AS d FROM {fqn}")[0]
    n = int(base["n"]) or 1
    prof: dict[str, Any] = {
        "null_rate": round(1 - int(base["nn"]) / n, 6),
        "distinct_ratio": round(int(base["d"]) / n, 6),
        "numeric": False,
    }
    expr = None
    if _is_numeric(data_type):
        expr = f"CAST({c} AS DOUBLE)"
    elif data_type.upper() == "VARCHAR":
        rate = wh.scalar(
            f"SELECT avg(CASE WHEN TRY_CAST({c} AS DOUBLE) IS NOT NULL THEN 1 ELSE 0 END) FROM {fqn}"
        )
        prof["numeric_parse_rate"] = round(float(rate or 0), 6)
        if (rate or 0) >= 0.99:
            expr = f"TRY_CAST({c} AS DOUBLE)"
    if expr:
        s = wh.rows(
            f"SELECT avg({expr}) AS mean, stddev_pop({expr}) AS std, min({expr}) AS lo, "
            f"max({expr}) AS hi FROM {fqn}"
        )[0]
        prof.update(
            {
                "numeric": True,
                "mean": round(float(s["mean"] or 0), 6),
                "std": round(float(s["std"] or 0), 6),
                "min": float(s["lo"] or 0),
                "max": float(s["hi"] or 0),
            }
        )
    return prof


def snapshot(wh: Warehouse, tables: list[str] | None = None) -> None:
    now = clock.naive_utc(clock.now())
    for fqn in tables or PHYSICAL_RAW:
        wh.execute("DELETE FROM meta.schema_snapshots WHERE fqn = ?", [fqn])
        for col in wh.table_schema(fqn):
            prof = profile_column(wh, fqn, col.name, col.data_type)
            wh.execute(
                "INSERT INTO meta.schema_snapshots VALUES (?, ?, ?, ?, ?)",
                [fqn, col.name, col.data_type, dumps(prof), now],
            )


def name_similarity(a: str, b: str) -> float:
    ta, tb = set(a.lower().split("_")), set(b.lower().split("_"))
    jacc = len(ta & tb) / max(1, len(ta | tb))
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    prefix = (
        1.0
        if any(x.startswith(y) or y.startswith(x) for x in ta for y in tb if len(x) > 2 and len(y) > 2)
        else 0.0
    )
    return round(max(seq, jacc, 0.5 * seq + 0.5 * prefix), 4)


def profile_similarity(old: dict[str, Any], new: dict[str, Any]) -> float:
    if old.get("numeric") != new.get("numeric"):
        return 0.0
    keys = ["null_rate", "distinct_ratio"] + (["mean", "std", "min", "max"] if old.get("numeric") else [])
    diffs = []
    for k in keys:
        a, b = float(old.get(k, 0)), float(new.get(k, 0))
        scale = max(abs(a), abs(b), 1e-9)
        diffs.append(min(1.0, abs(a - b) / scale) if scale > 1e-9 else 0.0)
    return round(1 - sum(diffs) / len(diffs), 4)


def classify(
    fqn: str, old: dict[str, tuple[str, dict[str, Any]]], new: dict[str, tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Classify schema changes: rename (name + profile similarity), type change, add, drop."""
    changes: list[dict[str, Any]] = []
    dropped = [c for c in old if c not in new]
    added = [c for c in new if c not in old]
    matched_new: set[str] = set()
    for d in dropped:
        best: tuple[float, float, str] | None = None
        for a in added:
            if a in matched_new:
                continue
            ps = profile_similarity(old[d][1], new[a][1])
            ns = name_similarity(d, a)
            if ps >= 0.9 and ns >= 0.35 and (best is None or ps + ns > best[0] + best[1]):
                best = (ps, ns, a)
        if best:
            ps, ns, a = best
            matched_new.add(a)
            changes.append(
                {
                    "kind": "rename",
                    "table": fqn,
                    "old_column": d,
                    "new_column": a,
                    "profile_similarity": ps,
                    "name_similarity": ns,
                }
            )
            if old[d][0] != new[a][0]:
                changes.append(
                    {
                        "kind": "type_change",
                        "table": fqn,
                        "column": a,
                        "old_column": d,
                        "old_type": old[d][0],
                        "new_type": new[a][0],
                    }
                )
        else:
            changes.append({"kind": "drop", "table": fqn, "column": d, "old_type": old[d][0]})
    for a in added:
        if a not in matched_new:
            changes.append({"kind": "add", "table": fqn, "column": a, "new_type": new[a][0]})
    for c in old:
        if c in new and old[c][0] != new[c][0]:
            changes.append(
                {
                    "kind": "type_change",
                    "table": fqn,
                    "column": c,
                    "old_column": c,
                    "old_type": old[c][0],
                    "new_type": new[c][0],
                }
            )
    return changes


def check(wh: Warehouse, tables: list[str] | None = None) -> list[dict[str, Any]]:
    """Return a list of drift events: one per table with changes."""
    events: list[dict[str, Any]] = []
    for fqn in tables or PHYSICAL_RAW:
        snap = wh.rows(
            "SELECT column_name, data_type, profile_json FROM meta.schema_snapshots WHERE fqn = ?", [fqn]
        )
        if not snap:
            continue
        old = {str(r["column_name"]): (str(r["data_type"]), loads(r["profile_json"])) for r in snap}
        live = wh.table_schema(fqn)
        live_types = {c.name: c.data_type for c in live}
        if {k: v[0] for k, v in old.items()} == live_types:
            continue
        new = {
            c.name: (c.data_type, profile_column(wh, fqn, c.name, c.data_type))
            for c in live
            if c.name not in old or old[c.name][0] != c.data_type
        }
        new.update({c: (t, old[c][1]) for c, t in live_types.items() if c not in new})
        changes = classify(fqn, old, new)
        if changes:
            events.append({"table": fqn, "changes": changes})
    return events
