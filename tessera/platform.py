"""Wires the warehouse, governance spine, agents and bus together."""

from __future__ import annotations

from pathlib import Path

from tessera import clock
from tessera.agents.answer import AnswerAgent
from tessera.agents.certificate import CertificateService
from tessera.agents.demand_miner import DemandMiner
from tessera.config import Settings, get_settings
from tessera.governance.catalog import Catalog
from tessera.governance.lineage import Lineage
from tessera.governance.policy import PolicyEngine
from tessera.governance.provenance import ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.llm.client import LLM
from tessera.orchestrator.approvals import Approvals
from tessera.orchestrator.bus import Bus
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
        self.resume_clock()
        self._wire()

    def resume_clock(self) -> None:
        latest = self.wh.scalar(
            "SELECT max(t) FROM (SELECT max(created_at) AS t FROM meta.provenance UNION ALL "
            "SELECT max(emitted_at) FROM meta.events UNION ALL SELECT max(issued_at) FROM meta.certificates "
            "UNION ALL SELECT max(created_at) FROM meta.llm_calls)"
        )
        clock.resume_after(latest)

    def _wire(self) -> None:
        self.bus.subscribe("question.logged", self._maybe_mine)

    def _maybe_mine(self, payload: dict[str, object]) -> None:
        """Demand Miner trigger: every N new unresolved questions."""
        if len(self.demand.pending_questions()) >= self.settings.demand_batch_size:
            self.demand.run()

    def close(self) -> None:
        self.wh.close()
