PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
ifeq ($(wildcard $(VENV_PYTHON)),$(VENV_PYTHON))
PYTHON := $(VENV_PYTHON)
endif

.PHONY: bootstrap demo api worker test lint regression stability-queue stability-api obs-up obs-down

bootstrap:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && pip install -r requirements-dev.txt

demo:
	$(PYTHON) src/main.py

api:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	$(PYTHON) scripts/run_worker.py

test:
	$(PYTHON) -m unittest discover -s tests

lint:
	ruff check app tests src

regression:
	$(PYTHON) scripts/run_regression.py --mode mock --fail-on-errors

stability-queue:
	$(PYTHON) scripts/run_stability_validation.py queue

stability-api:
	$(PYTHON) scripts/run_stability_validation.py api --mode inprocess

obs-up:
	docker compose -f deployments/observability/docker-compose.yml up --build -d

obs-down:
	docker compose -f deployments/observability/docker-compose.yml down
