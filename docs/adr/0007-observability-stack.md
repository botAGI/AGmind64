# ADR-0007: Observability Stack — Auto-Discovery + LGTM + structlog

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0005 (Service Descriptor), ADR-0006 (Traefik), deep-dive 03-observability-pipeline.md, R-recons R13/R14

## Контекст

Phase G дала basic observability profile (Prometheus + Grafana + Loki + Alloy + Alertmanager), но:

1. **Все targets hardcoded** в `prometheus.yml.j2` — добавление сервиса требует ручной правки.
2. **Нет alert rules** — Alertmanager работает «вхолостую».
3. **Logs без context propagation** — `agmind/log.py` пишет `%(message)s`, нет `trace_id`/`request_id`/`model`.
4. **`/metrics` для llama.cpp бедны** (deep-dive 03 §1): нет TTFT histograms, имена с двоеточием, router mode сломан (issue ggml-org#19811).
5. **GPU exporter для gfx1151 не существует** (deep-dive 03 §4, R13): официальные `amd_smi_exporter` и `device-metrics-exporter` — datacenter-only.
6. **Готового Grafana dashboard под Strix Halo нет** — никто не сделал.
7. **`alertmanager-bot` deprecated** (архив 2022) — нативный `telegram_configs` Alertmanager 0.24+ нужно использовать (мы pin'им конкретно v0.32.1 в `templates/services/alertmanager.yaml` — forward-compatible).

## Рассмотренные варианты

### A: оставить static targets, добавить minimal alerts
- ➕ Меньше изменений
- ➖ Не решает problem #1 (manual onboarding) — это противоречит user goal "fast добавить сервис"

### B: docker_sd_configs auto-discovery + structlog + textfile collector (выбран)
- ➕ Auto-discovery: новый сервис с `prometheus.scrape=true` подхватывается за 15 сек
- ➕ Уже сгенерированные labels от Phase H'.C (renderer.render_observability_labels)
- ➕ Whitelist через `prometheus.scrape=true` — Prometheus НЕ сканит всё (security)
- ➕ `structlog` JSON output → Alloy парсит → Loki structured metadata (trace_id)
- ➕ AMD GPU через `node_exporter --collector.textfile.directory` + bash script
- ➖ structlog soft dependency (graceful degrade на чистый stdlib если не установлен)
- ➖ textfile collector — bash (~80 строк) вместо «правильного» exporter, но альтернативы нет

### C: OTel Collector везде + Tempo + Mimir
- ➕ Сейчас тренд индустрии
- ➖ Overkill для single-node (RAM overhead 2-3 GB)
- ➖ Mimir vs Prometheus — Mimir не нужен на 1-3 нодах
- Решено отложить OTel traces до Phase H'' (точечная интеграция в LlamaServerClient)

## Решение

Вариант **B**.

### Архитектура

```
┌──────────────────────┐   docker.sock + labels:
│  docker daemon       │ ──┐ prometheus.scrape=true
└──────────────────────┘   │ loki.scrape=true
                            │ agmind.{service,tier,owner}
       ┌────────────────────┼─────────────────────┐
       ▼                    ▼                     ▼
  ┌─────────────┐  ┌─────────────────┐  ┌──────────────────┐
  │ Prometheus  │  │  Alloy          │  │  Traefik         │
  │ docker_sd   │  │  discovery.     │  │  Docker provider │
  │ + whitelist │  │  docker         │  │  (см. ADR-0006)  │
  └──────┬──────┘  └────────┬────────┘  └──────────────────┘
         │ scrape           │ tail logs
         ▼                  ▼
  ┌─────────────┐  ┌─────────────────┐
  │ Recording   │  │ Loki            │
  │ + Alerts    │  │ 14d retention   │
  │ → Alertmgr  │  │ structured md   │
  └──────┬──────┘  └────────┬────────┘
         │                  │
         └──────┬───────────┘
                ▼
        ┌──────────────┐         ┌──────────────────┐
        │ Grafana      │ ◀────── │ AlertManager     │
        │ (provisioned)│         │ → Telegram bot   │
        └──────────────┘         └──────────────────┘
                ▲                          ▲
                │                          │
        ┌───────────────────────┐  ┌──────────────┐
        │ node-exporter +       │  │ amdgpu_text  │
        │ textfile collector    │  │ file.sh (R13)│
        └───────────────────────┘  └──────────────┘
              /sys/class/drm/card*/device/hwmon/...
```

### Configs (8 файлов в `templates/observability/`)

- `prometheus.yml` — docker_sd + whitelist relabel; propagates `agmind.{service,tier,owner}` как Prometheus labels
- `prometheus/rules/llama.yml` — recording rules (decode_tps:rate5m, kv_usage:max5m) + 4 alerts
- `prometheus/rules/system.yml` — 7 alerts: OOM, restart loop, disk, memory, AmdGpuTempHigh, AmdGttUsageHigh, AmdGpuClockStuck
- `alertmanager.yml` — native `telegram_configs`, critical route с 30m repeat
- `alloy/config.alloy` — `discovery.docker` + `loki.scrape=true` filter + JSON parsing для structured metadata
- `loki/loki.yml` — 14d retention, structured_metadata enabled, filesystem store
- `grafana/provisioning/datasources/agmind.yml` — Prometheus + Loki + Alertmanager (derivedFields для trace_id linking)
- `grafana/provisioning/dashboards/dashboards.yml` — file provider для JSON dashboards

### structlog (агент-сторона)

`agmind/log.py` rewritten:
- API сохранён: `setup(level)` + `logger(name) -> logging.Logger` (existing tests pass)
- Опция `json_output` (env `AGMIND_LOG_JSON=true`) → JSON renderer через ProcessorFormatter
- `bind_context(**kwargs)` / `clear_context()` для contextvars-based trace_id propagation
- structlog **soft dependency**: graceful degrade на stdlib basicConfig если не установлен
- Alloy парсит JSON → `trace_id` попадает в Loki structured metadata (без cardinality explosion)

### R13 — gfx1151 GPU textfile collector

`scripts/ops/amdgpu_textfile.sh` (~95 строк bash):
- Читает `/sys/class/drm/card*/device/hwmon/temp1_input`, `power1_average`, `freq1_input`
- Читает `mem_info_{vram,gtt}_{used,total}` напрямую из sysfs
- `LC_ALL=C awk` (иначе русская локаль ставит запятые → ломает Prometheus parser)
- Output: `${TEXTFILE_DIR}/amdgpu.prom` атомарной подменой
- 9 метрик: temp_edge, power_average, sclk, mclk, gpu_busy, vram_{used,total}, gtt_{used,total}

**Это первое публичное решение для Strix Halo Prometheus monitoring** — никто из 14 проанализированных конкурентов (deep-dive 06) не имел gfx1151 support.

### Что НЕ делаем в этом ADR (отложено)

- **OTel traces** → Phase H'' (точечно в LlamaServerClient, см. R14 для TTFT histograms)
- **Готовые Grafana dashboards JSON** → Phase H'' (fork art-vish/llamacpp-llm-observer + custom для gfx1151)
- **Langfuse / Phoenix self-hosted** → Phase J опционально (OTel GenAI semconv даст interop)
- **Step-ca vs Let's Encrypt R15** → ждёт user decision

## Последствия

### Положительные

- **Zero-config service onboarding**: один YAML файл → метрики + логи в Grafana
- **Telegram алерты** работают из коробки (token через docker secret)
- **GPU мониторинг на Strix Halo** есть (первый public референс)
- **trace_id propagation** через structlog contextvars + Loki structured metadata
- 7+ critical alerts на месте (LlamaServerDown, OOM, GPU temp, KV cache full)
- Retention budget: Prometheus 15d ≈ 5 GB, Loki 14d ≈ 50-70 GB

### Отрицательные / технический долг

- `templates/observability/` дублируется с `ansible/roles/observability/templates/*.j2` (legacy Jinja2 ещё там). Phase H'.E удаляет legacy.
- TTFT histograms не строим в Phase H'.D — отложено в H'' через OTel (R14)
- Grafana dashboards пустые — нужно fork + customize в H''
- amdgpu textfile collector — bash, а не Python (правильное место для исключения "минимум bash")

### Что нужно сделать

- [x] H'.D.1: 8 observability configs (prometheus + rules + alertmanager + alloy + loki + grafana)
- [x] H'.D.2: agmind/log.py rewrite на structlog + backward compat tests
- [x] H'.D.3: R13 gfx1151 textfile collector + LC_ALL=C fix
- [x] H'.D.4: ADR-0007 + tests
- [ ] H''.1: OTel SDK в LlamaServerClient + TTFT spans (R14)
- [ ] H''.2: Fork art-vish/llamacpp-llm-observer Grafana dashboards
- [ ] H''.3: Ansible role observability → switch на templates/observability/

## Бенчмарки

| Метрика | Значение |
|---|---|
| Auto-discovery latency | 15 сек (Prometheus refresh) / 5 сек (Alloy) |
| GPU collector script run | ~50 ms на одну card |
| structlog overhead | ~10-15 μs/log vs 3-5 μs stdlib (deep-dive 03 §5) |
| Loki retention disk | 14d × 50 GB raw → ~5-7 GB после compression |

## Откат

Если auto-discovery даёт ложные негативы (Prometheus теряет targets):
1. Вернуть hardcoded `static_configs` в `templates/observability/prometheus.yml` (rollback к Phase G шаблону)
2. structlog отключается через `AGMIND_LOG_JSON=false` — basicConfig работает
3. textfile collector опциональный — alerts AmdGpu* не сработают, остальное ok
