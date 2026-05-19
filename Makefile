.PHONY: help audit smoke lint format test test-fast schema-export schema-validate \
        pre-commit-install pre-commit-run docker-base docker-cpu docker-vulkan docker-rocm \
        dod-A dod-B dod-C dod-D dod-E dod-F dod-G dod-H-prime clean

# Auto-detect venv: prefer .venv/bin/* если установлены, fallback на system PATH.
# Это даёт make targets работать как в dev (.venv) так и в CI (system pip install).
PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)
PYTEST := $(shell test -x .venv/bin/pytest && echo .venv/bin/pytest || echo pytest)
RUFF   := $(shell test -x .venv/bin/ruff && echo .venv/bin/ruff || echo ruff)
MYPY   := $(shell test -x .venv/bin/mypy && echo .venv/bin/mypy || echo mypy)

help:
	@echo "AGmind dev targets:"
	@echo "  audit                — run audit_forbidden.py (fail on findings)"
	@echo "  lint                 — ruff check + mypy strict"
	@echo "  format               — ruff format + auto-fix"
	@echo "  test                 — pytest полный + coverage"
	@echo "  test-fast            — $(PYTEST) -m 'not slow' (быстрые only)"
	@echo "  schema-export        — regenerate templates/schemas/service.json"
	@echo "  schema-validate      — check-jsonschema all templates/services/*.yaml"
	@echo "  pre-commit-install   — install git hooks локально"
	@echo "  pre-commit-run       — run все hooks against all files"
	@echo "  smoke                — load default backend and print device info"
	@echo "  docker-base          — build base x86-64 image"
	@echo "  docker-{cpu,vulkan,rocm}  — build backend images"
	@echo "  dod-X                — run Definition of Done check for phase X (A-G, H-prime)"

# --- Quality gates ---

audit:
	$(PYTHON) scripts/audit_forbidden.py --fail

lint:
	$(RUFF) check .
	$(MYPY) agmind/

format:
	$(RUFF) format .
	$(RUFF) check --fix .

test:
	$(PYTEST) -q --cov=agmind --cov-branch

test-fast:
	$(PYTEST) -q -m "not slow"

# --- Phase H'.A + L.A artifacts ---

schema-export:
	$(PYTHON) -m scripts.export_schemas

schema-validate:
	@if [ -x .venv/bin/check-jsonschema ]; then \
		.venv/bin/check-jsonschema --schemafile templates/schemas/service.json templates/services/*.yaml; \
	elif command -v uvx >/dev/null; then \
		uvx check-jsonschema --schemafile templates/schemas/service.json templates/services/*.yaml; \
	else \
		echo "check-jsonschema not in .venv and uvx unavailable — pip install check-jsonschema"; \
		exit 1; \
	fi

pre-commit-install:
	pre-commit install --install-hooks
	pre-commit install --hook-type commit-msg

pre-commit-run:
	pre-commit run --all-files

smoke:
	python3 -c "from agmind.compute import get_backend; print(get_backend().device_info())"

# --- Docker ---

docker-base:
	docker build -f docker/Dockerfile.base -t agmind-base:dev .

docker-cpu: docker-base
	docker build -f docker/Dockerfile.cpu --build-arg BASE_IMAGE=agmind-base:dev -t agmind-cpu:dev .

docker-vulkan: docker-base
	docker build -f docker/Dockerfile.vulkan --build-arg BASE_IMAGE=agmind-base:dev -t agmind-vulkan:dev .

docker-rocm:
	docker build -f docker/Dockerfile.rocm -t agmind-rocm:dev .

# --- Phase DoD checks (executable success criteria) ---

dod-A:
	@echo "Phase A DoD:"
	@test -f docs/MIGRATION_PLAN.md || { echo "  ❌ MIGRATION_PLAN.md missing"; exit 1; }
	@test -f .planning/research/x86-migration/baseline-audit.json || { echo "  ❌ baseline-audit.json missing"; exit 1; }
	@test -f scripts/audit_forbidden.py || { echo "  ❌ audit_forbidden.py missing"; exit 1; }
	@echo "  ✅ all artefacts present (human approval still required)"

dod-B: audit
	@echo "Phase B DoD: audit clean outside legacy/ — done."

dod-C: dod-B lint
	$(PYTEST) -m backend_cpu -q
	$(PYTEST) -m backend_any -q
	$(MAKE) smoke

dod-D: dod-C
	$(PYTEST) -m "backend_vulkan or backend_rocm" -q
	@test -f docs/BENCHMARKS.md || { echo "  ❌ BENCHMARKS.md baseline missing"; exit 1; }

dod-E: dod-D
	pytest -q

dod-F: dod-E docker-base docker-cpu docker-vulkan docker-rocm
	@echo "Phase F DoD: all 4 docker images built locally."

dod-G: dod-F
	@test -s docs/BENCHMARKS.md || { echo "  ❌ BENCHMARKS.md empty"; exit 1; }
	@grep -q "tg.*pp" docs/BENCHMARKS.md || { echo "  ❌ no tg/pp numbers in BENCHMARKS"; exit 1; }
	@echo "Phase G DoD: benchmarks + docs final."

dod-H-prime: audit schema-validate
	@echo "Phase H' DoD:"
	@test -d templates/services && [ "$$(ls templates/services/*.yaml 2>/dev/null | wc -l)" -ge 30 ] || \
		{ echo "  ❌ templates/services/*.yaml < 30 файлов"; exit 1; }
	@test -f templates/schemas/service.json || { echo "  ❌ JSON Schema missing"; exit 1; }
	@test -f agmind/services/renderer.py || { echo "  ❌ renderer.py missing"; exit 1; }
	@test -f agmind/cli/service_cmd.py || { echo "  ❌ service_cmd.py missing"; exit 1; }
	@test -f scripts/amdgpu_textfile.sh && [ -x scripts/amdgpu_textfile.sh ] || \
		{ echo "  ❌ amdgpu_textfile.sh missing/not exec"; exit 1; }
	@test -f docs/adr/0005-service-descriptor-schema.md || { echo "  ❌ ADR-0005 missing"; exit 1; }
	@test -f docs/adr/0006-traefik-routing-and-python-renderer.md || { echo "  ❌ ADR-0006 missing"; exit 1; }
	@test -f docs/adr/0007-observability-stack.md || { echo "  ❌ ADR-0007 missing"; exit 1; }
	@test -f docs/adr/0008-plugin-system-and-legacy-cleanup.md || { echo "  ❌ ADR-0008 missing"; exit 1; }
	$(PYTEST) -q -m "not slow" -k "test_service_schema or test_services_descriptors or test_renderer or test_observability"
	@echo "Phase H' DoD: всё на месте ✓"

# --- Cleanup ---

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
