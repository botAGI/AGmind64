# ADR-0005: Service Descriptor Schema (Pydantic v2)

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0002 (compute backend abstraction), Phase H' foundation refactor, deep-dive 04-service-onboarding.md, deep-dive 06-steal-fest.md

## Контекст

Phase G закрыта: 32 сервиса описаны в одном файле `templates/services.yaml`, рендерятся в `docker-compose.yml` через Jinja2 шаблон в Ansible. Runtime API — frozen dataclass `agmind.services.registry.Service`.

Текущие проблемы (выявлены deep-dive 04 и аудитом):
1. **Один монолитный YAML 500+ строк** — git конфликты, тяжёлая навигация, нет per-service ownership.
2. **Нет schema validation** — опечатка в `por: 8080` ловится только на runtime.
3. **Нет JSON Schema** — VSCode не подсказывает поля, не подсвечивает ошибки.
4. **Нет полей для auto-discovery** — Traefik labels, Prometheus scrape, Loki labels пишутся в Jinja2 руками.
5. **Legacy dataclass без валидации** — поля `mem_limit: str` принимают любую строку; форматы `4g`/`16gb`/`4G` неконсистентны.
6. **Невозможно расширить без правки core** — добавление `tier`, `owner`, `traefik_host` требует трогать `Service` dataclass + `_build_service()` + рендер.

User priority (см. memory feedback-tui-devops): "fast добавление нового сервиса, безболезненная интеграция, минимум ручных правок".

## Рассмотренные варианты

### A: оставить dataclass + ручные labels в Jinja2 (статус-кво)
- ➕ Ноль изменений
- ➖ Все 5 проблем сохраняются
- ➖ Не соответствует Phase H' DoD

### B: Pydantic v2 ServiceDescriptor + JSON Schema export
- ➕ Type safety, validation на parse time (mem_limit pattern `^\d+[kmg]$`, port format, image no `:latest`)
- ➕ `model_json_schema()` → `templates/schemas/service.json` → VSCode autocomplete via `# yaml-language-server: $schema=...`
- ➕ Pre-commit `check-jsonschema` → fail-fast
- ➕ Расширение нативное — добавление поля = добавление атрибута модели
- ➕ Auto-discovery section: `routing`, `observability`, `tier` встроены в schema
- ➕ Backward compat через `to_legacy_service()` — старый `agmind.services.registry.Service` остаётся, рендерер не ломается
- ➖ Pydantic v2 уже в deps (>=2.7), нулевой cost зависимостей
- ➖ Migration старого `services.yaml` в новый формат — отдельный шаг (Phase H'.B)

### C: CUE / Pkl / Jsonnet
- ➕ Stronger type system, schema unification
- ➖ Learning curve для 1 dev (см. deep-dive 06 §4 anti-patterns)
- ➖ Дополнительная toolchain в CI и pre-commit
- ➖ Решено в deep-dive 04: "Pydantic schema + Jinja2 + `docker compose config` для финальной валидации — 2026 best practice для compose-based стека"

### D: только JSON Schema без Pydantic (hand-written)
- ➕ Не привязывает к Python
- ➖ Дублирование: schema живёт отдельно от runtime model
- ➖ Нет typed access из Python без второго слоя
- ➖ Дрейф schema vs реальные поля

## Решение

Выбран вариант **B**: Pydantic v2 `ServiceDescriptor` в `agmind/schemas/service.py` + JSON Schema export в `templates/schemas/service.json`.

Ключевые design decisions:

1. **Backward compatibility**: ServiceDescriptor предоставляет `to_legacy_service() -> agmind.services.registry.Service` — старый рендерер работает без правок до Phase H'.C.
2. **Frozen models**: `model_config = ConfigDict(frozen=True, extra="forbid")` — невозможно случайно мутировать после load или добавить unknown поле.
3. **Sub-models**: `HealthCheck`, `RoutingConfig`, `ObservabilityConfig`, `ResourceLimits` — модульные, переиспользуемые.
4. **Auto-discovery поля по умолчанию sensible**: `observability.loki_scrape=true` (всё пишем в Loki), `observability.prometheus_scrape=false` (whitelist), `routing.middleware_chain=chain-internal`.
5. **Validation patterns**:
   - `name`: `^[a-z][a-z0-9-]{1,30}$` (docker container name conventions)
   - `image`: запрет `:latest` (Invariant I.2 из spec)
   - `mem_limit`: `^\d+(k|m|g)$` lowercase
   - `port`: `^(\d{1,3}(\.\d{1,3}){3}:)?\d+:\d+$`
   - `tier`: Literal `edge | inference | storage | ops`
6. **JSON Schema export**: `scripts/export_schemas.py` запускается вручную и в pre-commit при изменении `agmind/schemas/`. Artifact `templates/schemas/service.json` коммитится в git (developer convenience).

## Последствия

### Положительные
- VSCode autocomplete + inline validation для каждого `templates/services/*.yaml` (после Phase H'.B split)
- Pre-commit catch'ит опечатки до commit
- Новое поле = 3 строки в Pydantic + регенерация JSON Schema = доступно везде
- Тесты на schema (parse валидных/невалидных примеров) — детектируют breaking changes
- Plug-and-play observability: ServiceDescriptor → docker labels → Prometheus/Loki/Traefik auto-discovery

### Отрицательные / технический долг
- Дублирование информации: ServiceDescriptor (новое) + Service dataclass (legacy). Снимется в Phase H'.C когда рендерер переписан на ServiceDescriptor напрямую.
- Pydantic v2 strict mode потребует переписать существующие `health` dict[str, Any] в типизированную модель — done в H'.A.

### Что нужно сделать
- [x] H'.A: schema + tests + JSON Schema export (это ADR)
- [ ] H'.B: split `services.yaml` → 32 файла `templates/services/*.yaml`
- [ ] H'.B: pre-commit hook `check-jsonschema templates/services/*.yaml`
- [ ] H'.C: Jinja2 renderer читает ServiceDescriptor вместо legacy Service
- [ ] H'.C: `routing` + `observability` секции → auto labels в compose
- [ ] H'.D: Prometheus `docker_sd_configs` + Alloy `discovery.docker` подхватывают labels

## Бенчмарки (если применимо)
N/A — schema validation overhead < 1 ms на сервис, незаметно.

## Откат

Если ServiceDescriptor показал себя плохо:
1. `agmind/schemas/` остаётся как dead code (импортируется только в новом коде)
2. `templates/schemas/service.json` удаляется из pre-commit hooks
3. Старый `agmind.services.registry` продолжает работать без изменений (backward compat сохранена)
4. Никаких миграций отката не требуется
