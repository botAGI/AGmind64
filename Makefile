.PHONY: help audit smoke lint format test test-fast schema-export schema-validate \
        pre-commit-install pre-commit-run docker-base docker-cpu docker-vulkan docker-rocm \
        clean

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

# --- Quality gates ---

audit:
	$(PYTHON) scripts/checks/audit_forbidden.py --fail

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

# --- Schema artifacts ---

schema-export:
	$(PYTHON) scripts/dev/export_schemas.py

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

# --- Cleanup ---

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
