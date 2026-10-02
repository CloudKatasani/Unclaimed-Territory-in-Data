"""Demo conductor helpers (docs/08). Fixture recording lives here because it replays the demo."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from tessera import clock
from tessera.config import get_settings
from tessera.llm.client import FIXTURES
from tessera.platform import Tessera
from tessera.seed.generate import ensure_template


def record_fixtures() -> dict[str, Any]:
    """Replay the demo on a scratch copy in LLM_MODE=record and write fixture files."""
    from tessera.demo import storyline

    settings = get_settings()
    tpl = ensure_template(settings)
    existing = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json")}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "record.duckdb"
        shutil.copyfile(tpl, path)
        clock.reset()
        rec = Tessera(path)
        rec.llm.mode = "record"
        try:
            result = storyline.run_all(rec)
            calls = rec.wh.rows("SELECT prompt_id, variables_hash FROM meta.llm_calls ORDER BY created_at")
        finally:
            rec.close()
    files = sorted({f"{c['prompt_id']}/{c['variables_hash']}.json" for c in calls})
    stale = sorted(existing - set(files))
    for rel in stale:  # fixtures the demo no longer reaches
        (FIXTURES / rel).unlink()
    return {
        "storyline": result,
        "fixtures": files,
        "new": sorted(set(files) - existing),
        "removed": stale,
        "total_files": len(existing | set(files)),
    }
