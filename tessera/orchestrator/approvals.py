"""Approval workflow: humans approve contracts and held patches."""

from __future__ import annotations

from tessera.governance import contracts as contracts_mod
from tessera.orchestrator.bus import Bus
from tessera.seed.domain import APPROVERS
from tessera.warehouse.base import Warehouse


class ApprovalError(PermissionError):
    pass


class Approvals:
    def __init__(self, wh: Warehouse, bus: Bus) -> None:
        self.wh = wh
        self.bus = bus

    def require_approver(self, user_id: str) -> None:
        if user_id not in APPROVERS:
            raise ApprovalError(f"{user_id} is not an approver")

    def approve_contract(self, contract_id: str, user_id: str) -> None:
        self.require_approver(user_id)
        _, row = contracts_mod.load(self.wh, contract_id)
        if row["status"] != "draft":
            raise ApprovalError(f"contract is {row['status']}, not draft")
        contracts_mod.set_status(self.wh, contract_id, "approved", approved_by=user_id)
        self.bus.emit("contract.approved", {"contract_id": contract_id, "approved_by": user_id})

    def reject_contract(self, contract_id: str, user_id: str) -> None:
        self.require_approver(user_id)
        contracts_mod.set_status(self.wh, contract_id, "rejected")
        self.wh.execute(
            "UPDATE meta.demand_intents SET status = 'rejected' WHERE contract_id = ?", [contract_id]
        )
