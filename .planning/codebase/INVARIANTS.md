# AGmind Invariants — что нельзя нарушать

Каждый invariant: rule + rationale + enforcement.

## Compute / hardware

### I.1 Vulkan RADV mandatory, AMDVLK forbidden
- **Rule:** Only RADV driver acceptable для Vulkan backend
- **Rationale:** AMDVLK officially discontinued AMD 2025-09-15. Hard 2 GiB
  cap на VkDeviceMemory ломает LLM ≥30B dense.
- **Enforce:** `agmind/compute/backends/vulkan.py::VulkanBackend.make()` →
  `_assert_no_amdvlk()` raises RuntimeError if AMDVLK ICD files present.
  Doctor `amdvlk-absent` check warns.
- **Override:** none

### I.2 Никаких `:latest` image tags
- **Rule:** Все Docker images в `templates/services.yaml` должны иметь
  pinned semver.
- **Rationale:** Reproducibility, rollback safety, supply chain security.
- **Enforce:** `agmind.services.registry::validate_no_latest()`,
  CI audit + pre-commit hook (через `make audit`),
  `tests/services/test_registry.py::test_validate_no_latest_passes_clean_registry`.

### I.3 Compute backends НЕ хардкодят calls — все через ABC
- **Rule:** Вызовы конкретного backend (llama_cpp напрямую, torch.something)
  допустимы **только** в `agmind/compute/backends/_engines/*.py`.
- **Rationale:** Allows runtime backend selection без recompile.
- **Enforce:** Code review + import graph (`DEPENDENCIES.md`) shows engines
  isolated. Audit script подсмотрит torch.cuda paths.

### I.4 `_engines/` модули lazy-imported
- **Rule:** `from agmind.compute.backends._engines.X import Y` — внутри
  методов, не на module level.
- **Rationale:** Можно `import agmind` без llama_cpp/torch installed.
- **Enforce:** Code review; tests check import без heavy deps.

### I.5 НЕ хардкодить `aarch64`, `cuda`, `nvcr.io`, `NVIDIA` в main tree
- **Rule:** `legacy/` пусть существует если нужно, но в `agmind/`/`tests/`/etc — никаких legacy patterns.
- **Enforce:** `scripts/audit_forbidden.py` (7 rules + pre-commit + CI).

## Service catalog

### I.6 services.yaml как single source of truth
- **Rule:** Service definitions только в `templates/services.yaml`.
  Никакого дублирования в Ansible roles, Python modules, Dockerfile.
- **Rationale:** Schema drift = compose ≠ Python registry inconsistency.
- **Enforce:** Ansible reads через `lookup('file') | from_yaml`. Python
  reads через `agmind.services.registry.load_registry()`. Tests validate
  schema.

### I.7 services.yaml schema_version bump на breaking changes
- **Rule:** Если добавляешь required field — `schema_version: 2`.
- **Rationale:** Python reader checks schema_version.
- **Enforce:** Manual (нет автомиграции пока). Будет ADR-0003+.

## Models catalog

### I.8 GGUF tier matrix как single source of truth
- **Rule:** Tier mapping только в `templates/models.yaml`.
- **Enforce:** `agmind.models.load_models_registry()`,
  `agmind.cli.models_cmd.cmd_list()` reads from там же.

### I.9 LLM model URL должен быть HuggingFace `/resolve/main/`
- **Rule:** Все `hf_repo + filename` строит standard HF URL.
- **Rationale:** Predictable download path.
- **Enforce:** `agmind.models.ModelSpec.hf_url` property.

## CLI

### I.10 typer — soft dependency
- **Rule:** `agmind/cli/__init__.py` импортирует typer через try/except.
  `app()` exits with hint если не installed.
- **Rationale:** `pip install agmind` без `[dev]` не должен крашить.
- **Enforce:** `tests/test_cli.py::test_app_called_without_typer_exits`.

### I.11 CLI commands lazy-import subcommand modules
- **Rule:** `cli/models_cmd.py` и др не импортируются при `agmind --help`.
- **Rationale:** Fast `--help` (под 100ms).
- **Enforce:** Code review; `_make_app()` использует `from agmind.cli.X import Y`
  внутри handler functions.

## Cluster

### I.12 PeerHealth.is_alive — single source of truth для routing
- **Rule:** `choose_peer()` фильтрует только по `is_alive=True`.
- **Rationale:** Stale routing = failed requests.
- **Enforce:** Все strategies проверяют `is_alive` before selection.

### I.13 Sticky session — deterministic
- **Rule:** Same session_id → same peer (если он alive).
- **Rationale:** KV cache reuse.
- **Enforce:** `hashlib.sha256(session_id) % len(alive)` — deterministic.
  Test `test_sticky_session_deterministic`.

## Configuration

### I.14 AGMIND_* env vars validated в read_config()
- **Rule:** Invalid value → ValueError на `read_config()`, не silent default.
- **Rationale:** Fail-fast on misconfiguration.
- **Enforce:** `agmind.compute.config::_read_str()` asserts allowed set.

### I.15 Secrets через chmod 600 файлы, не env
- **Rule:** Production passwords/keys в `.env` (chmod 600), не в Ansible group_vars (plain).
- **Rationale:** group_vars committable.
- **Enforce:** `agmind.secrets.write_creds()` always chmod 600.
  `ansible/roles/services/templates/env.j2` chmod 600.

### I.16 mask_value() для logging
- **Rule:** Логирование secrets — только через `mask_value()` (keep 4 chars + ****).
- **Enforce:** Code review; doctor скрипт masked где relevant.

## Workflow / development

### I.17 git mv only, no rm legacy
- **Rule:** Legacy code → `git mv` в `legacy/gb10/`, never delete напрямую.
- **Rationale:** Spec Part 1.5 #1 — rollback safety до 2027-Q1.
- **Override:** User explicit "удалить" (но classifier safety net).

### I.18 ADR per нетривиальное решение
- **Rule:** New architectural pattern → `docs/adr/NNNN-X.md`.
- **Enforce:** Code review.

### I.19 Один PR = одна фаза GSD
- **Rule:** Не сваливать D1+D2+D3 в один commit.
- **Rationale:** Atomic review, revert-friendly.
- **Enforce:** Process discipline (после git init).

### I.20 Recon перед нетривиальной фичей
- **Rule:** Before adding new backend / engine / external dep — recon отчёт в
  `.planning/research/x86-migration/R-X-<topic>.md`.
- **Rationale:** Verified vs unverified claims; supply chain due diligence.
- **Enforce:** CLAUDE.md operational rules.

## Audit / security

### I.21 audit_forbidden.py — frozen file
- **Rule:** Не править rule list без новой ADR.
- **Rationale:** Rules = invariant set; loosening = security regression.
- **Enforce:** `EXCLUDED_PATHS` includes itself; CI fails if rules diverge.

### I.22 Tests НЕ редактируют RUN-ts через automated tools
- **Rule:** Test files (`tests/`) — write once, manual review для edits.
- **Rationale:** Anthropic harness recommendation (R0): «prohibit edit of tests».
- **Enforce:** Code review.

## Build / release

### I.23 НЕ `:latest` в Dockerfiles
- **Rule:** Base images pinned с digest.
- **Status:** `Dockerfile.base` имеет `REPLACE_WITH_DIGEST` placeholder —
  заполнить при первом build (Phase H).

### I.24 НЕ `-march=native` в shippable артефактах
- **Rule:** Use `-march=x86-64-v3` baseline для portable Docker images.
- **Rationale:** Strix Halo может загружать images собранные где-то ещё.
- **Enforce:** audit `native_march` rule.

### I.25 Audit clean = mandatory для merge
- **Rule:** `python3 scripts/audit_forbidden.py --fail` exit 0 — мерж-gate.
- **Enforce:** CI workflow `audit` job; pre-commit hook.
