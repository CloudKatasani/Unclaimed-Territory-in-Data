PY ?= .venv/bin/python
UV ?= uv

.PHONY: setup seed test lint typecheck demo api ui ui-check reset drift fixtures check

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

ui-check:
	cd ui && npm run typecheck && npm run build

check: lint typecheck test

api:
	$(PY) -m uvicorn tessera.api.app:app --port 8000

ui:
	cd ui && npm run dev -- --port 5173

demo:
	test -f data/tessera.duckdb || $(PY) -m tessera.cli reset
	$(PY) -m uvicorn tessera.api.app:app --port 8000 & API_PID=$$!; \
	  trap "kill $$API_PID" EXIT INT TERM; cd ui && npm run dev -- --port 5173

reset:
	$(PY) -m tessera.cli reset

drift:
	$(PY) -m tessera.cli drift

fixtures:
	$(PY) -m tessera.cli record-fixtures
