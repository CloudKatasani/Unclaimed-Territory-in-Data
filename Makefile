PY ?= .venv/bin/python
UV ?= uv

.PHONY: setup seed test lint typecheck demo api ui reset drift fixtures check

setup:
	test -d .venv || $(UV) venv .venv -p 3.11
	$(UV) pip install -p $(PY) -e ".[dev]"
	$(PY) -m tessera.seed.generate --install
	cd ui && npm install

seed:
	$(PY) -m tessera.seed.generate --install

test:
	LLM_MODE=mock $(PY) -m pytest

lint:
	$(PY) -m ruff check tessera tests
	$(PY) -m ruff format --check tessera tests

typecheck:
	$(PY) -m mypy tessera

check: lint typecheck test

api:
	$(PY) -m uvicorn tessera.api.app:app --port 8000

ui:
	cd ui && npm run dev -- --port 5173

demo:
	$(PY) -m tessera.seed.generate --install
	( $(PY) -m uvicorn tessera.api.app:app --port 8000 & cd ui && npm run dev -- --port 5173 ; kill %1 )

reset:
	$(PY) -m tessera.cli reset

drift:
	$(PY) -m tessera.cli drift

fixtures:
	LLM_MODE=record $(PY) -m tessera.cli record-fixtures
