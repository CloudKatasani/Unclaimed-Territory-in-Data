"""Data product contracts: YAML <-> Pydantic, JSON-schema validation, persistence."""

from __future__ import annotations

from typing import Any, Literal

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field

from tessera import clock, ids
from tessera.warehouse.base import Warehouse


class SchemaColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str
    nullable: bool = True


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    sql: str


class ForeignKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    references: str


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["row_filter", "column_mask"]
    expression: str | None = None
    column: str | None = None
    applies_to_roles: list[str] = Field(default_factory=list)


class Quality(BaseModel):
    model_config = ConfigDict(extra="forbid")
    freshness_sla_hours: float = 24
    expectations: list[Expectation] = Field(default_factory=list)


class MeasureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    expression: str
    certified: bool = False


class SemanticSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    measures: list[MeasureSpec] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    synonyms: dict[str, list[str]] = Field(default_factory=dict)


class SlaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credits_per_breach: float = 0


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    product: str = Field(pattern=r"^dp\.[a-z][a-z0-9_]*$")
    version: int = 1
    title: str
    description: str = ""
    owner: str
    origin: Literal["human", "demand_miner"] = "human"
    demand_intent_id: str | None = None
    grain: list[str]
    primary_key: list[str]
    sources: list[str] = Field(min_length=1)
    join_path: list[str] = Field(default_factory=list)
    columns: list[SchemaColumn] = Field(alias="schema", min_length=1)
    quality: Quality = Field(default_factory=Quality)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    policies: list[PolicySpec] = Field(default_factory=list)
    semantic: SemanticSpec | None = None
    sla: SlaSpec | None = None
    criticality: int = Field(default=2, ge=1, le=4)
    # breaking-change metadata for a new version: old column/element name -> new name
    renames: dict[str, str] = Field(default_factory=dict)

    @property
    def table_name(self) -> str:
        return self.product.split(".", 1)[1]

    def column(self, name: str) -> SchemaColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    def to_yaml(self) -> str:
        data = self.model_dump(by_alias=True, exclude_none=True, mode="json")
        return yaml.safe_dump(data, sort_keys=False, width=110)


class ContractError(ValueError):
    pass


def json_schema() -> dict[str, Any]:
    return Contract.model_json_schema(by_alias=True)


def parse(spec_yaml: str) -> Contract:
    """Parse and validate a contract (JSON schema, then semantic checks)."""
    try:
        data = yaml.safe_load(spec_yaml)
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("contract must be a mapping")
    try:
        jsonschema.validate(data, json_schema())
    except jsonschema.ValidationError as exc:
        raise ContractError(f"schema validation failed: {exc.message} at {list(exc.absolute_path)}") from exc
    contract = Contract.model_validate(data)
    names = [c.name for c in contract.columns]
    if len(set(names)) != len(names):
        raise ContractError("duplicate column names")
    for key in contract.primary_key + contract.grain:
        if key not in names:
            raise ContractError(f"key column {key} not in schema")
    for fk in contract.foreign_keys:
        if fk.column not in names:
            raise ContractError(f"foreign key column {fk.column} not in schema")
    return contract


def save(
    wh: Warehouse,
    contract: Contract,
    status: str,
    intent_id: str | None = None,
    contract_id: str | None = None,
) -> str:
    cid = contract_id or ids.new_id(wh, "contract")
    wh.execute(
        "INSERT INTO meta.contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        [
            cid,
            contract.product,
            contract.version,
            status,
            contract.to_yaml(),
            contract.origin,
            intent_id,
            contract.owner,
            clock.naive_utc(clock.now()),
        ],
    )
    return cid


def load(wh: Warehouse, contract_id: str) -> tuple[Contract, dict[str, Any]]:
    rows = wh.rows("SELECT * FROM meta.contracts WHERE contract_id = ?", [contract_id])
    if not rows:
        raise KeyError(contract_id)
    return parse(str(rows[0]["spec_yaml"])), rows[0]


def latest_for(wh: Warehouse, product_fqn: str) -> tuple[Contract, dict[str, Any]] | None:
    rows = wh.rows(
        "SELECT contract_id FROM meta.contracts WHERE product_fqn = ? AND status = 'published' "
        "ORDER BY version DESC LIMIT 1",
        [product_fqn],
    )
    return load(wh, str(rows[0]["contract_id"])) if rows else None


def set_status(wh: Warehouse, contract_id: str, status: str, approved_by: str | None = None) -> None:
    if approved_by:
        wh.execute(
            "UPDATE meta.contracts SET status = ?, approved_by = ?, approved_at = ? WHERE contract_id = ?",
            [status, approved_by, clock.naive_utc(clock.now()), contract_id],
        )
    else:
        wh.execute("UPDATE meta.contracts SET status = ? WHERE contract_id = ?", [status, contract_id])
