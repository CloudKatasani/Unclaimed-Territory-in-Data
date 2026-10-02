"""Wires the warehouse, governance spine, agents and bus together."""

from __future__ import annotations

from pathlib import Path

from tessera import clock
from tessera.agents.answer import AnswerAgent
from tessera.agents.certificate import CertificateService
from tessera.agents.decision import DecisionEvaluator
from tessera.agents.demand_miner import DemandMiner
from tessera.agents.drift_healer import DriftHealer
from tessera.agents.narrative import NarrativeWriter
from tessera.agents.pipeline_builder import PipelineBuilder
from tessera.agents.recall import RecallJob
from tessera.config import Settings, get_settings
from tessera.governance.catalog import Catalog
from tessera.governance.lineage import Lineage
from tessera.governance.policy import PolicyEngine
from tessera.governance.provenance import ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.governance.sentinel import Sentinels
from tessera.llm.client import LLM
from tessera.market.listings import Marketplace
from tessera.market.trials import Trials
from tessera.orchestrator.approvals import Approvals
from tessera.orchestrator.bus import Bus
from tessera.publisher.gate import Publisher
from tessera.warehouse.duckdb_wh import DuckDBWarehouse


class Tessera:
    def __init__(self, db_path: Path | str | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.wh = DuckDBWarehouse(db_path or self.settings.db_path)
        self.bus = Bus(self.wh)
        self.llm = LLM(self.wh, self.settings)
        self.catalog = Catalog(self.wh)
        self.semantic = SemanticModel(self.wh)
        self.policy = PolicyEngine(self.wh)
        self.lineage = Lineage(self.wh)
        self.ledger = ProvenanceLedger(self.wh)
        self.certs = CertificateService(self.wh, self.lineage, self.ledger)
        self.answer = AnswerAgent(
            self.wh, self.llm, self.semantic, self.policy, self.lineage, self.certs, self.bus, self.settings
        )
        self.demand = DemandMiner(
            self.wh, self.llm, self.semantic, self.catalog, self.ledger, self.bus, self.settings
        )
        self.approvals = Approvals(self.wh, self.bus)
        self.builder = PipelineBuilder(self.wh, self.llm, self.policy, self.ledger, self.bus, self.settings)
        self.publisher = Publisher(self.wh, self.lineage, self.ledger, self.semantic, self.bus)
        self.healer = DriftHealer(self.wh, self.llm, self.lineage, self.bus, self.settings)
        self.market = Marketplace(self.wh, self.certs)
        self.recall = RecallJob(self.wh, self.semantic, self.policy)
        self.trials = Trials(self.wh, self.answer, self.market, self.ledger)
        self.sentinels = Sentinels(self.wh)
        self.healer.sentinel_check = self.sentinels.check_shadow
        self.answer.sentinels = self.sentinels
        self.load_sentinel_keys()
        self.decisions = DecisionEvaluator(self.wh, self.llm, self.semantic)
        self.market.decisions = self.decisions
        self.answer.decisions = self.decisions
        self.answer.narrator = NarrativeWriter(self.wh, self.llm)
        self.answer.pack_verdict = lambda pack: self.market.pack_evidence(pack)["verdict"]
        self.resume_clock()
        self._wire()

    def load_sentinel_keys(self) -> None:
        keys = self.sentinels.keys()
        self.answer.sentinel_keys = keys
        self.recall.sentinel_keys = keys

    def resume_clock(self) -> None:
        latest = self.wh.scalar(
            "SELECT max(t) FROM (SELECT max(created_at) AS t FROM meta.provenance UNION ALL "
            "SELECT max(emitted_at) FROM meta.events UNION ALL SELECT max(issued_at) FROM meta.certificates "
            "UNION ALL SELECT max(created_at) FROM meta.llm_calls)"
        )
        clock.resume_after(latest)

    def _wire(self) -> None:
        self.bus.subscribe("question.logged", self._maybe_mine)
        self.bus.subscribe("contract.approved", lambda p: self.builder.build(str(p["contract_id"])))
        self.bus.subscribe("build.ready_for_gate", lambda p: self.publisher.gate_build(str(p["job_id"])))
        self.bus.subscribe("product.published", self._on_published)
        self.bus.subscribe("drift.detected", lambda p: self.healer.heal(str(p["event_id"])))
        self.bus.subscribe("patch.ready_for_gate", lambda p: self.publisher.gate_patch(str(p["job_id"])))
        self.bus.subscribe("patch.held", self._on_held)

    # -- human actions -------------------------------------------------------------------------
    def approve_patch(self, job_id: str, user_id: str) -> dict[str, object]:
        self.approvals.require_approver(user_id)
        out = self.publisher.approve_patch(job_id, user_id)
        self.bus.drain()
        return out

    def reject_patch(self, job_id: str, user_id: str) -> dict[str, object]:
        self.approvals.require_approver(user_id)
        out = self.publisher.reject_patch(job_id, user_id)
        self.bus.drain()
        return out

    def simulate_drift(self) -> dict[str, object]:
        """`make drift`: the vendor changes the feed; the ingestion check detects it; the healer runs."""
        from tessera.seed.drift import apply_drift

        applied = apply_drift(self.wh)
        events = self.healer.check()
        self.bus.drain()
        return {"vendor_change": applied, "events": events}

    def _on_published(self, payload: dict[str, object]) -> None:
        self.semantic.refresh()
        fqn = str(payload["fqn"])
        if not payload.get("restated"):
            self.market.list_product(fqn)
            self.sentinels.expect([fqn])
            self.load_sentinel_keys()
        self.sentinels.verify(fqn)
        intent_id = payload.get("intent_id")
        if intent_id:
            self.demand.replay(str(intent_id), fqn, self.answer)
        if payload.get("restated"):
            self.recall.run(fqn, str(payload.get("reason") or "upstream data restated"))

    def _on_held(self, payload: dict[str, object]) -> None:
        """A held refresh is an SLA breach: record it with a signed status certificate."""
        for fqn in payload.get("products", []):  # type: ignore[attr-defined]
            nodes = self.lineage.upstream_columns(str(fqn), [c.name for c in self.wh.table_schema(str(fqn))])
            self.certs.issue(
                question_id=f"status:{fqn}",
                asked_by="subscribers",
                metrics=[],
                products=[str(fqn)],
                policy_decisions=[],
                lineage_nodes=nodes,
                result_hash=None,
            )

    def _maybe_mine(self, payload: dict[str, object]) -> None:
        """Demand Miner trigger: every N new unresolved questions."""
        if len(self.demand.pending_questions()) >= self.settings.demand_batch_size:
            self.demand.run()

    def close(self) -> None:
        self.wh.close()
