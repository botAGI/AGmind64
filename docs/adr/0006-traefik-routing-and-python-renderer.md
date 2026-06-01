# ADR-0006: Traefik v3 + Python Compose Renderer

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0005 (Service Descriptor), Phase H'.C, deep-dive 01-traefik-llama-server.md

## Контекст

Phase H'.B завершила split монолитного `services.yaml` на 32 типизированных `templates/services/<name>.yaml`. Каждый сервис теперь — Pydantic `ServiceDescriptor`. Но:

1. **Рендерер всё ещё Jinja2** в `ansible/roles/services/templates/docker-compose.yml.j2` — нетипизированный, тяжело тестируемый, не понимает новые поля schema (`tier`, `routing`, `observability`, `command`, `devices`, `group_add`).
2. **Nginx hardcoded** как reverse proxy. Из требований пользователя и deep-dive 01: Traefik v3 — лучший выбор для self-hosted AI стека (auto-discovery через docker labels, SSE-safe настройки, средние weight средств для middleware chains).
3. **Нет auto-discovery**: добавление нового сервиса требует ручной правки `prometheus.yml`, `nginx-default.conf`, Loki scrape конфига. Это противоречит цели "fast добавить новый сервис, он подхватился метриками, получил нейминг" (см. memory `feedback-tui-devops`).
4. **SSE через прокси — критичный точка отказа** (deep-dive 01 §1): дефолтные buffering/HTTP-2 настройки tihie ломают llama-server streaming. Нужен унифицированный механизм пометить SSE-сервисы.

## Рассмотренные варианты

### A: оставить Jinja2 + добавить Traefik labels через шаблон
- ➕ Минимум кода
- ➖ Jinja2 не понимает Pydantic типы — Traefik labels пишутся как строки без проверки
- ➖ Дублирование логики в шаблоне (генерация labels) — сложно тестировать
- ➖ Сложно сделать SSE-safe defaults (`flushinterval=1ms`, `tls.options=no-http2@file`)

### B: Python renderer на ServiceDescriptor (выбран)
- ➕ Типизированный (Pydantic → dict → YAML), тестируемый (pytest)
- ➕ Логика generation Traefik / Prometheus / Loki labels в одном месте (`agmind/services/renderer.py`)
- ➕ Reusable: `agmind render compose` CLI команда + Ansible вызывает её
- ➕ Легко расширять (новый tier, новые labels) — правка только Python
- ➖ Дополнительная зависимость от PyYAML (мы её и так используем)
- ➖ Migration cost — Jinja2 шаблон становится deprecated

### C: Helm + k3s
- ➕ Декларативный, индустрия-стандарт
- ➖ Overkill для 1-3 нод (см. deep-dive Q&A про k8s)
- ➖ Лишний слой абстракции (k8s manifests vs Compose)
- ➖ Не соответствует решению user'а оставаться на Compose до scale

### D: Caddy v2 как alternative
- ➕ Простой Caddyfile DSL
- ➖ Docker auto-discovery — community plugin, не first-party
- ➖ Меньше middleware из коробки (forwardAuth, ratelimit с ipStrategy)
- Решено в deep-dive 01: Traefik лучше для 32-service стека
> Superseded by Phase 08: caddy removed from the catalog (user decision).

## Решение

Вариант **B**: Python renderer `agmind/services/renderer.py` + Traefik v3.7.1 как default reverse proxy.

### Ключевые design decisions

1. **Renderer — pure function**: `load_descriptors() → filter_by_profile() → render_compose() → to_yaml()`. Без side effects, легко тестируется.
2. **Traefik labels из RoutingConfig**:
   - `routing.host` → `Host(...)` rule
   - `routing.middleware_chain` → `chain-llm@file` / `chain-internal@file` / `chain-public@file`
   - `routing.sse=True` → автоматически добавляет `responseforwarding.flushinterval=1ms` и `tls.options=no-http2@file` (фикс для SSE streaming)
   - `routing.healthcheck_path` → `loadbalancer.healthcheck.path`
3. **Observability labels из ObservabilityConfig**:
   - `prometheus_scrape=True` → `prometheus.scrape=true` (whitelist для docker_sd_configs)
   - `loki_scrape=True` (default) → `loki.scrape=true` (Alloy discovery.docker)
   - Всегда: `agmind.service`, `agmind.tier`, `agmind.owner` для grouping в queries
4. **Middleware chains в file provider** (не labels): `templates/traefik/dynamic/middlewares.yml` определяет три chain:
   - `chain-llm`: rate-limit + security-headers + Authelia, БЕЗ buffering
   - `chain-internal`: security-headers + Authelia (Grafana/Portainer/Prometheus)
   - `chain-public`: rate-limit + security-headers (без auth)
5. **TLS options в file provider**: `no-http2` для SSE endpoints (см. deep-dive 01 §1).
6. **Logging defaults включены**: `json-file 50m × 3 файла` на каждом сервисе (предотвращает 100GB log bloat).
7. **YAML anchors для logging**: PyYAML safe_dump использует anchors (`&id001` / `*id001`) автоматически — экономит ~40% размера compose файла.
8. **Nginx → `core-nginx` alternative profile**: Traefik default в `core`, nginx был opt-in fallback (для setup без публичного домена). > Superseded by Phase 08: nginx removed from the catalog (user decision — same defect class as caddy).
9. **Ansible role calls `agmind render compose`** вместо Jinja2 lookup — single source of truth.

### Public services с Traefik routing

Заполнены 8 сервисов:

| Service | Host | Middleware | SSE |
|---|---|---|---|
| llama-llm | llama.agmind.dev | chain-llm | ✅ |
| llama-embed | embed.agmind.dev | chain-llm | ❌ |
| llama-rerank | rerank.agmind.dev | chain-llm | ❌ |
| grafana | grafana.agmind.dev | chain-internal | ❌ |
| openwebui | chat.agmind.dev | chain-llm | ✅ |
| dify-web | dify.agmind.dev | chain-llm | ✅ |
| ragflow | rag.agmind.dev | chain-llm | ✅ |
| portainer | portainer.agmind.dev | chain-internal | ❌ |

## Последствия

### Положительные

- Добавление сервиса: создать `templates/services/<name>.yaml` с `routing.host` → автоматически роутится через Traefik. **Никаких правок rendering кода**.
- SSE правильно работает для llama-server / Dify / Open WebUI / RAGFlow из коробки.
- Auto-discovery Prometheus/Loki через docker labels (Phase H'.D wiring).
- `agmind render compose --diff` показывает что изменится перед deploy — закладка под Phase L DevOps Excellence.
- 57 renderer tests + 70 descriptor tests = 127 регрессионных тестов в одном слое.

### Отрицательные / технический долг

- Старый `services.yaml` остался как legacy (читается `agmind.services.registry.load_registry`). Удалим в Phase H'.E когда registry.py переключится на ServiceDescriptor.
- `docker-compose.yml.j2` и `nginx-default.conf.j2` deprecated (Phase H'.E удалит).
- `extra_args` поле deprecated, в Phase H'.E удалить (миграция уже разнесла по `devices/group_add/security_opt/cap_add`).
- Public домен (R15) ещё не выбран — `agmind.dev` placeholder. Пользователь решит купить vs `step-ca`. Файлы routing используют placeholder, легко sed-replace при выборе.

### Что нужно сделать

- [x] H'.C.1: Python renderer + 57 tests
- [x] H'.C.2: Schema extension (command/devices/group_add/security_opt/cap_add)
- [x] H'.C.3: Traefik service descriptor + middlewares.yml + transport.yml
- [x] H'.C.4: `agmind render compose` CLI + Ansible role update
- [ ] H'.D: docker_sd_configs Prometheus + Alloy discovery.docker
- [ ] H'.E: удалить legacy `services.yaml` + `docker-compose.yml.j2` + `nginx-default.conf.j2`

## Бенчмарки

| Действие | До (Jinja2) | После (Python) |
|---|---|---|
| Render core profile | n/a (Ansible only) | 0.06s локально |
| Add new service | 3+ файлов править | 1 файл (`templates/services/X.yaml`) |
| Add Traefik routing | hardcode в nginx + compose | 4 строки в descriptor |
| Tests | 0 | 127 (renderer + descriptors + schema) |
| Lines of code | 60 Jinja2 + 200 nginx.conf | 280 Python (typed) |

## Откат

Если Python renderer показал проблемы:
1. `agmind render compose` остаётся, но Ansible role переключается обратно на `docker-compose.yml.j2` (старый шаблон не удалён до Phase H'.E).
2. ServiceDescriptor может конвертироваться в legacy Service через `to_legacy_service()` — старый Jinja2 продолжит работать.
3. Traefik service descriptor можно временно вынести в `core-traefik` alternative profile. (nginx удалён из каталога в Phase 08 — не применимо как вариант отката.)

Точки отката не блокируют ни одну продакшен фичу.
