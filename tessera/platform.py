"""Wires the warehouse, governance spine, agents and bus together."""

from __future__ import annotations

from pathlib import Path

from tessera import clock
from tessera.agents.answer import AnswerAgent
from tessera.agents.certificate import CertificateService
from tessera.config import Settings, get_settings
from tessera.governance.catalog import Catalog
from tessera.governance.lineage import Lineage
from tessera.governance.policy import PolicyEngine
from tessera.governance.provenance import ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.llm.client import LLM
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
        pass

    def close(self) -> None:
        self.wh.close()
