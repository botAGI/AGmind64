---
phase: A
step: A5
date: 2026-05-19
status: completed
---

# A5 — Hot path: что воспроизводим в Python, что выбрасываем

Hot path = функциональность которую новая Python-кодовая база ОБЯЗАНА
обеспечить (либо в M1, либо позже по приоритету). Контекст: спека
Part 1.4 описывает целевую структуру (`agmind/compute/`, `agmind/cli/`,
docker/, docs/adr/), но НЕ описывает деталей CLI/wizard/installer-фаз —
это нужно вытащить из текущего Bash-кода через recon.

## Категория 1. HOT — воспроизвести в Python обязательно

| Bash модуль (legacy/) | Что делает | Python-эквивалент | Приоритет | Фаза |
|----------------------|-----------|--------------------|-----------|------|
| `lib/detect.sh` | Hardware detection (CPU/RAM/GPU/arch/DGX Spark) | `agmind/compute/detect.py` (spec Part 5.3 уже описан) | P0 | C |
| `agmind/compute/*` (new) | Vulkan/ROCm/CPU backends | По спеке Part 1.4 + 5.3 | P0 | C-D |
| `lib/common.sh` | log_info/log_error/_env_get | `agmind/log.py`, `agmind/_env.py` | P0 | C |
| `lib/phases.sh` | Phase engine: ordered installer phases с resume | `agmind/install/phases.py` (если установщик переезжает в Python) | P1 | C/D |
| `lib/state.sh` | /var/lib/agmind/state/ schema versioning + atomic write | `agmind/state.py` | P1 | C |
| `lib/migrations.sh` | Schema migrations + rollback | `agmind/migrations.py` | P1 | C |
| `lib/health.sh` + `lib/doctor.sh` | Preflight + container health + support bundle | `agmind/diagnostics/{doctor,health}.py` | P1 | E |
| `lib/status.sh` | `agmind status` — services table | `agmind/cli/status.py` | P1 | E |
| `lib/creds.sh` | credentials.txt management (chmod 600, masked logs) | `agmind/secrets.py` | P1 | E |
| `lib/i18n.sh` | RU/EN translations | `agmind/i18n/` (gettext OR fluent) | P2 | E |
| `lib/tui.sh` | TUI prompts (yes/no, choice, masked input) | `questionary` или `rich.prompt` | P2 | E |
| `lib/wizard.sh` | Interactive installer wizard | `agmind/cli/wizard.py` | P2 | E |
| `lib/compose.sh` | Docker Compose profile builder | **ЗАВИСИТ** от решения по стеку | P1-? | E |
| `lib/service-map.sh` + `lib/_registry.indexed.sh` | Service registry runtime | **ЗАВИСИТ** от решения по стеку | P1-? | E |
| `lib/config.sh` | .env generation с placeholder substitution | `agmind/config/env.py` | P1 | E |
| `lib/security.sh` | UFW + fail2ban + audit (БЕЗ driver 580 pin) | `agmind/security/` | P2 | E |
| `lib/backup.sh` + `lib/restore.sh` | Backup/restore (если стек сохранён) | **ЗАВИСИТ** от решения по стеку | P2-? | E/F |
| `scripts/audit_forbidden.py` | Audit (уже создан) | Уже в `scripts/`, дополнить `# audit: allow` | P0 | A |
| `scripts/check-upstream.sh` | Version drift check для image:tag (без NVIDIA) | `scripts/check_upstream.py` (Python rewrite) | P3 | F |

## Категория 2. COLD — выбрасываем без замены

| Bash модуль | Почему выбрасываем |
|-------------|---------------------|
| `lib/peer.sh` | Dual-Spark QSFP 200G NAT — Spark-specific. На x86 multi-node нужен другой подход (если вообще). |
| `lib/ssh_trust.sh` | SSH-trust между master/worker Spark — то же что peer.sh. |
| `lib/cluster_mode.sh` | Cluster wizard prompts — Spark-specific. |
| `lib/airgapped.sh` | Air-gapped mode logic — пока не приоритет. P3 backlog. |
| `lib/bundle.sh` | Offline transfer bundle — пока не приоритет. P3 backlog. |
| `lib/authelia.sh` | Authelia 2FA — нужно если стек сохранён, но не P0. |
| `lib/openwebui.sh` | Open WebUI integration — зависит от стека. |
| `lib/models.sh` | Spark-specific model defaults (gemma-4-26B FP16 etc.) | **PERESEN`** на x86: новый tier-based model selection через `agmind/models.py` с учётом detected VRAM (R-karpathy «overfit one batch» principle). |
| `scripts/check-upstream.sh` NVIDIA части | NGC/Spark image bumps — выбросить, оставить только generic version drift |
| Driver 580 pin в `lib/security.sh` | GB10 UMA-специфика, не нужно на x86 |
| FlashInfer FP8 workaround в env | SM_121 specific |
| `tests/unit/test_versions_env_arm64_holds.sh` | arm64 holds — заменить на amd64 holds (или вовсе убрать pinning if not needed) |

## Категория 3. OPEN QUESTIONS — нужен апрув пользователя

Эти решения сильно влияют на scope hot path. Записать в MIGRATION_PLAN.md
для апрува.

**OQ-1. Сохраняется ли Dify+Weaviate+RAGFlow стек?**
- **Если ДА (стек сохраняется):** новый `agmind/` пакет = тонкий
  оркестратор поверх docker compose. Hot path расширяется: `compose.sh`,
  `service-map.sh`, `wizard.sh`, `config.sh` нужны как Python.
- **Если НЕТ (минималистский подход):** `agmind/` = inference + embed +
  rerank через `agmind.compute` + лёгкий REST API. Никакого Dify/RAGFlow.
  Hot path сокращается ~40%.
- **Третий вариант:** opt-in стек через `agmind deploy stack-name`
  команду, ядро minimal.

**OQ-2. Какой entrypoint для нового `agmind`?**
- (a) CLI (typer/click), как сейчас: `agmind install`, `agmind status`, etc.
- (b) REST API (FastAPI) + CLI клиент.
- (c) Pure library: `from agmind.compute import get_backend`.
- (d) Все три: lib + CLI + опциональный API сервер.

**OQ-3. Инсталлятор как Python entrypoint?**
- Если ДА: `pip install agmind && agmind install` запускает фазы (детект,
  pull images, generate compose, start, smoke). Это требует `agmind/install/`.
- Если НЕТ: пользователь сам делает `docker compose up`, agmind = только
  inference + ops CLI.

**OQ-4. Какой UI для wizard?**
- (a) `questionary` (читаемый Python, default).
- (b) `rich.prompt` (если уже зависим от rich для отображения).
- (c) `textual` (full TUI app) — overkill.
- (d) Простой CLI flags only, no interactive (агентам/automate проще).

**OQ-5. Multi-node x86 cluster (новый требование из чата)?**
- Какие технологии: Docker Swarm? K3s? Ansible? Simple SSH-based как раньше?
- Это P2/P3, не критично для M1.

**OQ-6. mDNS/.local discovery на x86?**
- Avahi работает на любом Linux. Сохранять `*.local` URLs или mигрировать
  на обычный DNS / Caddy с automatic HTTPS?

## Маппинг hot path на фазы спеки

```
Phase B (Legacy quarantine) — переезжаем ВСЁ из категории 1+2 в legacy/.
                              Категория 3 — обсуждаем до B finalize.

Phase C (Compute abstraction skeleton) — реализуем:
  - agmind/compute/{base.py, detect.py, config.py, __init__.py}
  - agmind/compute/backends/{cpu.py, npu_stub.py}
  - agmind/log.py, agmind/_env.py
  - agmind/state.py (если решено DO)
  - tests/compute/test_contract.py + test_detect.py

Phase D (Backends) — реализуем:
  - agmind/compute/backends/vulkan.py
  - agmind/compute/backends/rocm.py
  - benchmarks baseline в docs/BENCHMARKS.md

Phase E (Call-sites migration) — реализуем оставшийся hot path:
  - agmind/diagnostics/, agmind/cli/, agmind/secrets/, agmind/config/
  - agmind/i18n/, agmind/security/
  - Если стек сохранён: agmind/deploy/, agmind/services/

Phase F (Docker & CI) — Dockerfile.{base,cpu,vulkan,rocm} (Part 5 спеки).

Phase G (Benchmarks & docs) — финальные числа, ADR закрыты, README.
```

## Конкретные данные для MIGRATION_PLAN.md (выжимка)

- **~32 файла кода** в legacy/ (после фазы B)
- **~10-15 файлов нужно воспроизвести в Python** (зависит от OQ-1/3)
- **~3-5 файлов выбрасываем без замены** (peer/cluster/airgapped/bundle/models)
- **2 entrypoint скрипта (install.sh + agmind.sh)** → 1 Python CLI
- **Старый registry.yaml (50 сервисов)** → решается через OQ-1

## Sanity-check: размер новой кодовой базы

Грубая оценка по аналогии с Karpathy nanoGPT minimal-deps philosophy
(R-karpathy.md):

- `agmind/compute/`: 600-1000 LOC (4 backends × 150-250 LOC + base + detect + config)
- `agmind/cli/`: 400-800 LOC (typer-based CLI, ~10 commands)
- `agmind/diagnostics/`: 400-600 LOC
- `agmind/config/`: 200-400 LOC
- `agmind/secrets/`, `agmind/state/`: 300-500 LOC
- `tests/`: 1000-2000 LOC (contract + unit + integration)
- `docker/`: 4 Dockerfile (по 50 LOC) = 200 LOC
- `scripts/`: audit + 2-3 утилиты = 600 LOC

**Грубый итог нового кода:** 4000-6000 LOC Python + 1000-2000 LOC tests.
**Старый Bash:** ~30,000 LOC (по оценке текущего дерева). Сокращение
~5×. Это согласуется с Karpathy «simplicity first» principle.

## Что должно быть в новом `Makefile`

```makefile
.PHONY: audit smoke lint test docker dod-A dod-B dod-C ...

audit:
	python3 scripts/audit_forbidden.py --fail

lint:
	ruff check .
	ruff format --check .
	mypy agmind/

test:
	pytest -q --cov=agmind

smoke:
	python3 -c "from agmind.compute import get_backend; print(get_backend().device_info())"

docker-base:
	docker build -f docker/Dockerfile.base -t agmind-base:dev .

docker-cpu docker-vulkan docker-rocm: docker-base
	docker build -f docker/Dockerfile.$(subst docker-,,$@) --build-arg BASE_IMAGE=agmind-base:dev -t agmind-$(subst docker-,,$@):dev .

dod-A: audit
	@echo "Phase A DoD: audit baseline + MIGRATION_PLAN.md approved"
	@test -f docs/MIGRATION_PLAN.md
	@test -f .planning/research/x86-migration/baseline-audit.json
	# manual gate: human approval recorded in progress.json::phase_status

dod-B:
	@echo "Phase B DoD: audit returns 0 outside legacy/"
	python3 scripts/audit_forbidden.py --fail

dod-C: dod-B lint
	pytest -m backend_cpu

dod-D: dod-C
	pytest -m "backend_vulkan or backend_rocm"

dod-E: dod-D
	pytest -q

dod-F: dod-E
	docker-cpu docker-vulkan docker-rocm

dod-G: dod-F
	@test -f docs/BENCHMARKS.md
```

Это draft, финальная версия согласуется с пользователем в фазе F.
