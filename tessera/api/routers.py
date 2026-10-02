"""REST API (docs/01): ask, questions, demand, contracts, builds, drift, lineage, certs."""

from __future__ import annotations

import difflib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tessera.agents.answer import serialise
from tessera.api import state
from tessera.api.deps import current_user
from tessera.api.serialise import row, rows
from tessera.governance import contracts as contracts_mod
from tessera.orchestrator.approvals import ApprovalError
from tessera.publisher.gate import GateRejected
from tessera.seed.domain import USERS

router = APIRouter()


class AskBody(BaseModel):
    question: str


def _forbidden(exc: Exception) -> HTTPException:
    return HTTPException(403, str(exc))


# -- users / notifications -------------------------------------------------------------------------
@router.get("/users")
def users() -> list[dict[str, Any]]:
    return USERS


@router.get("/notifications")
def notifications(user: str = Depends(current_user)) -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(
            app.wh.rows("SELECT * FROM meta.notifications WHERE user_id = ? ORDER BY created_at DESC", [user])
        )


@router.post("/notifications/{nid}/read")
def read_notification(nid: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        app.wh.execute(
            "UPDATE meta.notifications SET read_at = now() WHERE notification_id = ? AND user_id = ?",
            [nid, user],
        )
        return {"ok": True}


# -- ask ----------------------------------------------------------------------------------------------
@router.post("/ask")
def ask(body: AskBody, user: str = Depends(current_user)) -> dict[str, Any]:
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    with state.platform() as app:
        return app.answer.ask(body.question.strip(), user).as_dict()


@router.get("/suggested-questions")
def suggested() -> list[str]:
    return [
        "What was SAIDI by substation in my region last month?",
        "Which meters reported zero usage during outages last month?",
        "What is SAIFI by feeder for the last 3 months?",
        "Daily kWh for feeder F-017 last week",
    ]


@router.get("/questions")
def questions(user: str | None = None) -> list[dict[str, Any]]:
    with state.platform() as app:
        sql = "SELECT * FROM meta.questions"
        params: list[Any] = []
        if user:
            sql += " WHERE asked_by = ?"
            params.append(user)
        return rows(app.wh.rows(sql + " ORDER BY asked_at DESC", params), ("plan_json",))


# -- certificates -------------------------------------------------------------------------------------
@router.get("/certs/public-key")
def public_key() -> dict[str, str]:
    with state.platform() as app:
        return {"algorithm": "Ed25519", "public_key_hex": app.certs.signer.public_key_hex}


@router.get("/certs/{cert_id}")
def cert(cert_id: str) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return app.certs.get(cert_id)
        except KeyError as exc:
            raise HTTPException(404, "certificate not found") from exc


@router.get("/certs/{cert_id}/verify")
def verify_cert(cert_id: str) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return app.certs.verify(cert_id)
        except KeyError as exc:
            raise HTTPException(404, "certificate not found") from exc


# -- demand -------------------------------------------------------------------------------------------
def _intent(app: Any, r: dict[str, Any]) -> dict[str, Any]:
    it = app.demand.intent(str(r["intent_id"]))
    qids = it["question_ids"]
    qs = app.wh.rows(
        f"SELECT question_id, asked_by, text, outcome, resolved_by_product FROM meta.questions "
        f"WHERE question_id IN ({', '.join('?' * len(qids))}) ORDER BY asked_at",
        qids,
    )
    out = row(it)
    out["questions"] = rows(qs)
    out["closure"] = app.demand.closure(str(r["intent_id"]))
    if it["contract_id"]:
        out["contract_status"] = app.wh.scalar(
            "SELECT status FROM meta.contracts WHERE contract_id = ?", [it["contract_id"]]
        )
    return out


@router.get("/demand/intents")
def intents() -> list[dict[str, Any]]:
    with state.platform() as app:
        rs = app.wh.rows("SELECT intent_id FROM meta.demand_intents ORDER BY demand_score DESC, created_at")
        return [_intent(app, r) for r in rs]


@router.get("/demand/unresolved")
def unresolved() -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(app.demand.pending_questions(), ("plan_json",))


@router.post("/demand/mine")
def mine() -> dict[str, Any]:
    with state.platform() as app:
        return {"intents": app.demand.mine()}


@router.post("/demand/intents/{intent_id}/draft")
def draft(intent_id: str) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return {"contract_id": app.demand.draft_contract(intent_id)}
        except contracts_mod.ContractError as exc:
            raise HTTPException(422, str(exc)) from exc


# -- contracts ------------------------------------------------------------------------------------------
@router.get("/contracts")
def contracts() -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(
            app.wh.rows(
                "SELECT contract_id, product_fqn, version, status, origin, owner, created_at, "
                "approved_by FROM meta.contracts ORDER BY created_at DESC"
            )
        )


@router.get("/contracts/{contract_id}")
def contract(contract_id: str) -> dict[str, Any]:
    with state.platform() as app:
        r = app.wh.rows("SELECT * FROM meta.contracts WHERE contract_id = ?", [contract_id])
        if not r:
            raise HTTPException(404, "contract not found")
        c = r[0]
        prev = app.wh.rows(
            "SELECT spec_yaml, version FROM meta.contracts WHERE product_fqn = ? AND version < ? "
            "ORDER BY version DESC LIMIT 1",
            [c["product_fqn"], c["version"]],
        )
        old = str(prev[0]["spec_yaml"]) if prev else ""
        diff = list(
            difflib.unified_diff(
                old.splitlines(),
                str(c["spec_yaml"]).splitlines(),
                "previous" if prev else "(none)",
                f"v{c['version']}",
                lineterm="",
            )
        )
        return {**row(c), "diff": diff}


@router.post("/contracts/{contract_id}/approve")
def approve(contract_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            app.approvals.approve_contract(contract_id, user)
        except ApprovalError as exc:
            raise _forbidden(exc) from exc
    with state.platform() as app:
        job = app.wh.rows(
            "SELECT job_id, status FROM meta.build_jobs WHERE contract_id = ? ORDER BY created_at "
            "DESC LIMIT 1",
            [contract_id],
        )
        return {"approved": True, "build": rows(job)[0] if job else None}


@router.post("/contracts/{contract_id}/reject")
def reject(contract_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            app.approvals.reject_contract(contract_id, user)
        except ApprovalError as exc:
            raise _forbidden(exc) from exc
        return {"rejected": True}


# -- builds -------------------------------------------------------------------------------------------------
@router.get("/builds")
def builds() -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(
            app.wh.rows(
                "SELECT job_id, contract_id, product_fqn, status, timeline_json, created_at "
                "FROM meta.build_jobs ORDER BY created_at DESC"
            ),
            ("timeline_json",),
        )


@router.get("/builds/{job_id}")
def build(job_id: str) -> dict[str, Any]:
    with state.platform() as app:
        try:
            job = app.builder.get(job_id)
        except IndexError as exc:
            raise HTTPException(404, "build not found") from exc
        prov = [
            app.ledger.public_view(p)
            for a in (job["artifact_ids"] or [])
            if (p := app.ledger.get(str(a))) is not None
        ]
        return {**row(job), "provenance": prov}


# -- drift --------------------------------------------------------------------------------------------------
@router.get("/drift/events")
def drift_events() -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(
            app.wh.rows("SELECT * FROM meta.drift_events ORDER BY detected_at DESC"),
            ("changes_json", "blast_radius_json"),
        )


@router.get("/drift/events/{event_id}")
def drift_event(event_id: str) -> dict[str, Any]:
    with state.platform() as app:
        try:
            ev = app.healer.event(event_id)
        except IndexError as exc:
            raise HTTPException(404, "event not found") from exc
        patch = None
        if ev["job_id"]:
            try:
                patch = app.publisher.patch(str(ev["job_id"]))
            except KeyError:
                patch = None
        cands = app.wh.rows(
            "SELECT job_id, candidate_no, label, source, expression, patch_sql, result_json, "
            "passed, selected FROM meta.patch_candidates WHERE job_id = ? ORDER BY candidate_no",
            [ev["job_id"]],
        )
        return {
            "event": row(ev, ()),
            "patch": row(patch) if patch else None,
            "candidates": rows(cands, ("result_json",)),
        }


@router.post("/drift/simulate")
def drift_simulate() -> dict[str, Any]:
    with state.platform() as app:
        return app.simulate_drift()


@router.post("/drift/check")
def drift_check() -> dict[str, Any]:
    with state.platform() as app:
        return {"events": app.healer.check()}


@router.post("/drift/patches/{job_id}/approve")
def approve_patch(job_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return row(app.approve_patch(job_id, user))
        except ApprovalError as exc:
            raise _forbidden(exc) from exc
        except GateRejected as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/drift/patches/{job_id}/reject")
def reject_patch(job_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return row(app.reject_patch(job_id, user))
        except ApprovalError as exc:
            raise _forbidden(exc) from exc


# -- products / lineage / provenance ------------------------------------------------------------------------
@router.get("/products")
def products() -> list[dict[str, Any]]:
    with state.platform() as app:
        rs = app.wh.rows(
            "SELECT p.*, c.criticality FROM meta.products p LEFT JOIN meta.criticality c "
            "USING (fqn) ORDER BY fqn"
        )
        return rows(rs, ("failing_tests",))


@router.get("/products/{fqn}/preview")
def preview(fqn: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        if not fqn.startswith("dp.") or not app.wh.table_exists(fqn):
            raise HTTPException(404, "unknown product")
        from tessera.governance.policy import load_principal

        sql, decisions = app.policy.rewrite(f"SELECT * FROM {fqn} LIMIT 50", load_principal(app.wh, user))
        return {"sql": sql, "rows": serialise(app.wh.rows(sql)), "policies": [d.as_dict() for d in decisions]}


@router.get("/lineage/graph")
def lineage_graph(fqn: str | None = None) -> dict[str, Any]:
    with state.platform() as app:
        edges = app.lineage.table_graph()
        if fqn:
            keep = set(app.lineage.upstream_tables(fqn)) | {fqn}
            edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
        nodes = sorted({e["src"] for e in edges} | {e["dst"] for e in edges} | ({fqn} if fqn else set()))
        crit = {str(r["fqn"]): int(r["criticality"]) for r in app.wh.rows("SELECT * FROM meta.criticality")}
        out_nodes = []
        for n in nodes:
            arts = [h for k in ("model_sql", "patch") if (h := app.ledger.head(n, k)) is not None]
            status = "source"
            if arts:
                agent_arts = [a for a in arts if not str(a["agent"]).startswith("human:")]
                if any(a["approved_by"] is None for a in agent_arts):
                    status = "agent_unapproved"
                elif agent_arts:
                    status = "agent_approved"
                else:
                    status = "human"
            out_nodes.append(
                {
                    "id": n,
                    "criticality": crit.get(n),
                    "status": status,
                    "artifacts": [
                        {
                            "artifact_id": a["artifact_id"],
                            "kind": a["artifact_kind"],
                            "agent": a["agent"],
                            "approved_by": a["approved_by"],
                        }
                        for a in arts
                    ],
                }
            )
        return {"nodes": out_nodes, "edges": edges}


@router.get("/lineage/columns")
def lineage_columns(fqn: str, column: str) -> dict[str, Any]:
    with state.platform() as app:
        return {
            "upstream": app.lineage.upstream_columns(fqn, [column]),
            "downstream": app.lineage.downstream_columns(fqn, column),
        }


@router.get("/provenance")
def provenance(target: str | None = None) -> list[dict[str, Any]]:
    with state.platform() as app:
        sql = "SELECT * FROM meta.provenance"
        params: list[Any] = []
        if target:
            sql += " WHERE target_fqn = ?"
            params.append(target)
        return [app.ledger.public_view(p) for p in app.wh.rows(sql + " ORDER BY created_at DESC", params)]


@router.get("/provenance/verify")
def provenance_verify() -> dict[str, Any]:
    with state.platform() as app:
        return app.ledger.verify()


@router.get("/metrics")
def metrics() -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(app.wh.rows("SELECT * FROM meta.metrics ORDER BY recorded_at"), ("context",))


@router.get("/events")
def events(limit: int = 100) -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(
            app.wh.rows("SELECT * FROM meta.events ORDER BY emitted_at DESC LIMIT ?", [limit]), ("payload",)
        )


# -- demo -----------------------------------------------------------------------------------------------------
@router.post("/demo/reset")
def demo_reset() -> dict[str, Any]:
    app = state.reset()
    return {"ok": True, "products": [p["fqn"] for p in app.publisher.products()]}


# -- marketplace (docs/06) ---------------------------------------------------------------------------------
@router.get("/market/listings")
def market_listings(kind: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    with state.platform() as app:
        return [row(c) for c in app.market.listings(kind, q)]


@router.get("/market/listings/{listing_id}")
def market_listing(listing_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            r = app.market.get(listing_id)
        except KeyError as exc:
            raise HTTPException(404, "listing not found") from exc
        card = app.market.card(r)
        history = []
        if r["kind"] == "agent":
            history = rows(
                app.wh.rows(
                    "SELECT evaluation_id, version, n, accuracy, avg_cost, p95_latency, evaluated_at, signature "
                    "FROM meta.agent_evaluations WHERE agent = ? ORDER BY evaluated_at",
                    [str(r["fqn"]).split(".", 1)[1]],
                )
            )
        subscribed = app.wh.rows(
            "SELECT bundle_id, status FROM meta.subscriptions WHERE listing_id = ? AND "
            "principal = ? AND status <> 'revoked'",
            [listing_id, user],
        )
        return {
            **row(card),
            "spec_yaml": r["spec_yaml"],
            "history": history,
            "subscription": rows(subscribed)[0] if subscribed else None,
        }


@router.post("/market/listings/{listing_id}/subscribe")
def market_subscribe(listing_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        try:
            return row(app.market.subscribe(listing_id, user))
        except KeyError as exc:
            raise HTTPException(404, "listing not found") from exc


@router.post("/market/listings/{listing_id}/unsubscribe")
def market_unsubscribe(listing_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        return {"revoked_leases": app.market.unsubscribe(listing_id, user)}


@router.get("/market/ledger")
def market_ledger(consumer: str | None = None) -> list[dict[str, Any]]:
    with state.platform() as app:
        return rows(app.market.ledger(consumer))


@router.get("/access")
def access(user: str = Depends(current_user)) -> list[dict[str, Any]]:
    with state.platform() as app:
        return [
            {**b, "leases": rows(b["leases"]), "budget": row(b["budget"]) if b["budget"] else None}
            for b in app.market.access(user)
        ]


# -- recall inbox (docs/07 capability 1) --------------------------------------------------------------------
@router.get("/inbox")
def inbox(user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        notices = rows(app.recall.inbox(user), ("delta_json",))
        for n in notices:
            snap = app.wh.rows(
                "SELECT question, kind, served_at, plan_json, result_json FROM meta.answer_snapshots "
                "WHERE cert_id = ?",
                [n["cert_id"]],
            )
            n["original"] = row(snap[0], ("plan_json", "result_json")) if snap else None
        return {"open": app.recall.open_count(user), "notices": notices}


@router.post("/inbox/{notice_id}/ack")
def inbox_ack(notice_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    with state.platform() as app:
        app.recall.acknowledge(notice_id, user)
        return {"open": app.recall.open_count(user)}


@router.get("/exports")
def exports(user: str = Depends(current_user)) -> list[dict[str, Any]]:
    with state.platform() as app:
        rs = app.wh.rows(
            "SELECT s.cert_id, s.question, s.kind, s.served_at, s.result_json, s.plan_json, "
            "(SELECT count(*) FROM meta.recall_notices n WHERE n.cert_id = s.cert_id) AS restated "
            "FROM meta.answer_snapshots s WHERE s.consumer = ? ORDER BY s.served_at DESC",
            [user],
        )
        return rows(rs, ("result_json", "plan_json"))
