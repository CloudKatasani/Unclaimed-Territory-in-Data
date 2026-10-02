"""Shared fixtures. The seed is generated once per session into a template and copied per test."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ["LLM_MODE"] = "mock"
os.environ.setdefault("TESSERA_FROZEN_CLOCK", "1")

_TMP = Path(os.environ.get("TESSERA_TEST_DIR", "/tmp/tessera-tests"))
_TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TESSERA_SEED_TEMPLATE", str(_TMP / "seed-template.duckdb"))
os.environ.setdefault("TESSERA_DB", str(_TMP / "tessera.duckdb"))

from tessera import clock  # noqa: E402
from tessera.config import reload_settings  # noqa: E402
from tessera.seed.generate import ensure_template  # noqa: E402
from tessera.warehouse.duckdb_wh import DuckDBWarehouse  # noqa: E402


@pytest.fixture(scope="session")
def template() -> Path:
    settings = reload_settings()
    force = os.environ.get("TESSERA_RESEED") == "1"
    return ensure_template(settings, force=force)


def _copy(template: Path, name: str) -> Path:
    dst = _TMP / f"{name}.duckdb"
    for p in (dst, Path(str(dst) + ".wal")):
        if p.exists():
            p.unlink()
    shutil.copyfile(template, dst)
    clock.reset()
    return dst


@pytest.fixture()
def wh(template: Path, request: pytest.FixtureRequest) -> Iterator[DuckDBWarehouse]:
    path = _copy(template, f"wh-{request.node.name}"[:80])
    w = DuckDBWarehouse(path)
    yield w
    w.close()
    path.unlink(missing_ok=True)
