# ADR-0012: Upstream Version Check Workflow

- **Status:** accepted
- **Date:** 2026-05-20
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** Legacy AGmind [issue #63](https://github.com/botAGI/AGmind/issues/63),
  ADR-0005 (ServiceDescriptor), Phase P
- **Driver:** user request — "сделай подобный воркфлоу для чека версий со
  своими пометками как https://github.com/botAGI/AGmind/issues/63".

## Контекст

В AGmindx86 хранится ~30 pinned versions:
- 23 service image pin в `templates/services/*.yaml`
- 3 docker base image в `docker/Dockerfile.*`
- Python deps в `pyproject.toml`

Без автоматического мониторинга:
- Безопасные patch-bumpы откладываются и копятся
- Critical security patches не замечаются
- HOLD-овые причины ("ждём Dify minor bump postgres") размазаны по комментариям

Legacy AGmind решил это weekly GitHub Action который сканирует pins,
запрашивает upstream, рендерит markdown table с **explicit HOLD-причинами**,
создаёт/обновляет issue с label `upstream-update`.

Тот же подход для x86 версии.

## Рассмотренные варианты

### A: Dependabot (out of box)
- ➕ Zero maintenance
- ➖ Поддерживает только npm/pip/maven/cargo/etc, **не Docker compose service
  image tags в YAML files**. Наш main pin layout вообще не покрыт.
- ➖ Нет custom HOLD аннотаций с человеческими reasons
- ➖ Создаёт по PR на каждое обновление — шум

### B: Renovate Bot
- ➕ Поддерживает Docker compose YAML pins (через regex managers)
- ➕ Group / scheduling support
- ➖ Внешний бот, требует install в org settings
- ➖ Конфиг — сложный JSON, легко сломать
- ➖ HOLD annotations через PR labels — не наш UX (мы хотим **отчёт читать**)

### C: Самописный Python скрипт + GH Action (выбран)
- ➕ Полный контроль над форматом, HOLD reasons как text strings в
  `templates/version_holds.yaml`, не зашиты в bot config
- ➕ Single issue с обновляемым body (по принципу legacy #63) — читать
  таблицу удобнее чем 25 open PRs
- ➕ Mirror legacy AGmind UX точно как user попросил
- ➕ Tests cover regex / scanner / compare логику — refactor safe
- ➖ Поддерживать парсеры в `scripts/checks/version_check.py` руками. Mitigation:
  100 LOC + unit tests, легко.
- ➖ Probe rate limits (Docker Hub anonymous = 100/6h; GitHub anon = 60/h).
  Mitigation: only ~25 pin'ов; fits в anonymous budget.

## Решение

Вариант **C**. Реализовано в Phase P:

### Структура

```
scripts/checks/version_check.py         # main scanner + report renderer
templates/version_holds.yaml      # HOLD config (image → reason)
.github/workflows/version-check.yml  # weekly cron + issue update
tests/governance/test_version_check.py       # 11 unit tests (regex, compare, end-to-end)
```

### scanner pipeline

1. `scan_compose_pins(templates/services/)` — regex `^image:\s*X:T` для каждого
   `*.yaml`. Возвращает list[(image, tag, file)].
2. `scan_dockerfile_pins(docker/)` — regex `^FROM\s+X:T` для каждого
   `Dockerfile.*`.
3. For each unique (image, tag):
   - check `load_holds()` — если есть HOLD → status='hold' + reason
   - else `probe_latest(image)` через:
     - GHCR if `ghcr.io/...` (anonymous token + tags/list)
     - Docker Hub otherwise (hub.docker.com/v2/repositories tags?page_size=50)
     - GitHub Releases для known release-pinned images (TBD)
4. `_compare(current, latest)` → 'up_to_date' / 'patch' / 'minor' / 'major'

Tag filtering: regex `^v?\d+(\.\d+){0,2}([.-][a-zA-Z0-9.-]+)?` — отбрасывает
build IDs (b9049, sha-based, etc).

### Markdown rendering

Mirrors legacy #63 format:

```markdown
## Upstream Version Check — 2026-05-20

| Component | Current | Latest | Status | Note |
|-----------|---------|--------|--------|------|
| `infiniflow/ragflow` | v0.25.5 | v0.25.5 | ✅ up_to_date | templates/services/ragflow.yaml |
| `langgenius/dify-api` | 1.14.2 | 1.14.2 | ✅ up_to_date | templates/services/dify-api.yaml |
| `ghcr.io/ggml-org/llama.cpp` | server-vulkan-b9049 | — | ⏸ HOLD | Phase H bench reference build (b9049). Bump после re-bench. |
...

### Legend
- ✅ up_to_date — pin совпадает с latest
- 📦 patch / 🔄 minor / ⚠️ major — semver delta
- ⏸ HOLD — намеренно держим (см. version_holds.yaml)
- ❌ error — probe failed
```

### GitHub Action

`.github/workflows/version-check.yml`:
- Schedule: `cron: "0 6 * * 1"` (понедельник 06:00 UTC)
- Manual trigger через `workflow_dispatch` с `dry_run` boolean
- Создаёт **single open issue** с label `upstream-update`:
  - Если open issue существует → обновляет title + body
  - Если нет → создаёт новый
- Permissions: `contents: read`, `issues: write` — minimal scope

### holds yaml

```yaml
infiniflow/ragflow:
  reason: "RAGFlow requires ES 9.x compatible с DOC_ENGINE=elasticsearch."
  hold_until: 2026-08-01  # optional

ghcr.io/ggml-org/llama.cpp:
  reason: "Phase H bench reference build (b9049). Bump после re-bench."
```

`hold_until` not enforced в этой первой версии — будущее улучшение auto-clear
после даты.

## Последствия

### Положительные

- **Single source of truth** для known holds — `templates/version_holds.yaml`
  объясняет почему конкретные pins held.
- **Issue body — actionable** — table с `📦 patch` показывает что safe
  bump, `⚠️ major` — где нужен review.
- **No drift между legacy и x86 UX** — user привык к weekly issue format
  из old AGmind, ничего не меняется.
- **Tests cover scanner core** — regex changes / new compose layout
  caught immediately.

### Отрицательные / технический долг

- **Tag canonicalization**: некоторые images используют branch tags
  (`v0.21.2-spark`, `cu130-nightly`). Naive semver compare показывает их
  как malformed — фильтруются через regex.
- **GitHub Releases probe не реализован**. Если upstream использует только
  releases без registry semver tags (rare) — мы покажем error. Add later.
- **Docker Hub rate limits**: 100 anonymous probes per 6h. Запас на 4×
  weekly run + ad-hoc dispatch. Если разрастаемся выше 80 pins — добавить
  Docker Hub login secret.
- **hold_until не enforced**. После даты status всё ещё 'hold' без auto-
  переходa. Manual review каждой holdовой. OK для текущего scale.
- **No per-image rationale link**. Legend объясняет глифы, но не где
  читать **подробности** про `Dify-pinned`. Mitigation: hold reason
  message содержит inline ссылку на ADR (e.g.
  `"см. docs/adr/0011-service-capability-graph.md"`).

### Что нужно сделать

- [x] P.1: `scripts/checks/version_check.py` сканер + report
- [x] P.2: registry probes (Docker Hub + GHCR anonymous)
- [x] P.3: `templates/version_holds.yaml` + parser
- [x] P.4: `.github/workflows/version-check.yml` cron + issue management
- [x] P.5: 11 unit tests + ADR-0012
- [ ] P.6 (future): GitHub Releases probe для cases когда registry tags
      не semver-friendly
- [ ] P.7 (future): auto-clear HOLDs где `hold_until` прошёл
- [ ] P.8 (future): scan pyproject.toml deps (currently only image pins)

## Откат

- `scripts/checks/version_check.py` — изолированный, не импортируется production
  кодом. `git rm` безболезненно.
- GH Action отключается через `disabled: true` в workflow YAML, или
  удалением файла.
- `version_holds.yaml` — additive; удаление просто значит "проверять все
  pins без HOLD аннотаций".
