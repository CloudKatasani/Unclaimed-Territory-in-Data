"""Marketplace (docs/06): data products, agents and decision packs as listings with evidence."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

import yaml

from tessera import clock, ids
from tessera.agents.certificate import CertificateService
from tessera.governance import contracts as contracts_mod
from tessera.governance.policy import load_principal
from tessera.jsonutil import dumps, loads
from tessera.market.agents import (
    SEED_DIR,
    AgentContract,
    agent_scope,
    credits,
    evidence_sentence,
    latest_evaluation,
    load_agent_spec,
)
from tessera.warehouse.base import Warehouse

ORDER = {"release": 0, "release_with_warning": 1, "block": 2}
ENTITLED_ROLES = {"ops_manager", "analyst", "ops_director", "data_engineer", "admin"}
LEASE_DAYS = 90


class SubscriptionError(ValueError):
    pass


def product_listing_id(fqn: str) -> str:
    return f"product:{fqn}"


def agent_listing_id(agent: str, version: int) -> str:
    return f"agent:{agent}@{version}"


class Marketplace:
    def __init__(self, wh: Warehouse, certs: CertificateService) -> None:
        self.wh = wh
        self.certs = certs
        self.decisions: Any = None  # decision evaluator (M10) for threshold margins

    # -- listings ----------------------------------------------------------------------------------
    def upsert(self, listing_id: str, kind: str, fqn: str, version: int, status: str, spec_yaml: str) -> None:
        self.wh.execute("DELETE FROM meta.listings WHERE listing_id = ?", [listing_id])
        self.wh.execute(
            "INSERT INTO meta.listings VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
            [listing_id, kind, fqn, version, status, spec_yaml, clock.naive_utc(clock.now())],
        )

    def list_product(self, fqn: str) -> None:
        found = contracts_mod.latest_for(self.wh, fqn)
        if found:
            contract, _ = found
            self.upsert(
                product_listing_id(fqn),
                "data_product",
                fqn,
                contract.version,
                "published",
                contract.to_yaml(),
            )

    def seed(self) -> None:
        for r in self.wh.rows("SELECT fqn FROM meta.products ORDER BY fqn"):
            self.list_product(str(r["fqn"]))
        for text in sorted(p.read_text() for p in SEED_DIR.glob("agent_*.yaml")):
            spec = load_agent_spec(text)
            status = "published" if spec.version == 1 else "candidate"
            self.upsert(
                agent_listing_id(spec.agent, spec.version),
                "agent",
                f"agent.{spec.agent}",
                spec.version,
                status,
                text,
            )
        for p in sorted(SEED_DIR.glob("decision_pack_*.yaml")):
            data = yaml.safe_load(p.read_text())
            self.upsert(
                f"pack:{data['decision']}",
                "decision_pack",
                f"pack.{data['decision']}",
                1,
                "published",
                p.read_text(),
            )

    def get(self, listing_id: str) -> dict[str, Any]:
        rows = self.wh.rows("SELECT * FROM meta.listings WHERE listing_id = ?", [listing_id])
        if not rows:
            raise KeyError(listing_id)
        return rows[0]

    def agent_spec(self, agent: str, version: int | None = None) -> AgentContract:
        if version is None:
            r = self.wh.rows(
                "SELECT spec_yaml FROM meta.listings WHERE kind = 'agent' AND fqn = ? AND "
                "status = 'published' ORDER BY version DESC LIMIT 1",
                [f"agent.{agent}"],
            )
        else:
            r = self.wh.rows(
                "SELECT spec_yaml FROM meta.listings WHERE listing_id = ?", [agent_listing_id(agent, version)]
            )
        if not r:
            raise KeyError(agent)
        return load_agent_spec(str(r[0]["spec_yaml"]))

    def listings(self, kind: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM meta.listings WHERE status IN ('published', 'candidate')"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        out = []
        for r in self.wh.rows(sql + " ORDER BY kind, fqn, version", params):
            if q and q.lower() not in (str(r["fqn"]) + str(r["spec_yaml"])).lower():
                continue
            out.append(self.card(r))
        return out

    # -- evidence ---------------------------------------------------------------------------------------
    def card(self, r: dict[str, Any]) -> dict[str, Any]:
        kind = str(r["kind"])
        spec = yaml.safe_load(str(r["spec_yaml"]))
        evidence: dict[str, Any]
        if kind == "data_product":
            evidence = self.product_evidence(str(r["fqn"]))
            title = spec.get("title", r["fqn"])
        elif kind == "agent":
            evidence = self.agent_evidence(load_agent_spec(str(r["spec_yaml"])))
            title = spec.get("title", r["fqn"])
        else:
            evidence = self.pack_evidence(spec)
            title = spec.get("title", r["fqn"])
        self.wh.execute(
            "UPDATE meta.listings SET evidence_json = ? WHERE listing_id = ?",
            [dumps(evidence), r["listing_id"]],
        )
        return {
            "listing_id": r["listing_id"],
            "kind": kind,
            "fqn": r["fqn"],
            "version": r["version"],
            "status": r["status"],
            "title": title,
            "description": spec.get("description") or spec.get("purpose"),
            "evidence": evidence,
            "subscribers": self.subscribers(str(r["listing_id"])),
        }

    def product_evidence(self, fqn: str) -> dict[str, Any]:
        if not self.wh.table_exists(fqn):
            return {"available": False}
        a = self.certs.assess([fqn])
        f = a["freshness"].get(fqn, {})
        q = a["quality"].get(fqn, {})
        demand = self.wh.scalar(
            "SELECT max(i.demand_score) FROM meta.demand_intents i JOIN meta.contracts c "
            "ON c.contract_id = i.contract_id WHERE c.product_fqn = ?",
            [fqn],
        )
        since = clock.naive_utc(clock.now() - timedelta(days=30))
        sla = self.wh.scalar(
            "SELECT coalesce(sum(credits), 0) FROM meta.sla_ledger WHERE fqn = ? AND settled_at >= ?",
            [fqn, since],
        )
        crit = self.wh.scalar("SELECT criticality FROM meta.criticality WHERE fqn = ?", [fqn])
        return {
            "available": True,
            "verdict": a["verdict"],
            "verdict_reasons": [t["reason"] for t in a["rule_trace"] if t["outcome"] == a["verdict"]],
            "freshness": f,
            "quality_score": q.get("score"),
            "demand_score": demand,
            "sla_credits_30d": float(sla or 0),
            "criticality": crit,
        }

    def agent_evidence(self, spec: AgentContract) -> dict[str, Any]:
        ev = latest_evaluation(self.wh, spec.agent, spec.version)
        trial = self.wh.rows(
            "SELECT * FROM meta.agent_trials WHERE agent = ? AND candidate_version = ? "
            "ORDER BY started_at DESC LIMIT 1",
            [spec.agent, spec.version],
        )
        out: dict[str, Any] = {
            "required_products": spec.requires.data_products,
            "policies": spec.requires.policies,
            "purpose": spec.requires.purpose,
            "budget": spec.budget.model_dump(),
            "bundle_products": agent_scope(self.wh, spec),
        }
        if ev:
            out.update(
                {
                    "accuracy": ev["accuracy"],
                    "n": ev["n"],
                    "cost_per_run": ev["avg_cost"],
                    "p95_latency": ev["p95_latency"],
                    "sentence": evidence_sentence(ev),
                    "meets_minimum": ev["accuracy"] >= spec.evaluation.min_verified_accuracy,
                    "evaluated_at": clock.iso(ev["evaluated_at"].replace(tzinfo=UTC)),
                }
            )
        if trial:
            t = trial[0]
            out["trial"] = {
                "trial_id": t["trial_id"],
                "status": t["status"],
                **(loads(t["summary_json"]) or {}),
            }
        return out

    def pack_evidence(self, spec: dict[str, Any]) -> dict[str, Any]:
        products = list(spec["bundle"]["data_products"])
        verdicts = {}
        for p in products:
            e = self.product_evidence(p)
            verdicts[p] = e.get("verdict", "unavailable")
        known = [v for v in verdicts.values() if v in ORDER]
        worst = max(known, key=lambda v: ORDER[v]) if known else "release_with_warning"
        if len(known) < len(products) and ORDER[worst] < ORDER["release_with_warning"]:
            worst = "release_with_warning"
        thresholds = []
        for t in spec.get("thresholds", []):
            entry = {k: t[k] for k in ("metric", "scope", "comparator", "value", "meaning")}
            if self.decisions is not None:
                entry.update(self.decisions.margin_summary(spec, t))
            thresholds.append(entry)
        return {
            "verdict": worst,
            "product_verdicts": verdicts,
            "thresholds": thresholds,
            "agents": spec["bundle"].get("agents", []),
            "dashboards": spec["bundle"].get("dashboards", []),
            "aggregation": spec.get("certificate", {}).get("aggregation", "worst_of_bundle"),
        }

    # -- subscriptions (entitlement bundles, idea 14) ---------------------------------------------------
    def subscribers(self, listing_id: str) -> int:
        return int(
            self.wh.scalar(
                "SELECT count(*) FROM meta.subscriptions WHERE listing_id = ? AND status = 'active'",
                [listing_id],
            )
            or 0
        )

    def resolve(self, listing_id: str) -> tuple[list[str], AgentContract | None, str]:
        r = self.get(listing_id)
        if r["kind"] == "agent":
            spec = load_agent_spec(str(r["spec_yaml"]))
            return (
                sorted(set(spec.requires.data_products) | set(agent_scope(self.wh, spec))),
                spec,
                spec.requires.purpose,
            )
        if r["kind"] == "decision_pack":
            data = yaml.safe_load(str(r["spec_yaml"]))
            products = set(data["bundle"]["data_products"])
            agent = None
            for a in data["bundle"].get("agents", []):
                agent = self.agent_spec(a)
                products |= set(agent_scope(self.wh, agent))
            return sorted(products), agent, "decision_support"
        return [str(r["fqn"])], None, "self_service"

    def subscribe(self, listing_id: str, user_id: str) -> dict[str, Any]:
        principal = load_principal(self.wh, user_id)
        existing = self.wh.rows(
            "SELECT bundle_id FROM meta.subscriptions WHERE listing_id = ? AND principal = ? "
            "AND status IN ('active', 'pending_approval')",
            [listing_id, user_id],
        )
        if existing:
            return self.bundle(str(existing[0]["bundle_id"]))
        products, agent, purpose = self.resolve(listing_id)
        available = {str(r["fqn"]) for r in self.wh.rows("SELECT fqn FROM meta.products")}
        missing = [{"fqn": p, "reason": "not yet published"} for p in products if p not in available]
        if principal.role not in ENTITLED_ROLES:
            missing += [{"fqn": p, "reason": f"role {principal.role} not entitled"} for p in products]
        bundle_id = ids.new_id(self.wh, "bundle")
        status = "pending_approval" if missing else "active"
        now = clock.naive_utc(clock.now())
        self.wh.execute(
            "INSERT INTO meta.subscriptions VALUES (?, ?, ?, ?, ?, ?, NULL)",
            [bundle_id, listing_id, user_id, status, dumps(missing), now],
        )
        if status == "active":
            for p in products:
                scope: dict[str, Any] = {"read": True}
                for pol in self.wh.rows(
                    "SELECT * FROM meta.policies WHERE target_fqn = ? AND kind = 'row_filter'", [p]
                ):
                    exempt = set(loads(pol["exempt_roles"]) or [])
                    if principal.role not in exempt:
                        attr = str(pol["principal_attr"])
                        scope["row_filter"] = f"{attr} = '{principal.attr(attr)}'"
                self.wh.execute(
                    "INSERT INTO meta.leases VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL)",
                    [
                        ids.new_id(self.wh, "lease"),
                        user_id,
                        p,
                        purpose,
                        dumps(scope),
                        now + timedelta(days=LEASE_DAYS),
                        bundle_id,
                    ],
                )
            budget = agent.budget if agent else None
            self.wh.execute(
                "INSERT INTO meta.budgets VALUES (?, ?, ?, ?, ?, 0, 0, 'active')",
                [
                    bundle_id,
                    user_id,
                    agent.agent if agent else None,
                    budget.max_credits_per_run if budget else 0.0,
                    budget.max_runs_per_day if budget else 0,
                ],
            )
        return self.bundle(bundle_id)

    def unsubscribe(self, listing_id: str, user_id: str) -> int:
        bundles = [
            str(r["bundle_id"])
            for r in self.wh.rows(
                "SELECT bundle_id FROM meta.subscriptions WHERE listing_id = ? AND principal = ? AND status <> 'revoked'",
                [listing_id, user_id],
            )
        ]
        now = clock.naive_utc(clock.now())
        n = 0
        for b in bundles:
            n += int(
                self.wh.scalar(
                    "SELECT count(*) FROM meta.leases WHERE bundle_id = ? AND status = 'active'", [b]
                )
                or 0
            )
            self.wh.execute(
                "UPDATE meta.leases SET status = 'revoked', revoked_at = ? WHERE bundle_id = ?", [now, b]
            )
            self.wh.execute(
                "UPDATE meta.subscriptions SET status = 'revoked', revoked_at = ? WHERE bundle_id = ?",
                [now, b],
            )
            self.wh.execute("UPDATE meta.budgets SET status = 'closed' WHERE bundle_id = ?", [b])
        return n

    def bundle(self, bundle_id: str) -> dict[str, Any]:
        sub = self.wh.rows("SELECT * FROM meta.subscriptions WHERE bundle_id = ?", [bundle_id])[0]
        leases = self.wh.rows("SELECT * FROM meta.leases WHERE bundle_id = ? ORDER BY fqn", [bundle_id])
        budget = self.wh.rows("SELECT * FROM meta.budgets WHERE bundle_id = ?", [bundle_id])
        return {
            "bundle_id": bundle_id,
            "listing_id": sub["listing_id"],
            "principal": sub["principal"],
            "status": sub["status"],
            "missing": loads(sub["missing_json"]) or [],
            "leases": [{**lease, "scope_json": loads(lease["scope_json"])} for lease in leases],
            "budget": budget[0] if budget else None,
        }

    def access(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.wh.rows(
            "SELECT bundle_id FROM meta.subscriptions WHERE principal = ? ORDER BY created_at", [user_id]
        )
        return [self.bundle(str(r["bundle_id"])) for r in rows]

    def charge(self, bundle_id: str, tokens: int) -> None:
        self.wh.execute(
            "UPDATE meta.budgets SET credits_used = credits_used + ?, runs_used = runs_used + 1 "
            "WHERE bundle_id = ?",
            [credits(tokens), bundle_id],
        )

    def ledger(self, consumer: str | None = None) -> list[dict[str, Any]]:
        if consumer:
            return self.wh.rows(
                "SELECT * FROM meta.sla_ledger WHERE consumer = ? ORDER BY settled_at", [consumer]
            )
        return self.wh.rows("SELECT * FROM meta.sla_ledger ORDER BY settled_at")
