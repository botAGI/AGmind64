# Zero-friction service onboarding: ресёрч-отчёт (май 2026)

## 1. Service descriptor as single source of truth

**Coolify** (~280 templates) хранит каждый сервис как один YAML c metadata в комментариях шапки + полный compose-блок ниже. Парсер читает `# slogan:`, `# tags:`, `# port:`, `# documentation:` и собирает индекс в `service-templates.json` (base64-encoded compose). Пример (Plausible, реальный из `templates/compose/plausible.yaml`):

```yaml
# ignore: true
# documentation: https://plausible.io/docs/self-hosting
# slogan: "Plausible Analytics is a simple, open-source..."
# category: analytics
# tags: analytics, privacy, google, alternative
# port: 8000
services:
  plausible:
    image: ghcr.io/plausible/community-edition:v3.0.1
    environment:
      - BASE_URL=${SERVICE_FQDN_PLAUSIBLE}
      - SECRET_KEY_BASE=${SERVICE_BASE64_64_PLAUSIBLE}
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8000/api/health || exit 1"]
      interval: 30s
    depends_on:
      plausible-db: { condition: service_healthy }
```

Placeholder-конвенции `SERVICE_FQDN_*` / `SERVICE_PASSWORD_*` — это и есть автоматическая интеграция: Coolify сам подставит FQDN, сгенерит пароль, прокинет в Traefik. Это типовая модель «descriptor → variable substitution → wired stack».

**Dokploy** (200+ templates) использует тот же подход, но шаблоны параметризуются переменными (`refresh_rate`, `retention_days`, `cpu_threshold`) и хранятся как YAML без base64-индекса.

**CapRover** — captain-definition (JSON): `{ "schemaVersion": 2, "dockerfilePath": "./Dockerfile" }` либо с `dockerComposeFile`. Минимализм, но без observability-интеграции из коробки.

**Backstage Catalog Info** — паттерн «metadata в репо сервиса, агрегируется глобально»:

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: qdrant
  annotations:
    prometheus.io/rule: qdrant_rules.yaml
    grafana/dashboard-selector: "service=qdrant"
    backstage.io/source-location: url:https://github.com/.../qdrant
spec:
  type: service
  lifecycle: production
  owner: team-rag
  system: agmind-rag
```

Тут важный паттерн: **annotations как мост в внешние системы** (PagerDuty, Grafana, Prometheus). Для AGmindx86 это прямой образец — annotations описывают «где живут метрики/дашборды/логи» без жёсткой генерации.

**Сравнительная таблица:**

| Tool | Single-file | Auto-Traefik | Auto-Prom | Auto-Logs | Schema |
|---|---|---|---|---|---|
| Coolify | YAML+meta-comments | да (FQDN-vars) | внешне | внешне | нет JSON Schema |
| Dokploy | YAML | да | template-included | внешне | нет |
| CapRover | JSON | да | нет | нет | нет |
| Backstage | YAML (annotations) | n/a | annotations | annotations | да (well-known) |
| **Рекоменд. для AGmindx86** | YAML + Pydantic schema | labels + Jinja2 | docker_sd labels | Alloy labels | Pydantic→JSON Schema |

## 2. Traefik auto-labels — реальное решение

Compose YAML-anchors **не подставляют** placeholder типа `{{name}}`. Это документированное ограничение. Три рабочих варианта:

**(a) Extension fields + anchors для статических значений:**

```yaml
x-traefik-defaults: &traefik-defaults
  traefik.enable: "true"
  traefik.docker.network: edge
  traefik.http.routers.default.middlewares: "default-chain@file"

services:
  qdrant:
    labels:
      <<: *traefik-defaults
      traefik.http.routers.qdrant.rule: "Host(`qdrant.lan`)"
      traefik.http.services.qdrant.loadbalancer.server.port: "6333"
```

Подменяемые имена (`qdrant`) всё равно дублируются — anchor решает только переиспользование статики.

**(b) File provider вместо labels** — рекомендованный 2025-подход: middleware-цепочки в `traefik/dynamic/*.yml`, контейнеры лишь активируют их через `middlewares=default-chain@file`. Сильно сокращает compose.

**(c) Jinja2-генерация compose (AGmindx86 baseline)** — единственный способ автоматически проставлять `{{name}}` в каждом label. Coolify, Dokploy, Portainer Templates делают именно так — у них «шаблон» это compose-с-плейсхолдерами, рендерится при деплое.

Известное ограничение: anchors из base compose **не пробрасываются** в `compose.override.yml` (issue docker/compose#5621). Поэтому overlay-стратегия с anchors не работает — нужен Jinja2 или compose-go merge.

## 3. Prometheus docker_sd_configs

Конфиг для auto-discovery (опубликованный паттерн):

```yaml
scrape_configs:
  - job_name: docker-auto
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 15s
    relabel_configs:
      # keep only opted-in containers
      - source_labels: [__meta_docker_container_label_prometheus_scrape]
        regex: "true"
        action: keep
      # port override
      - source_labels: [__address__, __meta_docker_container_label_prometheus_port]
        regex: "(.*):(?:\\d+);(\\d+)"
        target_label: __address__
        replacement: "${1}:${2}"
      # metrics path
      - source_labels: [__meta_docker_container_label_prometheus_path]
        regex: "(.+)"
        target_label: __metrics_path__
      # propagate AGmind-specific labels
      - source_labels: [__meta_docker_container_label_agmind_tier]
        target_label: tier
      - source_labels: [__meta_docker_container_label_agmind_model]
        target_label: model_name
```

В сервисе достаточно:
```yaml
labels:
  prometheus.scrape: "true"
  prometheus.port: "8000"
  prometheus.path: "/metrics"
  agmind.tier: "inference"
  agmind.model: "qwen2-7b"
```

**Static targets vs docker_sd:** static — детерминированно, версионируется, но требует ручной правки на каждый сервис. docker_sd — zero-touch, но риск race-conditions (контейнер up до того, как metrics endpoint готов — нужен `start_period` в healthcheck) и label-collisions (два проекта с одинаковым `agmind.tier`). Рекомендация — docker_sd + явный whitelist через `prometheus.scrape=true`.

## 4. Loki/Alloy auto-discovery

Грубо тот же паттерн, через Alloy (новый агент Grafana, заменяет Promtail). Канонический конфиг 2026:

```hcl
discovery.docker "containers" {
  host = "unix:///var/run/docker.sock"
  refresh_interval = "5s"
}

discovery.relabel "containers" {
  targets = discovery.docker.containers.targets
  rule {
    source_labels = ["__meta_docker_container_label_loki_scrape"]
    regex         = "true"
    action        = "keep"
  }
  rule {
    source_labels = ["__meta_docker_container_label_agmind_service"]
    target_label  = "service_name"
  }
  rule {
    source_labels = ["__meta_docker_container_label_agmind_tier"]
    target_label  = "tier"
  }
}

loki.source.docker "default" {
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.containers.output
  forward_to = [loki.write.default.receiver]
  labels     = { env = "prod" }
}
```

Один общий ключ `loki.scrape=true` + propagated labels — и сервис автоматически в Loki с правильным `service_name`, `tier`.

## 5. Healthcheck как single source

Реальный паттерн (применяется в Coolify-templates):

1. **Compose**: `healthcheck:` блок — Docker сам перезапускает контейнер.
2. **Traefik**: `traefik.http.services.X.loadbalancer.healthcheck.path=/health` — Traefik исключает unhealthy из роутинга.
3. **Prometheus blackbox**: scrape `blackbox-exporter` с targets из тех же docker labels:
   ```yaml
   - job_name: blackbox-http
     metrics_path: /probe
     params: { module: [http_2xx] }
     docker_sd_configs: [{ host: unix:///var/run/docker.sock }]
     relabel_configs:
       - source_labels: [__meta_docker_container_label_healthcheck_url]
         target_label: __param_target
       - target_label: __address__
         replacement: blackbox:9115
   ```

Source-of-truth — label `healthcheck.url=http://service:port/health`. Compose-healthcheck парсит её через `wget -q $HEALTHCHECK_URL`; Traefik читает; blackbox через relabel. Один URL — три потребителя.

## 6. Schema validation: Pydantic v2 → JSON Schema

Стандарт 2026. Workflow:

```python
# templates/schemas/service.py
from pydantic import BaseModel, Field
class ServiceDescriptor(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")
    image: str
    tier: Literal["edge","inference","storage","ops"]
    port: int = Field(ge=1, le=65535)
    healthcheck_url: str | None = None
    traefik_host: str | None = None
    prometheus_scrape: bool = True
    loki_scrape: bool = True
    owner: str
```

```bash
python -c "import json; from agmind.schemas import ServiceDescriptor; \
  print(json.dumps(ServiceDescriptor.model_json_schema(), indent=2))" \
  > templates/schemas/service.json
```

В `services.yaml`:
```yaml
# yaml-language-server: $schema=./schemas/service.json
qdrant:
  image: qdrant/qdrant:v1.15
  tier: storage
  port: 6333
  owner: team-rag
```

VSCode YAML extension даёт автодополнение/inline-ошибки. Pre-commit hook через `check-jsonschema --schemafile templates/schemas/service.json templates/services/*.yaml` — fail-fast в CI. Pydantic v2 JSON Schema соответствует Draft 2020-12 — поддерживается всеми тулами.

## 7. Generator: чем рендерить compose в 2025-2026

| Tool | Pro | Contra | Кто использует |
|---|---|---|---|
| **Python + Jinja2** | низкий порог, гибкость | runtime errors, no types | AGmindx86 сейчас, Ansible |
| **compose-go SDK** (Docker official) | каноничный merge/normalize, валидация против compose-spec | Go, не подходит для AGmind core | Docker CLI, Compose v2 |
| **CUE** | type-safety + merge + JSON Schema в одном; готовая `compose` схема в CUE Central Registry | learning curve | Mercari, Timoni, ArgoCD-CUE |
| **Pkl** (Apple) | строгая типизация, validation compile-time; `ComposePkl` уже существует | новый язык, малое сообщество | Apple internal, ComposePkl |
| **Jsonnet** | взрослый, гибкий | runtime errors, dynamic | Grafana dashboards, Tanka |

**Что реально используют успешные проекты:**
- **Coolify, Dokploy, Portainer**: shell/PHP/Node + плейсхолдеры в YAML → substitution на деплое (низкоуровневое решение).
- **ArgoCD**: не генерирует compose, а раскручивает Helm/Kustomize.
- **Timoni** (k8s-only): CUE end-to-end.
- **2026 best practice для compose-based стека**: Pydantic schema + Jinja2 + `docker compose config --quiet` для финальной валидации через compose-go под капотом. CUE — если команда готова инвестировать в типобезопасность.

## 8. Python plugin system через entry points (PEP 621)

Стандартный паттерн (используется pytest/pluggy, MLflow, SQLAlchemy dialects, Flask-extensions, JupyterLab):

**Core (`agmind/pyproject.toml`)** — НЕ объявляет конкретные backends, только группу:
```toml
[project.entry-points."agmind.backends"]
cpu     = "agmind.compute.backends.cpu:CPUBackend"
vulkan  = "agmind.compute.backends.vulkan:VulkanBackend"
rocm    = "agmind.compute.backends.rocm:ROCmBackend"
```

**Third-party plugin (`agmind-intel-backend/pyproject.toml`):**
```toml
[project.entry-points."agmind.backends"]
xpu = "agmind_intel_backend.backend:IntelXPUBackend"
```

**Discovery в `agmind/compute/_registry.py`:**
```python
from importlib.metadata import entry_points

def discover_backends() -> dict[str, type[Backend]]:
    eps = entry_points(group="agmind.backends")
    result = {}
    for ep in eps:
        try:
            result[ep.name] = ep.load()
        except Exception as e:
            log.warning("backend %s failed to load: %s", ep.name, e)
    return result
```

Это **полностью устраняет** 7-8 мест правки для нового backend. Третьи стороны делают `pip install agmind-intel-backend` — backend появляется в registry без модификации core.

**MLflow** — канонический real-world: `[project.entry-points."mlflow.project_backend"] dummy = "pkg:Backend"` + AbstractBackend interface. **Pluggy** (используется pytest, tox, devpi) — даёт уровень выше: `@hookspec` / `@hookimpl` с явными контрактами и порядком исполнения через `tryfirst`/`trylast`/`wrapper`. Для AGmind с одним методом `compute()` на backend — простой entry-points достаточно, pluggy overkill.

**Минусы entry-points:**
- Lazy import не из коробки — `ep.load()` импортит модуль; решение через `LazyLoader` обёртку.
- Ошибки entry-point не видны без try/except (как выше).
- Версионирование интерфейса: нужен `ABC` + `__version__` контракт, иначе старый плагин ломает новый core. Решается семвером и runtime-проверкой `Protocol`/`isinstance(ABC)`.

## 9. End-to-end UX «новый сервис за 5 минут»

Ближайший аналог — **Coolify** (через UI) и **Backstage Software Templates** (через `scaffolder`). Для CLI-first проектов реалистичный flow для AGmindx86:

```bash
agmind service scaffold qdrant --tier storage --template vector-db
#  -> templates/services/qdrant.yaml (с # yaml-language-server: $schema=...)
#  -> templates/grafana/dashboards/qdrant.json (из dashboard-template)
#  -> templates/prometheus/rules/qdrant.yaml (alert rules stub)

vim templates/services/qdrant.yaml   # юзер правит image/env/port

agmind service validate qdrant
#  - JSON Schema check
#  - dependency graph (depends_on cycles)
#  - port conflicts vs other services
#  - traefik host uniqueness

agmind deploy up --profile rag
#  - render docker-compose.yml через Jinja2
#  - docker compose config --quiet  (compose-spec validation)
#  - docker compose up -d
#  - Prometheus auto-picks via docker_sd
#  - Alloy auto-picks via discovery.docker
#  - Grafana: provisioning подхватывает templates/grafana/dashboards/qdrant.json

agmind service status qdrant
#  Endpoint:    https://qdrant.lan
#  Metrics:     https://qdrant.lan/metrics  (scraped: yes, last: 12s ago)
#  Logs:        loki query {service_name="qdrant"}
#  Dashboard:   https://grafana.lan/d/qdrant
#  Health:      OK (200, 14ms)
```

Backstage Templates делают именно это — `scaffolder` + `software-templates` репо, плюс автогенерация catalog-info.yaml через **Score** (тренд 2025). Для AGmindx86 ближе CLI-paradigm: scaffold-команда + Jinja2-templates + Pydantic-validate.

## 10. Anti-patterns

1. **Docker socket в Traefik как root-канал** — широко известный security-risk (traefik issue #4174). Решение: docker-socket-proxy с RBAC (read-only `/containers`, `/networks`), отдельный socket для Prometheus/Alloy.
2. **Label collision между compose-проектами** — два сервиса с одинаковым `traefik.http.routers.api.rule` → один молча перетирает другой. Конвенция: routers/services с префиксом `{project}-{service}`.
3. **YAML anchors через override-файлы** — не работают (docker/compose#5621). Не пытайтесь строить «multi-environment через base + override + anchors». Используйте Jinja2 или compose-go merge.
4. **Auto-discover = «всё в проде по умолчанию»** — Prometheus заскрейпит всё, что висит на network. Всегда whitelist через `prometheus.scrape=true`, иначе internal sidecars, secrets-init контейнеры начнут отдавать /metrics с PII.
5. **Pluggy/entry-points без version-контракта** — старый плагин с устаревшим API падает на runtime внутри hot-path. Минимум: `class Backend(Protocol)` + `BACKEND_API_VERSION` в core + проверка при load.
6. **Healthcheck-url в три места копи-пастой** — раздобывайте из labels, не дублируйте в `prometheus.yml` руками.
7. **Jsonnet/CUE/Pkl для команды из 1 человека** — реальный cost ownership > benefit. CUE имеет смысл при 50+ сервисах и команде, которая будет его учить.
8. **«Один YAML на всё»** при росте: когда `services.yaml` > 500 строк, split на `templates/services/<name>.yaml` (Coolify, Backstage делают именно так).

---

## Конкретно для AGmindx86 — рекомендованный стек

| Слой | Инструмент | Файл |
|---|---|---|
| Schema | Pydantic v2 | `agmind/schemas/service.py` → `templates/schemas/service.json` |
| Descriptor | YAML, 1 файл/сервис | `templates/services/<name>.yaml` |
| Render | Jinja2 (текущий baseline ОК) | `ansible/roles/services/templates/` |
| Final validate | `docker compose config` (compose-go под капотом) | в `Makefile dod-phase-X` |
| Traefik | labels через Jinja2 + file-provider middleware chain | `templates/traefik/dynamic/` |
| Prometheus | `docker_sd_configs` + `prometheus.scrape=true` whitelist | `templates/prometheus/prometheus.yml.j2` |
| Logs | Grafana Alloy + `discovery.docker` + `loki.scrape=true` | `templates/alloy/config.alloy` |
| Healthcheck | один label `healthcheck.url=...`, потребляется compose + Traefik + blackbox | в descriptor |
| Compute plugins | setuptools entry points group `agmind.backends` | `agmind/pyproject.toml`, third-party packages |
| UX | `agmind service scaffold/validate/status` CLI | новые подкоманды в `agmind/cli/` |

CUE/Pkl — отложить до фазы, когда >20 сервисов и есть готовность к learning curve. Pluggy — не нужен, простой entry-points достаточно для backends.

---

## Sources

- [Coolify Service Template docs](https://coolify.io/docs/get-started/contribute/service)
- [Coolify Plausible YAML example](https://github.com/coollabsio/coolify/blob/v4.x/templates/compose/plausible.yaml)
- [Coolify Service Templates wiki](https://deepwiki.com/coollabsio/coolify/4.5-service-templates)
- [Dokploy Prometheus template](https://docs.dokploy.com/docs/templates/prometheus)
- [Dokploy Prom Monitoring Extension](https://docs.dokploy.com/docs/templates/dokploy-prom-monitoring-extension)
- [Coolify vs Dokploy comparison 2026](https://getdeploying.com/guides/coolify-vs-dokploy)
- [CapRover captain-definition](https://caprover.com/docs/captain-definition-file.html)
- [CapRover Service Update Override (YAML/JSON)](https://caprover.com/docs/service-update-override.html)
- [Backstage Descriptor Format](https://backstage.io/docs/features/software-catalog/descriptor-format/)
- [Backstage catalog-info.yaml example](https://github.com/backstage/backstage/blob/master/catalog-info.yaml)
- [Score → Backstage descriptor generation](https://medium.com/@mabenoit/generate-your-backstage-software-catalog-files-with-score-b62aa33e8ecc)
- [Ultimate Traefik Docker Compose Guide 2025](https://www.simplehomelab.com/udms-18-traefik-docker-compose-guide/)
- [Traefik label reuse forum](https://community.traefik.io/t/reuse-repetitive-labels/1998)
- [Docker Compose Extensions (x-fields)](https://docs.docker.com/reference/compose-file/extension/)
- [docker/compose anchors-in-override issue #5621](https://github.com/docker/compose/issues/5621)
- [Docker socket security in Traefik issue #4174](https://github.com/traefik/traefik/issues/4174)
- [Traefik Docker provider docs](https://doc.traefik.io/traefik/providers/docker/)
- [Prometheus configuration reference](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Prometheus docker labels discovery example (Sqooba)](https://github.com/Sqooba/prometheus-docker-labels-discovery)
- [Prometheus Scraping Configurations by Example](https://john-tucker.medium.com/prometheus-scraping-configurations-by-example-cc2ffea2cef6)
- [Demystifying Prometheus Docker Compose labels](https://howik.com/prometheus-docker-compose-lables)
- [Grafana Alloy monitor Docker containers](https://grafana.com/docs/alloy/latest/monitor/monitor-docker-containers/)
- [Alloy loki.source.docker reference](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.docker/)
- [Manage container logs with Alloy + Loki (2026)](https://middlewaretechnologies.in/2026/01/how-to-manage-container-logs-with-grafana-alloy-and-loki.html)
- [Blackbox exporter GitHub](https://github.com/prometheus/blackbox_exporter)
- [Blackbox health checks article](https://blog.devgenius.io/prometheus-blackbox-service-health-checks-1c7051eb351f)
- [Pydantic JSON Schema (latest)](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)
- [Pydantic + JSON Schema + VSCode example repo](https://github.com/is3ka1/pydantic-jsonschema-with-vscode-example)
- [compose-spec JSON Schema](https://github.com/compose-spec/compose-spec/blob/main/schema/compose-spec.json)
- [Docker Compose SDK (compose-go)](https://docs.docker.com/compose/compose-sdk/)
- [compose-go pkg.go.dev](https://pkg.go.dev/github.com/docker/compose/v2)
- [Docker Compose merge docs](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)
- [Docker Compose override strategies (2026)](https://oneuptime.com/blog/post/2026-01-30-docker-compose-override-strategies/view)
- [CUE getting started with Docker Compose](https://cue.dev/docs/getting-started-with-docker-compose-cue/)
- [Timoni (CUE-based)](https://timoni.sh/)
- [Apple Pkl GitHub](https://github.com/apple/pkl)
- [ComposePkl (Pkl for Docker Compose)](https://rossollc.com/)
- [Python Packaging entry points spec](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Creating and discovering plugins (PyPA)](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
- [setuptools entry_point docs](https://setuptools.pypa.io/en/latest/userguide/entry_point.html)
- [pluggy GitHub](https://github.com/pytest-dev/pluggy)
- [MLflow Plugins (latest)](https://mlflow.org/docs/latest/ml/plugins/)
- [Self-hosted deployment tools compared (Coolify/Dokploy/Kamal/Dokku)](https://dev.to/ameistad/self-hosted-deployment-tools-compared-coolify-dokploy-kamal-dokku-and-haloy-2npd)
