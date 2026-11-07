PYTHON ?= python3
BUN ?= bun
ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
else
VENV_PYTHON := .venv/bin/python
endif

.PHONY: help env install frontend check test test-e2e test-perf test-hardware build acquire web run ventana run-ligero migrate archive docker-build docker-up docker-web docker-down docker-logs docker-test
help:
	@$(PYTHON) scripts/manage.py help
env:
	$(PYTHON) scripts/manage.py env
install: env
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(BUN) install --cwd frontend --frozen-lockfile
frontend:
	$(BUN) run --cwd frontend dev
check:
	$(BUN) run --cwd frontend check
test:
	$(VENV_PYTHON) -m unittest discover -s tests
	$(BUN) run --cwd frontend test
test-e2e: build
	$(BUN) run --cwd frontend test:e2e
test-perf:
	SMP_PERF=1 $(VENV_PYTHON) -m unittest discover -s tests -p test_performance.py
test-hardware:
	SMP_HARDWARE=1 $(VENV_PYTHON) -m unittest discover -s tests -p test_hardware.py
ventana:
	$(VENV_PYTHON) -m smp22x ventana
run-ligero:
	$(VENV_PYTHON) scripts/manage.py run-ligero
migrate:
	$(VENV_PYTHON) -m smp22x migrate
archive:
	$(VENV_PYTHON) -m smp22x archive
build:
	$(BUN) run --cwd frontend build
acquire:
	$(VENV_PYTHON) -m smp22x log --no-plot
web:
	$(VENV_PYTHON) -m smp22x dashboard
run: build
	$(VENV_PYTHON) scripts/manage.py run
docker-build:
	docker compose build
docker-up: env
	docker compose up -d --build
docker-web: env
	docker compose up -d --build web
docker-down:
	docker compose down
docker-logs:
	docker compose logs -f
docker-test:
	docker build --target test -t radiometry:test .
	docker run --rm radiometry:test
