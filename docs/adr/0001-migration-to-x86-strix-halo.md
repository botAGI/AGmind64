# ADR-0001: Миграция AGmind с aarch64/GB10 на x86_64/AMD Strix Halo

- **Status:** accepted (migration shipped 2026-05-19, alpha v0.1.0-dev)
- **Date:** 2026-05-19
- **Accepted:** 2026-05-19 (после Phase A-G ship)
- **Authors:** AGmind core team
- **Supersedes:** старый ADR-0001 в `legacy/gb10/docs/adr/0001-arm64-only.md`
  («arm64-only; install.sh exits 1 on x86_64»)
- **Related:** ADR-0002 (compute backend abstraction), `AGMIND_MIGRATION_SPEC.md`,
  `docs/MIGRATION_PLAN.md`

## Контекст

AGmind v3.2.0 — Bash + Docker Compose installer для DGX Spark (NVIDIA GB10,
Grace Blackwell, aarch64, unified 128 GB LPDDR5X). x86_64 поддержка была
**удалена** 2026-04-25 (старый ADR-0001) по причине дрейфа maintenance:
поддерживать две архитектуры с разными arm64-only NGC образами (vLLM
gemma4-cu130, Docling cu130, RAGFlow spark) стало невозможно при размере
команды.

В 2026-05 пользователь сменил направление: основная платформа теперь —
**AMD Strix Halo** (Ryzen AI Max+ 395, Radeon 8060S, gfx1151, RDNA 3.5,
128 GB unified LPDDR5X, x86_64). Это:
- **архитектурно похоже** на GB10 (оба APU с unified memory),
- но **совершенно другой compute layer**: ROCm/Vulkan вместо CUDA,
- и **другой ISA**: x86_64 вместо aarch64.

Сохранять старый Bash installer с условными ветками `if cuda; else rocm;
fi` невозможно — половина кода (peer.sh QSFP NAT, driver 580 pin,
FlashInfer TRITON_ATTN, sm_121 specifics) бессмысленна на Strix Halo. Из
30k LOC Bash порядка 60% — Spark/GB10-specific и не воспроизводится.

## Рассмотренные варианты

### A. Multi-arch comeback в текущем Bash-дереве

Восстановить условную логику: `if arch=arm64 → GB10 path, if arch=amd64
→ x86/Strix path`. Два набора `versions.env.{arm64,amd64}`, два compose
overlay, две CI lanes.

- **Плюсы:** один репо, общие фичи.
- **Минусы:** дорого в поддержке (двойной CI, двойное тестирование,
  ADR-холды × 2). Только что отказались от этого пути (старый ADR-0001
  2026-04-25). Strix Halo требует ROCm/Vulkan — никакого общего кода с
  CUDA веткой не возникает.
- **Цена:** ~30% поддержки на одну архитектуру удваивает usable
  capacity.

### B. Hard fork: x86-only Bash installer

Берём текущий код, выпиливаем всё aarch64/GB10, оставляем Bash. ROCm
заменяет CUDA в image:tag, остальное as is.

- **Плюсы:** минимум изменений в архитектуре сборки.
- **Минусы:** Bash + Docker Compose плохо ложится на inference workload.
  Compute abstraction (Vulkan/ROCm/CPU runtime-выбор) в Bash — мучение.
  Тестировать через shellcheck + bash unit-тесты сложнее чем pytest.
  Karpathy «simplicity first» нарушается.

### C. Pol Полный rewrite в Python (полный rewrite)

Текущая Bash-кодовая база → `legacy/gb10/`. Новый `agmind/` Python-пакет
с runtime-абстракцией compute backends (Vulkan / ROCm / CPU / NPU-stub),
CLI на typer, docker/ для multi-backend сборок, audit-скрипт как CI
gate.

- **Плюсы:**
  - Compute abstraction естественно ложится на Python (ABC, lazy
    imports, contract tests параметризованные по backend).
  - Снижение LOC ~5× (Karpathy «simplicity first»).
  - Pytest + ruff + mypy — стандартный 2026 Python tooling.
  - llama-cpp-python + onnxruntime + torch — все нативно Python.
  - Дисциплина через executable DoD per phase (R6 из R-karpathy).
- **Минусы:**
  - 2-3 месяца человеко-работы на перенос.
  - Теряем 6 месяцев Bash hardening (state store, registry codegen,
    phase engine — придётся переписать).
  - Старая мускульная память команды на Bash-стек.
- **Цена:** полная переработка, но с осмысленной структурой и
  тулингом.

### D. Полный рестарт «с нуля» без legacy/

Удалить старый код насовсем, написать AGmind заново под Strix Halo.

- **Плюсы:** ноль legacy debt.
- **Минусы:** нет safety net для отката. Пользователь явно зафиксировал
  «legacy/gb10/ — gold standard для rollback до 2027-Q1» (Part 1.5
  спеки).

### E. Ничего не делать

Оставить AGmind как есть, x86_64 + Strix Halo не поддерживается.

- **Минусы:** Пользователь сменил hardware target. Не вариант.

## Решение

**Выбран вариант C: полный rewrite в Python с legacy quarantine.**

Обоснование:
- Соответствует пользовательскому решению 2026-05-18: «Polный rewrite в
  Python (спека буквально)».
- `AGMIND_MIGRATION_SPEC.md` Part 1.4 описывает именно этот целевой
  layout (`agmind/compute/`, `legacy/gb10/`, `docker/Dockerfile.{base,cpu,vulkan,rocm}`,
  `docs/adr/`, `scripts/audit_forbidden.py`).
- Compute abstraction (Vulkan/ROCm/CPU/NPU-stub) — natural fit для
  Python ABC + lazy imports.
- Karpathy-style simplicity (minimum code, surgical changes, executable
  DoD) — естественнее в pytest-driven Python чем в bash + shellcheck.
- Safety net через `legacy/gb10/` (до 2027-Q1) обеспечивает rollback.

## Последствия

### Положительные

- Унифицированный compute interface — один Python API для пользователя,
  4 бэкенда под капотом.
- Снижение LOC ~5× (~30k Bash → ~5k Python + tests).
- Стандартный Python tooling (ruff, mypy, pytest, pre-commit).
- CI matrix на amd64 + 3 backends (cpu/vulkan/rocm) с self-hosted
  runner на Strix Halo (Part 5.4 спеки).
- Audit-скрипт как hard gate против регресса CUDA/aarch64.
- Понятный rollback: `cd legacy/gb10 && bash install.sh`.

### Отрицательные / технический долг

- 2-3 месяца календарного времени на полный перенос (или 3-6 недель в
  agentic-engineering loop с reviewer).
- В первые M1-M2 итерации часть production-фич старого AGmind не
  доступна (RAGFlow, Dify deep features, dual-Spark cluster) —
  компенсируется решением OQ-1/2/3.
- Bash экспертиза команды частично перестаёт быть load-bearing.

### Что нужно сделать

- [ ] Получить апрув плана `docs/MIGRATION_PLAN.md`.
- [ ] Фаза B: PR-B1..B7 переезд в `legacy/gb10/`.
- [ ] Фаза C: `agmind/compute/` skeleton + CPU backend + contract tests.
- [ ] Фаза D: Vulkan + ROCm backends + бенчи baseline.
- [ ] Фаза E: остальной hot path (CLI, diagnostics, secrets, config, ...).
- [ ] Фаза F: 4 Docker-образа + CI + self-hosted runner.
- [ ] Фаза G: бенчмарки + README + закрытие ADR.

## Бенчмарки (если применимо)

Baseline-числа будут зафиксированы в `docs/BENCHMARKS.md` после фазы D
(см. план §4 фаза D DoD). Сравнение с GB10 — если есть сохранённые
числа в `legacy/gb10/benchmarks/`.

## Откат

При фундаментальном провале миграции:
1. `git revert <последний PR>` — backout по PR-у.
2. `cd legacy/gb10 && bash install.sh` — старый installer работает как
   раньше (до 2027-Q1, deprecation policy в `legacy/gb10/README.md`).
3. Создать ADR-XXXX «revert ADR-0001» с обоснованием.

## Ссылки

- `AGMIND_MIGRATION_SPEC.md`
- `docs/MIGRATION_PLAN.md`
- Local migration research notes (kept outside Git after repository cleanup)
- `legacy/gb10/docs/adr/0001-arm64-only.md` (старое решение, superseded)
