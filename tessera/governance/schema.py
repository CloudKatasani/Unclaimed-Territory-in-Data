"""DDL for the `meta` schema (docs/02-data-model.md) plus the tables later milestones need."""

from __future__ import annotations

from tessera.warehouse.base import Warehouse

META_DDL: dict[str, str] = {
    "keys": "key_id VARCHAR PRIMARY KEY, public_key_hex VARCHAR, private_key_hex VARCHAR, created_at TIMESTAMP",
    "sequences": "name VARCHAR PRIMARY KEY, value BIGINT",
    "users": "user_id VARCHAR PRIMARY KEY, display_name VARCHAR, role VARCHAR, region VARCHAR",
    "contracts": (
        "contract_id VARCHAR PRIMARY KEY, product_fqn VARCHAR, version INTEGER, status VARCHAR, "
        "spec_yaml VARCHAR, origin VARCHAR, demand_intent_id VARCHAR, owner VARCHAR, created_at TIMESTAMP, "
        "approved_at TIMESTAMP, approved_by VARCHAR"
    ),
    "semantic_model": (
        "element_id VARCHAR PRIMARY KEY, element_type VARCHAR, name VARCHAR, product_fqn VARCHAR, "
        "expression VARCHAR, grain VARCHAR, certified BOOLEAN, certified_by VARCHAR, synonyms VARCHAR, "
        "description VARCHAR"
    ),
    "policies": (
        "policy_id VARCHAR PRIMARY KEY, kind VARCHAR, target_fqn VARCHAR, principal_attr VARCHAR, "
        "expression VARCHAR, classification VARCHAR, exempt_roles VARCHAR, target_column VARCHAR"
    ),
    "lineage_edges": (
        "src_fqn VARCHAR, src_column VARCHAR, dst_fqn VARCHAR, dst_column VARCHAR, transform_id VARCHAR, "
        "created_at TIMESTAMP"
    ),
    "criticality": "fqn VARCHAR PRIMARY KEY, criticality INTEGER, reason VARCHAR",
    "provenance": (
        "artifact_id VARCHAR PRIMARY KEY, artifact_kind VARCHAR, target_fqn VARCHAR, content_sha256 VARCHAR, "
        "agent VARCHAR, model_name VARCHAR, prompt_id VARCHAR, prompt_sha256 VARCHAR, input_refs VARCHAR, "
        "tests_run VARCHAR, gate_evidence VARCHAR, approved_by VARCHAR, created_at TIMESTAMP, "
        "signature VARCHAR, prev_artifact_id VARCHAR, content VARCHAR"
    ),
    "questions": (
        "question_id VARCHAR PRIMARY KEY, asked_by VARCHAR, text VARCHAR, asked_at TIMESTAMP, outcome VARCHAR, "
        "plan_json VARCHAR, confidence DOUBLE, intent_id VARCHAR, resolved_by_product VARCHAR, "
        "resolved_at TIMESTAMP, cert_id VARCHAR, ground_truth_hash VARCHAR"
    ),
    "demand_intents": (
        "intent_id VARCHAR PRIMARY KEY, label VARCHAR, question_ids VARCHAR, required_elements VARCHAR, "
        "gap_type VARCHAR, candidate_sources VARCHAR, demand_score DOUBLE, contract_id VARCHAR, status VARCHAR, "
        "join_path VARCHAR, created_at TIMESTAMP"
    ),
    "certificates": (
        "cert_id VARCHAR PRIMARY KEY, question_id VARCHAR, asked_by VARCHAR, metrics VARCHAR, "
        "policy_decisions VARCHAR, freshness VARCHAR, quality VARCHAR, lineage_nodes VARCHAR, "
        "agent_authored VARCHAR, agent_share_unapproved DOUBLE, verdict VARCHAR, rule_trace VARCHAR, "
        "result_hash VARCHAR, sentinel_status VARCHAR, signature VARCHAR, issued_at TIMESTAMP"
    ),
    # --- operational tables beyond docs/02 (needed to run the loop) ---
    "models": (
        "fqn VARCHAR PRIMARY KEY, kind VARCHAR, select_sql VARCHAR, version INTEGER, artifact_id VARCHAR, "
        "updated_at TIMESTAMP"
    ),
    "products": (
        "fqn VARCHAR PRIMARY KEY, title VARCHAR, owner VARCHAR, contract_id VARCHAR, last_refreshed_at TIMESTAMP, "
        "freshness_sla_hours DOUBLE, quality_score DOUBLE, failing_tests VARCHAR, refresh_status VARCHAR, "
        "held_reason VARCHAR"
    ),
    "catalog_columns": (
        "fqn VARCHAR, column_name VARCHAR, data_type VARCHAR, description VARCHAR, classification VARCHAR, "
        "searchable BOOLEAN"
    ),
    "foreign_keys": "src_fqn VARCHAR, src_column VARCHAR, dst_fqn VARCHAR, dst_column VARCHAR",
    "schema_snapshots": "fqn VARCHAR, column_name VARCHAR, data_type VARCHAR, profile_json VARCHAR, snapshot_at TIMESTAMP",
    "drift_events": (
        "event_id VARCHAR PRIMARY KEY, fqn VARCHAR, changes_json VARCHAR, detected_at TIMESTAMP, status VARCHAR, "
        "blast_radius_json VARCHAR, job_id VARCHAR"
    ),
    "patch_candidates": (
        "job_id VARCHAR, candidate_no INTEGER, label VARCHAR, source VARCHAR, expression VARCHAR, "
        "patch_sql VARCHAR, result_json VARCHAR, passed BOOLEAN, selected BOOLEAN"
    ),
    "patches": (
        "job_id VARCHAR PRIMARY KEY, event_id VARCHAR, target_fqn VARCHAR, status VARCHAR, selected_candidate INTEGER, "
        "evidence_json VARCHAR, artifact_id VARCHAR, merged_products VARCHAR, held_products VARCHAR, "
        "approved_by VARCHAR, created_at TIMESTAMP, decided_at TIMESTAMP"
    ),
    "build_jobs": (
        "job_id VARCHAR PRIMARY KEY, contract_id VARCHAR, product_fqn VARCHAR, status VARCHAR, timeline_json VARCHAR, "
        "sql VARCHAR, tests_json VARCHAR, attempts_json VARCHAR, artifact_ids VARCHAR, error VARCHAR, "
        "created_at TIMESTAMP"
    ),
    "notifications": (
        "notification_id VARCHAR PRIMARY KEY, user_id VARCHAR, kind VARCHAR, message VARCHAR, ref VARCHAR, "
        "created_at TIMESTAMP, read_at TIMESTAMP"
    ),
    "metrics": "metric_id VARCHAR PRIMARY KEY, name VARCHAR, value DOUBLE, context VARCHAR, recorded_at TIMESTAMP",
    "events": "event_id VARCHAR PRIMARY KEY, kind VARCHAR, payload VARCHAR, emitted_at TIMESTAMP",
    "rejections": "rejection_id VARCHAR PRIMARY KEY, source VARCHAR, reason VARCHAR, sql VARCHAR, created_at TIMESTAMP",
    "llm_calls": (
        "call_id VARCHAR PRIMARY KEY, prompt_id VARCHAR, variables_hash VARCHAR, prompt_sha256 VARCHAR, "
        "model_name VARCHAR, input_tokens INTEGER, output_tokens INTEGER, source VARCHAR, created_at TIMESTAMP"
    ),
}

# Tables that hold seed configuration rather than runtime activity.
SEED_TABLES = {
    "keys",
    "sequences",
    "users",
    "contracts",
    "semantic_model",
    "policies",
    "lineage_edges",
    "criticality",
    "provenance",
    "models",
    "products",
    "catalog_columns",
    "foreign_keys",
    "schema_snapshots",
    "metrics",
}


def create_meta(wh: Warehouse) -> None:
    wh.execute("CREATE SCHEMA IF NOT EXISTS meta")
    for name, cols in META_DDL.items():
        wh.execute(f"CREATE TABLE IF NOT EXISTS meta.{name} ({cols})")
