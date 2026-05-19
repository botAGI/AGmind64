# ADR-0008: Plugin System (setuptools entry_points) + Legacy Cleanup

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0002 (compute backend abstraction), ADR-0005 (Service Descriptor), Phase H'.E, deep-dive 04 §8

## Контекст

К концу Phase H'.D у нас:
- 4 встроенных backends (`cpu`, `vulkan`, `rocm`, `npu`) hardcoded в `agmind/compute/_registry.py::_load_backends()` через `try/except ImportError`.
- 32 service descriptors живут в `templates/services/*.yaml`, рендерятся в compose через Python renderer (ADR-0006).
- Legacy файлы остались: `templates/services.yaml` (monolithic, заменён split), `ansible/roles/services/templates/docker-compose.yml.j2` (заменён `agmind render compose`), `ansible/roles/services/templates/nginx-default.conf.j2` (Traefik default per ADR-0006).
- `agmind/services/registry.py::load_registry()` всё ещё парсит legacy single yaml.

Проблемы для решения в H'.E:

1. **Добавление нового backend трогает 7-8 мест** (см. deep-dive 04 §8, audit). Это противоречит цели "безболезненное добавление фич".
2. **Legacy файлы дублируют новую truth** — confusion при поиске "где правильное определение сервиса".
3. **Нет `agmind service <verb>` CLI** — отсутствие developer-friendly способа создать новый descriptor.

## Рассмотренные варианты

### A: оставить hardcoded backends + ручной copy-paste для cleanup
- ➖ Не решает problem #1
- ➖ Legacy files остаются гнить

### B: setuptools entry_points + ServiceDescriptor-based registry + agmind service CLI (выбран)
- ➕ Backends discoverable через PEP-621 standard `entry_points` group `agmind.backends` (deep-dive 04 §8, паттерн pytest/MLflow/SQLAlchemy)
- ➕ Третьи стороны: `pip install agmind-intel-backend` → backend появляется в registry **без правки core**
- ➕ `load_registry()` читает `templates/services/*.yaml` (single source of truth)
- ➕ `agmind service scaffold/validate/status/list` — developer UX
- ➕ Legacy files удаляются окончательно (один source of truth)
- ➖ Editable install `pip install -e .` нужен для entry_points discovery (uv это делает быстро)
- ➖ Safety net: если entry_points не discovered (PYTHONPATH only mode) — fallback на explicit import CPU backend (Invariant I.1 — CPU must be available)

### C: pluggy framework (pytest-style hooks)
- ➕ Более мощный (hook spec'ы, ordering через tryfirst/trylast/wrapper)
- ➖ Overkill для одного метода `compute()` на backend (см. deep-dive 04 §8)
- ➖ Дополнительная зависимость

## Решение

Вариант **B**.

### Изменения в коде

1. **`pyproject.toml`**: добавлен `[project.entry-points."agmind.backends"]`:
   ```toml
   cpu = "agmind.compute.backends.cpu:CPUBackend"
   vulkan = "agmind.compute.backends.vulkan:VulkanBackend"
   rocm = "agmind.compute.backends.rocm:ROCmBackend"
   npu = "agmind.compute.backends.npu_stub:NPUStubBackend"
   ```

2. **`agmind/compute/_registry.py::_load_backends()`** переписан:
   ```python
   def _load_backends() -> dict[str, type[Backend]]:
       out = {}
       for ep in entry_points(group="agmind.backends"):
           try:
               out[ep.name] = ep.load()
           except Exception as exc:
               log.warning("backend %s load failed: %s", ep.name, exc)
       if "cpu" not in out:  # safety net для PYTHONPATH-only mode
           from agmind.compute.backends.cpu import CPUBackend
           out["cpu"] = CPUBackend
       return out
   ```

3. **`agmind/services/registry.py::load_registry()`** переписан:
   - Если `path` указан и это file → legacy single-yaml parsing (backward compat для существующих тестов с fixture).
   - Если `path` указан и это dir → читать `<dir>/*.yaml` через ServiceDescriptor.
   - Если `path` None → `templates/services/*.yaml` через ServiceDescriptor → `to_legacy_service()`.
   - Если path не существует → empty dict с warning (legacy contract).

4. **`agmind/cli/service_cmd.py`** + subapp `agmind service`:
   - `agmind service list` — таблица всех descriptors (name/tier/profiles/image)
   - `agmind service status [name]` — aggregate breakdown или детали
   - `agmind service validate [name]` — JSON Schema check всех или одного
   - `agmind service scaffold <name> --tier <T>` — новый descriptor из template

5. **`discover_backend_names()`** — публичная функция для `agmind status` чтобы показать loaded plugins.

### Cleanup

Удалены:
- `templates/services.yaml` (574 LOC monolith, заменён 32 split-файлами Phase H'.B)
- `ansible/roles/services/templates/docker-compose.yml.j2` (60 LOC Jinja2, заменён `agmind render compose`)
- `ansible/roles/services/templates/nginx-default.conf.j2` (Traefik — default reverse proxy per ADR-0006)

Остался для backward compat (для тестов fixture):
- `_load_legacy_single_yaml()` helper в registry.py — парсит test yaml fixtures, не используется в production пути

## Последствия

### Положительные

- **Новый backend = 1 файл + 1 строка**: `pip install -e .` после добавления entry_point — backend появляется.
- **Single source of truth** для service catalog: 32 файла в `templates/services/`.
- **Developer UX**: `agmind service scaffold qdrant --tier storage` за 1 секунду → готовый файл с правильным schema header.
- **Третьи стороны могут публиковать свои backends** через PyPI без форка AGmind.
- 580+ regression tests гарантируют backward compat.

### Отрицательные / технический долг

- **Editable install vs PYTHONPATH**: entry_points нужен правильно установленный пакет (`pip install -e .` или wheel). Если кто-то делает `PYTHONPATH=. python -m agmind` — entry_points будут пустыми, fallback на explicit CPU.
- **Dockerfile.base/rocm digests** ещё содержат `REPLACE_WITH_DIGEST` — это runtime task для Phase H (hardware bench), задокументировано как `DEF-DOCKERFILE-DIGESTS` в migration_progress.json. На dev машине без Docker не получится — нужен `docker buildx imagetools inspect` в живом окружении.
- `templates/services.yaml` удалён → пользователи на старой версии при `git pull` могут увидеть delete файла. Migration script `scripts/migrate_services_to_descriptors.py` сейчас вернёт empty registry (legacy.yaml не существует) → но `templates/services/*.yaml` уже сгенерированы и в git, повторный запуск миграции не нужен.

### Что нужно сделать

- [x] H'.E.1: entry_points в pyproject.toml + переписать `_load_backends()`
- [x] H'.E.2: `services/registry.py` читает из `templates/services/*.yaml`
- [x] H'.E.3: `agmind service` CLI (list/status/validate/scaffold)
- [x] H'.E.4: удалить 3 legacy файла
- [x] H'.E.5: ADR-0008
- [ ] DEF-DOCKERFILE-DIGESTS: заполнить REPLACE_WITH_DIGEST в Dockerfile.base/rocm (Phase H hardware bench)

## Бенчмарки

| Действие | До Phase H'.E | После |
|---|---|---|
| Добавить новый backend | 7-8 мест (deep-dive 04 §8) | 1 файл + 1 строка в pyproject |
| Создать service descriptor | manual mkdir + write | `agmind service scaffold X -t T` |
| Validate config | manual `yaml.safe_load` | `agmind service validate` |
| Source of truth | `services.yaml` + `services/*.yaml` (дубль) | `services/*.yaml` only |
| Legacy file count | 3 obsolete | 0 |

## Откат

Если entry_points discovery дал проблемы:
1. Safety net в `_load_backends()` импортит CPUBackend напрямую — CPU всегда работает.
2. Можно вернуть hardcoded loading через revert pyproject.toml entry_points block — но это **не нужно**, потому что fallback safety net уже покрывает edge case.
3. Удалённые legacy файлы есть в git history — `git checkout HEAD~N -- templates/services.yaml` если потребуется.
