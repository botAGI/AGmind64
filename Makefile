.PHONY: help audit smoke lint format test docker-base docker-cpu docker-vulkan docker-rocm \
        dod-A dod-B dod-C dod-D dod-E dod-F dod-G clean

help:
	@echo "AGmind dev targets:"
	@echo "  audit          — run audit_forbidden.py (fail on findings)"
	@echo "  lint           — ruff + mypy"
	@echo "  format         — ruff format"
	@echo "  test           — pytest with coverage"
	@echo "  smoke          — load default backend and print device info"
	@echo "  docker-base    — build base x86-64 image"
	@echo "  docker-{cpu,vulkan,rocm}  — build backend images"
	@echo "  dod-X          — run Definition of Done check for phase X (A-G)"

# --- Quality gates ---

audit:
	python3 scripts/audit_forbidden.py --fail

lint:
	ruff check .
	mypy agmind/

format:
	ruff format .
	ruff check --fix .

test:
	pytest -q --cov=agmind

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
	pytest -m backend_cpu -q
	pytest -m backend_any -q
	$(MAKE) smoke

dod-D: dod-C
	pytest -m "backend_vulkan or backend_rocm" -q
	@test -f docs/BENCHMARKS.md || { echo "  ❌ BENCHMARKS.md baseline missing"; exit 1; }

dod-E: dod-D
	pytest -q

dod-F: dod-E docker-base docker-cpu docker-vulkan docker-rocm
	@echo "Phase F DoD: all 4 docker images built locally."

dod-G: dod-F
	@test -s docs/BENCHMARKS.md || { echo "  ❌ BENCHMARKS.md empty"; exit 1; }
	@grep -q "tg.*pp" docs/BENCHMARKS.md || { echo "  ❌ no tg/pp numbers in BENCHMARKS"; exit 1; }
	@echo "Phase G DoD: benchmarks + docs final."

# --- Cleanup ---

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
