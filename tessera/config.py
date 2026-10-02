"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(_env("TESSERA_DB", str(REPO_ROOT / "data/tessera.duckdb")))
    )
    seed_template: Path = field(
        default_factory=lambda: Path(_env("TESSERA_SEED_TEMPLATE", str(REPO_ROOT / "data/seed.duckdb")))
    )
    llm_mode: str = field(default_factory=lambda: _env("LLM_MODE", "mock"))
    llm_model: str = field(default_factory=lambda: _env("TESSERA_LLM_MODEL", "claude-sonnet-5-5"))
    # Minutes between AMI reads. 15 matches production; 60 is the documented laptop down-sample.
    read_interval_min: int = field(default_factory=lambda: int(_env("TESSERA_READ_INTERVAL_MIN", "60")))
    demo_now: str = field(default_factory=lambda: _env("DEMO_NOW", "2026-10-02T09:00:00-04:00"))
    frozen_clock: bool = field(default_factory=lambda: _env("TESSERA_FROZEN_CLOCK", "1") == "1")
    # Seed for the demo signing key so certificates are reproducible across resets.
    key_seed: str = field(default_factory=lambda: _env("TESSERA_KEY_SEED", "tessera-demo-key"))
    random_seed: int = 20261002
    # Demand miner
    cluster_distance: float = 0.45
    demand_batch_size: int = 5
    role_weights: dict[str, int] = field(
        default_factory=lambda: {
            "ops_manager": 3,
            "ops_director": 3,
            "analyst": 1,
            "data_engineer": 1,
            "admin": 0,
        }
    )
    # Drift gate thresholds by criticality
    drift_thresholds: dict[int, float] = field(
        default_factory=lambda: {4: 0.0001, 3: 0.001, 2: 0.01, 1: 0.05}
    )
    drift_window_days: int = 7
    max_repairs: int = 3
    confidence_floor: float = 0.6


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings
