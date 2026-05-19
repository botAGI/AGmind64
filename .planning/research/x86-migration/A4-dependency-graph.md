---
phase: A
step: A4
date: 2026-05-19
status: completed
---

# A4 — Dependency graph между Bash-модулями

## Архитектура текущей кодовой базы

```
install.sh (entrypoint #1, sudo bash install.sh)
├── source lib/common.sh         ← 14 users (log_info/log_error/utils)
├── source lib/airgapped.sh
├── source lib/authelia.sh
├── source lib/backup.sh
├── source lib/bundle.sh         → airgapped.sh, doctor.sh
├── source lib/cluster_mode.sh
├── source lib/compose.sh        ← profile builder
├── source lib/config.sh         ← .env generation
├── source lib/detect.sh         ← HW detection (HOT)
├── source lib/docker.sh         → detect.sh
├── source lib/doctor.sh         → common.sh, detect.sh, health.sh
├── source lib/health.sh         → service-map.sh
├── source lib/i18n.sh           ← RU/EN translations
├── source lib/migrations.sh     ← state schema migrations
├── source lib/models.sh
├── source lib/openwebui.sh
├── source lib/peer.sh           ← dual-Spark NAT/QSFP (DEAD на x86)
├── source lib/phases.sh         ← phase engine (HOT)
├── source lib/security.sh       ← UFW + fail2ban + driver pin
├── source lib/ssh_trust.sh      → tui.sh
├── source lib/state.sh          ← /var/lib/agmind/state
├── source lib/tui.sh            ← TUI wizard chrome
└── source lib/wizard.sh         → cluster_mode.sh, detect.sh, tui.sh

scripts/agmind.sh (entrypoint #2, agmind CLI day-2)
├── source scripts/{14 modules}.sh    ← runtime copies of lib/*.sh
                                       (lib/_copy_runtime_files)
```

## Самые востребованные модули (reverse-dependency)

| File | Used by | Назначение | Эквивалент в новом дереве |
|------|--------:|-----------|----------------------------|
| `lib/common.sh` | 14 | log_info, log_error, _env_get, base utils | `agmind/log.py` + `agmind/_env.py` |
| `lib/detect.sh` | 4 | HW detection (CPU, GPU NVML, RAM, arch, DGX Spark) | `agmind/compute/detect.py` (spec Part 1.4) |
| `lib/service-map.sh` | 2 | Service registry runtime API | `agmind/services/registry.py` |
| `lib/tui.sh` | 2 | TUI prompts | `agmind/cli/prompts.py` (typer/click + questionary) |
| `lib/doctor.sh` | 1 (install.sh) | Preflight + bundle | `agmind/diagnostics/doctor.py` |
| `lib/health.sh` | 1 | Service health checks | `agmind/diagnostics/health.py` |
| `lib/wizard.sh` | 1 | Interactive installer wizard | `agmind/cli/wizard.py` |
| `lib/peer.sh` | 1 | Dual-Spark cluster, QSFP NAT | **УДАЛИТЬ** (Spark-specific) |
| `lib/security.sh` | 1 | UFW + fail2ban + driver 580 pin | если нужно — `agmind/security/` без driver 580 |

## Likely entrypoints (50 files)

**Главные:**
- `install.sh` — главный installer
- `scripts/agmind.sh` — day-2 CLI

**Standalone утилиты (можно вырезать из dependency дерева легко):**
- `scripts/check-upstream.sh` — NVIDIA NGC + GitHub version drift checker
- `scripts/generate-manifest.sh` — release manifest builder
- `scripts/health.sh` — standalone health probe
- `scripts/dr-drill.sh` — DR drill
- `scripts/import-dify-workflow.sh` — Dify workflow import
- `scripts/landmines-sync.sh` — LANDMINES regression sync
- `scripts/patch_dify_features.sh`, `scripts/rotate_secrets.sh`
- `scripts/redis-lock-cleanup.sh`, `scripts/mdns-status.sh`
- `scripts/docling-bench.sh`, `scripts/gpu-metrics.sh`
- `scripts/restore.sh`, `scripts/backup.sh`, `scripts/update.sh`
- `scripts/uninstall.sh`, `scripts/health-gen.sh`

## Зависимости от данных вне `*.sh`

- `lib/_registry.indexed.sh` ← codegen из `templates/services/registry.yaml`
- `lib/detect.sh` ← `/etc/os-release` (runtime)
- `lib/security.sh` ← `${install_dir}/versions.env` (runtime)
- `lib/wizard.sh` ← `versions.env` (runtime)
- `lib/migrations.sh` ← `lib/migrations/*.sh` (dir of migration scripts)
- `scripts/generate-manifest.sh` ← `templates/versions.env`

## Implications для фазы B

1. **Можно переезжать всем директорием** `lib/` + `scripts/` + `install.sh`
   — внутренние пути через `${INSTALLER_DIR}/...` остаются валидными
   после `git mv lib/ legacy/gb10/lib/`, если `INSTALLER_DIR` определён
   через `dirname "${BASH_SOURCE[0]}"`.
2. **`templates/` тоже целиком** в `legacy/gb10/templates/` — `lib/_copy_runtime_files`
   и `lib/wizard.sh` оба ожидают конкретные пути.
3. **`monitoring/`, `.github/`, `tests/`** — независимые директории, переехать блоками.
4. **`pipelines/`, `plugins/`, `dify-workflows/`, `workflows/`** — Dify-content,
   как блок в legacy.
5. **PR-B порядок**: чем меньше зависимостей от других, тем раньше
   можно переехать. Predлагаемый порядок:
   1. `.planning/` (1080 находок, нулевые зависимости от кода)
   2. `documentation/` (103, изолировано)
   3. `pipelines/`, `plugins/`, `dify-workflows/`, `workflows/` (контент)
   4. `monitoring/`, `benchmarks/` (изолированные ресурсы)
   5. `tests/` (зависит от lib/scripts/templates — переезжает либо ВМЕСТЕ либо ПЕРЕД ними)
   6. `templates/` (используется lib/scripts/install.sh)
   7. `lib/` + `scripts/` + `install.sh` + старый `Makefile` (нижний слой)
   8. `docs/` (старые ADR — частично переехать, частично оставить как
      reference в новом `docs/adr/`)
   9. Корневые файлы: `README.md`, `CLAUDE.md`, `CHANGELOG.md`,
      `SECURITY.md`, `SPEC.md`, `RELEASE` — переезд + замена на новые.

## Что НЕ переезжает в legacy

Остаётся в корне как часть нового проекта:
- `AGMIND_MIGRATION_SPEC.md` — спека
- `scripts/audit_forbidden.py` — audit-скрипт (с `# audit: allow` правками)
- `.planning/research/x86-migration/` — рабочие notes (этот файл и
  подобные)
- `.planning/sessions/` — журналы overnight-сессий
- `LICENSE` — Apache 2.0 (наследуется)
- `.gitignore` — переписать под Python-проект (Part 1.4 спеки)
- `.gsd/` — нужно решить с пользователем (legacy GSD workflow vs новый)
