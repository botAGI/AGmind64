# Observability pipeline для AGmindx86: отчёт

## 1. llama.cpp `/metrics` — что реально expose

Запуск: `llama-server --metrics --host 0.0.0.0 --port 8080 -m model.gguf`. Доступен с **b1882+** (Feb 2024, issue #5850). Формат: Prometheus text, но с **двоеточиями** в именах (`llamacpp:prompt_tokens_total`) — нарушает naming convention, активно обсуждается issue #19811.

**Полный набор метрик** (по состоянию b6500–b7010):

| Metric | Тип | Описание / Единицы |
|---|---|---|
| `llamacpp:prompt_tokens_total` | counter | Суммарно prompt tokens обработано |
| `llamacpp:tokens_predicted_total` | counter | Суммарно generation tokens |
| `llamacpp:tokens_predicted_seconds_total` | counter | Время генерации (сек), для rate-вычислений |
| `llamacpp:prompt_tokens_seconds` | gauge | Avg prompt throughput tok/s |
| `llamacpp:predicted_tokens_seconds` | gauge | Avg decode throughput tok/s |
| `llamacpp:requests_processing` | gauge | Активные запросы (parallel slots в работе) |
| `llamacpp:requests_deferred` | gauge | Очередь deferred |
| `llamacpp:n_decode_total` | counter | Кол-во `llama_decode()` вызовов |
| `llamacpp:n_busy_slots_per_decode` | gauge | Avg занятых slots на decode (батч-эффективность) |
| `llamacpp:kv_cache_usage_ratio` | gauge | KV cache fill, 0..1 (есть начиная с b3900+) |
| `llamacpp:kv_cache_tokens` | gauge | Tokens в KV cache |

**Чего нет** (важно знать заранее): **histograms по TTFT** (нет `_bucket` метрик — только средние gauges), **per-model labels** (issue #19811 — приходится разделять через job/instance в Prometheus), **router-mode aggregated /metrics** (нужен `?model=` query, discussion #19197).

**Compose snippet** (per-instance label через `external_labels` в Prometheus, не в llama-server):

```yaml
llama-q4:
  image: ghcr.io/ggml-org/llama.cpp:server-b7010
  command: >
    --host 0.0.0.0 --port 8080 --metrics
    -m /models/qwen2.5-32b-q4_k_m.gguf -c 32768 -ngl 99
  ports: ["8080:8080"]
  volumes: ["/srv/models:/models:ro"]
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:8080/health"]
    interval: 30s
    timeout: 5s
    retries: 3
```

`--metrics-tag` **не существует**. Layering модели делается через отдельные scrape jobs с разными `instance`/`model` labels.

## 2. Prometheus scrape config — multi-llama-server

Static targets проще под декларативную модель (нет docker labels-magic, всё в git):

```yaml
scrape_configs:
  - job_name: llama_q4
    metrics_path: /metrics
    static_configs:
      - targets: ["llama-q4:8080"]
        labels: {model: "qwen2.5-32b-q4", role: "chat"}
  - job_name: llama_q8
    static_configs:
      - targets: ["llama-q8:8081"]
        labels: {model: "qwen2.5-14b-q8", role: "chat"}
  - job_name: llama_embed
    static_configs:
      - targets: ["llama-embed:8082"]
        labels: {model: "bge-m3", role: "embed"}
```

**Recording rules** (`rules/llama.yml`):

```yaml
groups:
  - name: llama_aggregations
    interval: 30s
    rules:
      - record: agmind:llama_decode_tps:rate5m
        expr: rate(llamacpp:tokens_predicted_total[5m])
      - record: agmind:llama_prompt_tps:rate5m
        expr: rate(llamacpp:prompt_tokens_total[5m])
      - record: agmind:llama_avg_decode_ms:rate5m
        expr: 1000 * rate(llamacpp:tokens_predicted_seconds_total[5m]) / rate(llamacpp:tokens_predicted_total[5m])
      - record: agmind:llama_kv_usage:max5m
        expr: max_over_time(llamacpp:kv_cache_usage_ratio[5m])
```

**Retention** для домашнего сервера: 15d (`--storage.tsdb.retention.time=15d`) — при scrape interval 15s и ~12 метрик × 3 llama инстанса + node_exporter + GPU ≈ **2-3 GB/неделя**. 30d вполне реалистично (<15 GB).

## 3. Готовый Grafana dashboard

Реальных, поддерживаемых для llama.cpp dashboards немного. **Импортируемых из grafana.com нет** (поиск по grafana.com/dashboards для "llama.cpp" возвращает только косвенные). Рабочие источники:

- **`art-vish/llamacpp-llm-observer`** — JSON в `grafana/dashboards/llama-cpp-overview.json` + system metrics. Полный stack включая alerting rules. **Рекомендую как старт.**
- **`flox/llamacpp-monitoring`** — Nix-based, 8-панельный dashboard (decode_tps, prompt_tps, requests_processing, requests_deferred, n_decode_total rate, n_busy_slots_per_decode), single-instance, JSON живёт в Nix store — нужно экстрагировать.
- Можно использовать vLLM-dashboard ID **18674** (grafana.com) и переписать PromQL под `llamacpp:*` имена — структура панелей применима.

**Что обязательно на dashboard**:
- tokens/sec time series (decode + prompt отдельно)
- requests_processing / requests_deferred area chart (видно очереди)
- KV cache fill % gauge
- per-model breakdown через variable `$model` (`label_values(llamacpp:tokens_predicted_total, model)`)
- per-instance dropdown `$instance`

## 4. Strix Halo GPU exporter — **проблема**

**Это самая болезненная точка.** Ситуация на май 2026:

- **`amd/amd_smi_exporter`** — официальный, но **не поддерживает gfx1151** (только MI200/MI300/EPYC). Архивируется.
- **`ROCm/device-metrics-exporter`** — преемник, тоже **datacenter-only** (MI200/MI300/MI325X). Возможно работает частично, но не заявлено.
- **`amd-smi` на Strix Halo показывает ВСЕ N/A** — ROCm/ROCm issue #6035: amdsmi library не имеет поддержки APU monitoring interfaces для gfx1151. **Kernel exposes data через sysfs/hwmon, user-space tools blind.**
- **`rudimk/rocm-smi-exporter`** — на rocm-smi, та же проблема.

**Прагматичный workaround** для gfx1151:
1. **node_exporter с textfile collector** + кастомный bash script читает `/sys/class/drm/card*/device/hwmon/hwmon*/` (`temp1_input`, `power1_average`, `freq1_input`) и `/sys/class/drm/card*/device/mem_info_vram_used`, `mem_info_gtt_used` → пишет в `.prom` файл каждые 15 сек.
2. Альтернативно — собирать output `radeontop -d -` через wrapper.
3. ROCm 7.2+ начал постепенно фиксить amdsmi для gfx1151 (TinyComputers.io guide), стоит мониторить релизы device-metrics-exporter.

**Compose для node_exporter с textfile**:
```yaml
node_exporter:
  image: prom/node-exporter:v1.8.2
  command:
    - --path.rootfs=/host
    - --collector.textfile.directory=/var/lib/node_exporter/textfile
  volumes:
    - /:/host:ro,rslave
    - /var/lib/node_exporter/textfile:/var/lib/node_exporter/textfile:ro
  pid: host
  network_mode: host
```

Готового dashboard под gfx1151 **не существует** — придётся собрать ~6 панелей самостоятельно (temp, VRAM/GTT used, power, sclk/mclk, fan, utilization).

## 5. structlog для agmind/

Минимальная замена `agmind/log.py` под docker stdout + bind context:

```python
# agmind/log.py
import logging, sys, structlog

def configure(level: str = "INFO", json_output: bool = True) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    renderer = (structlog.processors.JSONRenderer() if json_output
                else structlog.dev.ConsoleRenderer(colors=True))
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,  # stdlib-логи (httpx, urllib3) через тот же pipeline
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

Bind контекста через `contextvars` (thread/asyncio-safe):
```python
from structlog.contextvars import bind_contextvars, clear_contextvars
bind_contextvars(trace_id=trace_id, request_id=req_id, model=model_name)
```

Все внешние библиотеки (urllib, httpx) автоматом проходят через `foreign_pre_chain` и получают timestamp/level. stdout → Alloy → Loki.

**Performance**: structlog ~3-5x медленнее голого stdlib (~10-15 μs/log vs 3-5 μs), но при наших volume (<1000 logs/sec суммарно) это шум. JSONRenderer самый дорогой шаг — критичные hot-paths оборачивать в `logger.isEnabledFor()`.

## 6. OpenTelemetry для Python

**Auto-instrumentation для urllib работает** для синхронных HTTP, **но SSE streaming — частичный случай**: span закрывается на `response.__exit__`, чтение streaming chunks внутри span работает, но `time_to_first_token` нужно мерять руками через `span.add_event("first_token")`. `traceparent` header **автоматически прокидывается** auto-инструментацией.

**llama.cpp сам OTel не поддерживает** (server-side traces нет) — это означает: trace context до llama-server долетает в header'е, но llama-server его игнорирует. Считай его black box endpoint.

**Sampling**: head-based 10% probabilistic на стороне приложения + **always-sample errors/slow** на collector через tail-sampling processor. Для LLM рекомендуется именно tail-sampling (решение принимается после завершения trace, можно по длительности/ошибке отсеять).

**Минимальный пример**:
```python
from opentelemetry import trace
tracer = trace.get_tracer("agmind.llm")

with tracer.start_as_current_span("llm.chat") as span:
    span.set_attribute("llm.model", model_name)
    span.set_attribute("llm.prompt_tokens", n_prompt)
    span.set_attribute("llm.temperature", temp)
    for i, chunk in enumerate(client.stream(...)):
        if i == 0:
            span.add_event("first_token")
        yield chunk
    span.set_attribute("llm.completion_tokens", n_completion)
```

**OTel Collector LGTM конфиг**:
```yaml
receivers:
  otlp: {protocols: {grpc: {endpoint: 0.0.0.0:4317}, http: {endpoint: 0.0.0.0:4318}}}
processors:
  batch: {}
  tail_sampling:
    decision_wait: 30s
    policies:
      - {name: errors, type: status_code, status_code: {status_codes: [ERROR]}}
      - {name: slow, type: latency, latency: {threshold_ms: 5000}}
      - {name: sample-10pct, type: probabilistic, probabilistic: {sampling_percentage: 10}}
exporters:
  otlphttp/tempo: {endpoint: http://tempo:4318}
  prometheusremotewrite: {endpoint: http://prometheus:9090/api/v1/write}
  loki: {endpoint: http://loki:3100/loki/api/v1/push}
service:
  pipelines:
    traces: {receivers: [otlp], processors: [tail_sampling, batch], exporters: [otlphttp/tempo]}
```

`openllmetry` (traceloop) — overkill для нашего случая, добавляет vendor-specific spans; голый OTel SDK достаточно.

## 7. Loki + Alloy

Конфиг `config.alloy` (HCL-like):
```hcl
discovery.docker "containers" {
  host = "unix:///var/run/docker.sock"
}

discovery.relabel "containers" {
  targets = discovery.docker.containers.targets
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "container"
  }
  rule {
    source_labels = ["__meta_docker_container_label_com_docker_compose_service"]
    target_label  = "service"
  }
}

loki.source.docker "containers" {
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.containers.output
  labels     = {job = "docker", stack = "agmind"}
  forward_to = [loki.write.local.receiver]
}

loki.write "local" {
  endpoint { url = "http://loki:3100/loki/api/v1/push" }
}
```

**Label best practices**: `{job, stack, service, container}` — НЕ класть высоко-кардинальные поля (request_id, user_id) в labels Loki, они идут в JSON-тело (Loki structured metadata в v3.x).

**Объём**: 32 сервиса × ~50 log lines/sec avg × 200 bytes ≈ **27 GB/сутки сырых**, после Loki compression ~3-5 GB/сутки. С retention 14d — **~50-70 GB disk**.

## 8. Alertmanager → Telegram

**`metalmatze/alertmanager-bot` устарел (архив 2022)**. Используем **нативный `telegram_configs`** Alertmanager v0.24+ (наш v0.32.1 — поддерживает):

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m
route:
  receiver: tg-default
  group_by: [alertname, severity]
  group_wait: 30s
  repeat_interval: 4h
  routes:
    - matchers: [severity="critical"]
      receiver: tg-critical
      repeat_interval: 30m
receivers:
  - name: tg-default
    telegram_configs:
      - bot_token_file: /run/secrets/tg_bot_token
        chat_id: -1001234567890
        parse_mode: HTML
        message: |
          <b>[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}</b>
          {{ range .Alerts }}
          • {{ .Labels.instance }} — {{ .Annotations.summary }}
          {{ end }}
  - name: tg-critical
    telegram_configs:
      - bot_token_file: /run/secrets/tg_bot_token
        chat_id: -1001234567890
        parse_mode: HTML
        disable_notifications: false
```

Token через docker secrets / `agmind/secrets.py` → mounted file.

**Обязательные алерты** (rules):
```yaml
- alert: LlamaServerDown
  expr: up{job=~"llama_.*"} == 0
  for: 2m
  labels: {severity: critical}
- alert: GpuTempHigh
  expr: amdgpu_temp_edge_celsius > 85
  for: 1m
  labels: {severity: critical}
- alert: GttUsageHigh
  expr: amdgpu_gtt_used_bytes / amdgpu_gtt_total_bytes > 0.90
  for: 5m
  labels: {severity: warning}
- alert: KvCacheNearFull
  expr: llamacpp:kv_cache_usage_ratio > 0.95
  for: 5m
- alert: ContainerRestartLoop
  expr: rate(container_start_time_seconds[10m]) > 0.1
  for: 10m
- alert: LlamaQueueBuildup
  expr: llamacpp:requests_deferred > 5
  for: 5m
- alert: OomKill
  expr: increase(node_vmstat_oom_kill[5m]) > 0
  labels: {severity: critical}
```

## 9. Сборка стека

Структура для `docker compose --profile observability up`:
```
ops/observability/
├── docker-compose.yml          # profile: observability
├── prometheus/
│   ├── prometheus.yml
│   └── rules/*.yml
├── alertmanager/
│   └── alertmanager.yml
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/{prometheus,loki,tempo}.yml
│   │   ├── dashboards/dashboards.yml   # file provider, pointing to /var/lib/grafana/dashboards
│   │   └── alerting/                   # Grafana-managed alerts (optional, prefer Prom rules)
│   └── dashboards/
│       ├── llama-overview.json
│       ├── amdgpu-strix-halo.json
│       └── agmind-app.json
├── alloy/
│   └── config.alloy
└── otel-collector/
    └── config.yaml
```

Provisioning через **файлы** (не UI): Grafana подхватывает `/etc/grafana/provisioning/dashboards/*.yml` + JSON в `/var/lib/grafana/dashboards/` — идемпотентно, повторный `up` пересоздаёт настройки из git, ничего не теряется. Datasources аналогично через YAML.

## 10. Реальные production self-hosted примеры

- **`art-vish/llamacpp-llm-observer`** — самый close-fit под нашу задачу: llama.cpp + Prometheus + Grafana + Alertmanager + node_exporter в одном docker-compose. Бери как baseline.
- **`deepaksatna/LLM-Observability-Stack`** — k8s + DCGM (NVIDIA only, но GPU dashboard pattern переносим).
- **glukhov.org** (March 2026) — обзор по vLLM/TGI/llama.cpp, PromQL queries и threshold рекомендации (p95 lat > X, KV > 90% / 15m, error >1% / 5m).
- **DEV.to / akshaygore** — самописный Ollama exporter (полезно если перейдёте на Ollama, у которого нативного `/metrics` нет до сих пор).
- **`cmakkaya` Medium** — Ollama+n8n+Grafana ToolServer (Observability 3.0, AI-powered APM) — экзотика, но видно куда движется индустрия.

---

## Краткие итоги / риски

1. **Главный pain point — GPU метрики Strix Halo**: ни один официальный exporter не работает на gfx1151. Придётся писать textfile-collector скрипт поверх sysfs/hwmon. Запланировать R-recon задачу.
2. **llama.cpp метрики бедные**: нет TTFT histograms, нет per-model labels, router mode сломан. Histogram'ы TTFT придётся строить **в приложении** через OTel span events.
3. **`alertmanager-bot` НЕ использовать** — используем нативный `telegram_configs` (наша версия 0.32.1 поддерживает).
4. **Dashboard готовый есть только у `art-vish`** — рекомендую форкнуть и адаптировать под наши 3 инстанса.
5. Retention math: Prometheus 15d ≈ 5-10 GB, Loki 14d ≈ 50-70 GB — закладывать в disk budget Strix Halo сервера.

## Sources
- [llama.cpp /metrics issue #19811 (naming, router mode)](https://github.com/ggml-org/llama.cpp/issues/19811)
- [llama.cpp /metrics endpoint issue #5850](https://github.com/ggml-org/llama.cpp/issues/5850)
- [llama.cpp router mode metrics discussion #19197](https://github.com/ggml-org/llama.cpp/discussions/19197)
- [llama-server /metrics discussion #10325](https://github.com/ggml-org/llama.cpp/discussions/10325)
- [llama-server(1) Debian manpage](https://manpages.debian.org/testing/llama.cpp-tools/llama-server.1.en.html)
- [Monitor LLM Inference in Production (2026) — glukhov.org](https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/)
- [art-vish/llamacpp-llm-observer (full stack)](https://github.com/art-vish/llamacpp-llm-observer)
- [flox/llamacpp-monitoring (Nix-based)](https://github.com/flox/llamacpp-monitoring)
- [Monitoring Self-Hosted LLM with Prometheus and Grafana — dev.to](https://dev.to/akshaygore/monitoring-self-hosted-llm-with-prometheus-and-grafana-28dn)
- [LLM Observability Stack (k8s, DCGM)](https://github.com/deepaksatna/LLM-Observability-Stack)
- [AMD SMI Exporter (datacenter only)](https://github.com/amd/amd_smi_exporter)
- [ROCm/device-metrics-exporter (datacenter only)](https://github.com/ROCm/device-metrics-exporter)
- [rudimk/rocm-smi-exporter (third-party)](https://github.com/rudimk/rocm-smi-exporter)
- [amd-smi all N/A on Strix Halo gfx1151 — ROCm issue #6035](https://github.com/ROCm/ROCm/issues/6035)
- [Upgrading ROCm 7.0 to 7.2 on Strix Halo](https://tinycomputers.io/posts/upgrading-rocm-7.0-to-7.2-on-amd-strix-halo-gfx1151.html)
- [AMD ROCm Setup for Local LLMs (2026)](https://localaimaster.com/blog/amd-rocm-local-llm-setup)
- [structlog stdlib integration (foreign_pre_chain)](https://www.structlog.org/en/17.2.0/standard-library.html)
- [Dash0 guide: Python logs with structlog](https://www.dash0.com/guides/python-logging-with-structlog)
- [django-structlog getting started (ProcessorFormatter pattern)](https://django-structlog.readthedocs.io/en/latest/getting_started.html)
- [OpenTelemetry tail sampling guide](https://opentelemetry.io/blog/2022/tail-sampling/)
- [OTel tail sampling processor (collector-contrib)](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor)
- [Distributed tracing for agentic workflows — Red Hat](https://developers.redhat.com/articles/2026/04/06/distributed-tracing-agentic-workflows-opentelemetry)
- [traceloop/openllmetry (alternative)](https://github.com/traceloop/openllmetry)
- [Grafana Alloy loki.source.docker docs](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.docker/)
- [Use Grafana Alloy to send logs to Loki — tutorial](https://grafana.com/docs/alloy/latest/tutorials/send-logs-to-loki/)
- [Alertmanager telegram_configs (Prometheus docs)](https://prometheus.io/docs/alerting/latest/configuration/#telegram_config)
- [metalmatze/alertmanager-bot (DEPRECATED — для справки)](https://github.com/metalmatze/alertmanager-bot)
