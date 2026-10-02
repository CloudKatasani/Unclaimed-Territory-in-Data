"""LLM access in two modes.

* ``mock``  — deterministic: ``fixtures/<prompt_id>/<variables_hash>.json`` if present, otherwise the
  rule-based responder in ``heuristics.py``. No network.
* ``live``  — Anthropic Messages API with the prompt template; output validated against the schema.
* ``record`` — like mock, but writes the heuristic output as a fixture file (``make fixtures``).

Every call returns the prompt hash and token usage so callers can write provenance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from tessera import clock, ids
from tessera.config import Settings, get_settings
from tessera.jsonutil import canonical, sha256
from tessera.llm import heuristics
from tessera.warehouse.base import Warehouse

T = TypeVar("T", bound=BaseModel)
LLM_DIR = Path(__file__).resolve().parent
PROMPTS = LLM_DIR / "prompts"
FIXTURES = LLM_DIR / "fixtures"


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult(Generic[T]):
    value: T
    prompt_id: str
    prompt_sha256: str
    variables_hash: str
    model_name: str
    source: str
    input_tokens: int = 0
    output_tokens: int = 0


def variables_hash(variables: dict[str, Any]) -> str:
    return sha256(canonical(variables))[:16]


def prompt_text(prompt_id: str) -> str:
    path = PROMPTS / f"{prompt_id}.md"
    if not path.exists():
        raise LLMError(f"missing prompt template {path}")
    return path.read_text()


def fixture_path(prompt_id: str, vhash: str) -> Path:
    return FIXTURES / prompt_id / f"{vhash}.json"


class LLM:
    def __init__(self, wh: Warehouse | None = None, settings: Settings | None = None) -> None:
        self.wh = wh
        self.settings = settings or get_settings()
        self.mode = self.settings.llm_mode
        self.log_calls = True
        self.overrides: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._client: Any = None

    def override(self, prompt_id: str, responses: list[dict[str, Any]], vhash: str = "*") -> None:
        """Test hook: queue raw responses for a prompt (consumed in order)."""
        self.overrides[(prompt_id, vhash)] = list(responses)

    def complete_json(self, prompt_id: str, variables: dict[str, Any], schema: type[T]) -> LLMResult[T]:
        template = prompt_text(prompt_id)
        p_sha = sha256(template)
        vhash = variables_hash(variables)
        raw: dict[str, Any] | None = None
        source = "fixture"
        model_name = "mock"
        in_tok = out_tok = 0
        for key in ((prompt_id, vhash), (prompt_id, "*")):
            queue = self.overrides.get(key)
            if queue:
                raw = queue.pop(0)
                source = "override"
                break
        if raw is None and self.mode == "live":
            raw, in_tok, out_tok = self._live(template, variables)
            source, model_name = "live", self.settings.llm_model
        elif raw is None:
            fp = fixture_path(prompt_id, vhash)
            if fp.exists() and self.mode != "record":
                raw = json.loads(fp.read_text())
            else:
                fn = heuristics.RESPONDERS.get(prompt_id)
                if fn is None:
                    raise LLMError(
                        f"no fixture {fp.relative_to(LLM_DIR)} and no mock responder for {prompt_id}"
                    )
                raw = fn(variables)
                source = "heuristic"
                if self.mode == "record":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
                    source = "fixture"
            in_tok = len(canonical(variables)) // 4
            out_tok = len(canonical(raw)) // 4
        value = schema.model_validate(raw)
        self._log(prompt_id, vhash, p_sha, model_name, in_tok, out_tok, source)
        return LLMResult(value, prompt_id, p_sha, vhash, model_name, source, in_tok, out_tok)

    def _log(self, prompt_id: str, vhash: str, p_sha: str, model: str, i: int, o: int, source: str) -> None:
        if self.wh is None or not self.log_calls:
            return
        self.wh.execute(
            "INSERT INTO meta.llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ids.new_id(self.wh, "llm_call"),
                prompt_id,
                vhash,
                p_sha,
                model,
                i,
                o,
                source,
                clock.naive_utc(clock.now()),
            ],
        )

    def _live(
        self, template: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], int, int]:  # pragma: no cover
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        prompt = template.replace("{{variables}}", json.dumps(variables, indent=2, default=str))
        msg = self._client.messages.create(
            model=self.settings.llm_model,
            max_tokens=4096,
            system="You are a component of a governed data platform. Reply with a single JSON object only.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError("live model returned no JSON object")
        return json.loads(match.group(0)), msg.usage.input_tokens, msg.usage.output_tokens
