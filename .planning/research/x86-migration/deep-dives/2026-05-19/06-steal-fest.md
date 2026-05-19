# Steal-fest: глубокий ресёрч self-hosted AI стеков 2025-2026

> Контекст: AGmindx86 — single-node AMD Strix Halo (gfx1151), Python 3.12, Ansible, Docker Compose, Prom/Graf/Loki, в планах Traefik v3, OTel, pydantic v2 service descriptors, setuptools entry-points для backend plugins.

---

## 1. Подробное сравнение конкурентов

### 1.1 av/harbor (ближайший конкурент)

**Метрики**: 2.9k stars, v0.4.18 (16.05.2026), 200 forks. Стек: TypeScript (36.7%) + Python (21.2%) + Bash (16.4%) + Deno (deno.json) + Poetry. Wiki "Adding A New Service" — самый подробный мануал у конкурентов.

**Архитектура добавления сервиса** (см. harbor wiki §7):
- `services/compose.<handle>.yml` — основной compose
- `services/compose.<handle>.nvidia.yml` — GPU override
- `services/compose.x.<a>.<b>.yml` — cross-service composition (загружается только когда оба активны)
- `services/<handle>/override.env` + `configs/`
- Регистрация в `app/src/serviceMetadata.tsx` с тегами `frontend|backend|satellite`
- Container name pattern: `${HARBOR_CONTAINER_PREFIX}.<handle>`

**CLI vs UI**: CLI primary (`harbor.sh` — гигантский bash), GUI вторична. **Profile management** через `harbor profile save/use` — это полноценная альтернатива нашему "env hierarchy".

**Что круче нас**: cross-service compose layering (`compose.x.a.b.yml`) — элегантнее чем наш будущий docker_sd подход; `harbor eject <services>` экспортирует подмножество в standalone compose-файл; **52+ готовых сервисов**.

**Что у них плохо**: bash-монстр `harbor.sh`, нет встроенных Prometheus/Grafana (логи через `docker logs`), нет structured config validation (мы планируем pydantic v2 — мы лучше).

**3 фишки украсть**:
1. **Cross-service compose layering** (`compose.x.<a>.<b>.yml`) — github.com/av/harbor/tree/main/services. Внедрить как генерируемый Ansible-ом overlay поверх docker_sd. ~6 ч.
2. **Profile save/use** через `harbor profile` — для нашего "минимум bash" это значит persisted env+compose snapshots. ~4 ч.
3. **`eject` команда** — экспортировать `agmind eject llamacpp-q4` в standalone compose, чтобы пользователь мог унести инстанс на другую машину. ~3 ч.

### 1.2 mudler/LocalAI

**Метрики**: 46.4k stars, v4.2.6 (16.05.2026). Стек: **Go (67%)** + JS + Python (6.7%). У них уже **OCI-based backend gallery** — backends живут в registry, не вкомпилены.

**Загрузка моделей** — самая богатая семантика среди всех:
- `local-ai run llama-3.2-1b:q4_k_m` (gallery)
- `huggingface://...`, `ollama://...`, `oci://...`
- 36+ backends, auto-detect GPU
- backend.proto gRPC контракт между core и backend (см. `backend/backend.proto`)

**Observability**: нет prom-endpoint в README, но есть "usage metrics per user".

**Что круче нас**: **URI-схема моделей** (`hf://`, `ollama://`, `oci://`) — zero-friction. Динамический backend pull через OCI — backends живут в гитхаб-контейнер-реджистри.

**Что у них плохо**: Go (мы согласились не идти в Go); монорепо требует rebuild при добавлении backend; gRPC контракт усложняет тестирование.

**3 фишки украсть**:
1. **URI-схема для моделей** — `agmind run hf://Qwen/Qwen3-Coder:Q4_K_M`. Гораздо лучше нашего service-descriptor подхода для разовых запусков. ~8 ч на парсер + resolver.
2. **OCI-distributed backend artifacts** — наши backend plugins (entry-points) можно публиковать как OCI artifacts через `oras`, чтобы single-dev мог `agmind backend pull oci://ghcr.io/me/agmind-vllm-gfx1151`. ~12 ч.
3. **`local-ai models list --installed`** semantics — для нашего pydantic ServiceDescriptor добавить state-machine `discovered|installed|running|cached`.

### 1.3 gpustack/gpustack

**Метрики**: 5k+ stars, v2.1.2 (21.04.2026). Стек: **Python 96.4%** — наш язык. Поддержка AMD GPU, Ascend NPU, Hygon DCU, Apple Silicon — самый широкий multi-vendor.

**Архитектура** (docs.gpustack.ai/latest/architecture):
- Workers: `Runtime` + `Serving Manager` + `Metric Exporter`
- Server: `Scheduler` + `Controllers` (k8s-style контроллеры)
- AI Gateway via **Higress** (Envoy-fork)
- Distributed inference через **Ray bootstrap**

**Pluggable backends**: vLLM, SGLang, llama-box (наш llama.cpp), MindIE, VoxBox, **custom inference backends**. См. `gpustack/server/inference_backends/`.

**Что круче нас**:
- **Catalog UI** с compatibility checks (downloading→running state machine)
- Worker join: `docker run ... --server-url --token --advertise-address` — zero-touch (наш Ansible на этом ровно)
- **Бесшовный multi-vendor** (AMD/NVIDIA/Apple/Huawei в одном binary)

**Что плохо**: Heavyweight (Ray, Higress) — оверкилл для single-node; их manifest scheduling требует k8s-mindset.

**3 фишки украсть**:
1. **Worker join URL+token pattern** — `agmind worker join --server <url> --token <t>` вместо ansible-inventory. Для 2-3 node cluster это самое то. ~10 ч.
2. **Compatibility check pre-deploy** — pydantic ServiceDescriptor валидирует `requires: gfx1151|vulkan|rocm>=6.4` перед стартом. ~6 ч.
3. **Backend plugin abstract class** (`gpustack/inference_servers/base.py`) — образец для нашего `agmind.backend` entry-point contract: `start()`, `health()`, `metrics()`, `stop()`, `capabilities() -> dict`. ~4 ч.

### 1.4 open-webui/open-webui

**Метрики**: 138k stars, v0.9.5, 16.5k коммитов. Стек: Python (35.5%) + Svelte (32.8%).

**Plugin system — ЛУЧШИЙ в индустрии** (см. docs.openwebui.com/features/extensibility/plugin/):
- **3 типа**: Pipes, Filters, Actions — auto-detect по имени класса
- **Frontmatter** (YAML в docstring): `title`, `author`, `version`, `required_open_webui_version`, **`requirements: pkg1,pkg2`** — auto pip install при первой загрузке (контроль через `PIP_INSTALL_FRONTMATTER_REQUIREMENTS`)
- **Valves/UserValves** — Pydantic BaseModel внутри класса. Type hints → автогенерация GUI (int → numeric, bool → toggle, password type через `json_schema_extra={"input":{"type":"password"}}`)
- Сам плагин — **один .py файл**, загружается через UI или каталог

**Observability**: **OpenTelemetry support out of the box** (traces, metrics, logs) — нативно. 9 vector DBs (Chroma, PGVector, Qdrant, Milvus, Elastic, OpenSearch, Pinecone, S3Vector, Oracle 23ai).

**Что круче нас**: zero-friction plugin authoring (один файл, frontmatter, Pydantic Valves), нативный OTel. Их Valves система — буквально то что мы планировали из pydantic v2 service descriptors, но доведено до production-уровня UI.

**Что плохо**: вытеснили CLI в пользу UI — наш one-dev usecase это терпит, но monorepo (`backend/` + Svelte) тяжёлый.

**3 фишки украсть** (приоритет №1 во всём ресёрче):
1. **Frontmatter в docstring + Pydantic Valves** — наш backend plugin через setuptools entry-point должен **дополнительно** поддерживать "single-file plugin": Python файл с YAML frontmatter и Valves-классом. Это убирает церемонию entry-points для пользовательских скриптов. Origin: `backend/open_webui/utils/plugin.py`. ~16 ч.
2. **`requirements` в frontmatter с auto-install** — для нашего "fast добавить" критично. ~6 ч.
3. **Auto-detect plugin type by class name** (`Pipe`, `Filter`, `Action`) — наш ServiceDescriptor должен иметь зеркальные dispatch-классы (`Backend`, `Gateway`, `Sidecar`). ~4 ч.

### 1.5 BerriAI/litellm

**Метрики**: 47.5k stars, v1.85.0 (май 2026), 21.2k dependent projects, Python 83.8%.

**Prometheus metrics — production-grade reference** (docs.litellm.ai/docs/proxy/prometheus): полный список ниже включает 40+ метрик:
- `litellm_spend_metric`, `litellm_total_tokens_metric`, `litellm_input_tokens_metric`
- `litellm_request_total_latency_metric`, `litellm_llm_api_latency_metric`, `litellm_llm_api_time_to_first_token_metric`, `litellm_overhead_latency_metric`
- `litellm_deployment_state` (0=healthy, 1=partial, 2=outage)
- `litellm_deployment_cooled_down`, `litellm_deployment_successful_fallbacks`
- `litellm_in_flight_requests`
- **Custom labels** через `custom_prometheus_metadata_labels`, **custom tags** с wildcard (`User-Agent: RooCode/*`)
- `prometheus_metrics_config` группы — селективное включение

**Config**: `proxy_server_config.yaml` + env, `model_prices_and_context_window.json`. Provider plugins в `litellm/llms/<provider>/`.

**Testing**: `make install-dev` (uv) → `make format` (Black) → `make lint` (Ruff+MyPy) → `make test-unit`. Циркулярная проверка импортов.

**Что круче нас**: набор метрик — мы должны эмулировать **точно эту схему** с префиксом `agmind_`. TTFT vs total latency vs overhead — разделение, которого у нас сейчас не запланировано.

**3 фишки украсть**:
1. **Metric naming schema** буквально портировать: `agmind_deployment_state`, `agmind_llm_api_ttft_metric`, `agmind_in_flight_requests`. Source: github.com/BerriAI/litellm/blob/main/litellm/integrations/prometheus.py. ~8 ч + Grafana dashboard.
2. **`custom_prometheus_metadata_labels`** — пользователь добавляет per-service labels через YAML, не код. Bash-минимизация. ~4 ч.
3. **Make-target архитектура** (`make test-unit`, `make lint`, `make format`) — у нас уже есть DoD через `make`, но добавить `make audit` совмещённый с `mypy --strict + ruff + circular-import detector`. ~3 ч.

### 1.6 vllm-project/production-stack

**Метрики**: 2.3k stars, k8s-only reference. Helm + Prom/Grafana.

**Ценное**: **Grafana dashboard reference** (helm/observability/) — TTFT distribution, GPU KV cache usage, prefix-aware routing metrics. **LMCache integration** для KV offloading.

**Что круче нас**: их Grafana панели (`helm/observability/grafana-dashboard.json`) — готовая VLLM dashboard, мы можем взять её JSON напрямую и подкрутить metric names.

**Что плохо**: k8s-only, бесполезно для single-node.

**3 фишки украсть**:
1. **Grafana JSON dashboard** — портировать панели "Time-to-First-Token Distribution", "KV cache hit rate", "Queue depth" в `agmind/observability/grafana/`. ~4 ч.
2. **Session-ID-based routing** на нашем будущем Traefik v3 — labels `traefik.http.middlewares.session-sticky.plugin.sticky.cookie=...` для KV cache reuse. ~6 ч.
3. **LMCache mention** — но: см. §3, для single-node это anti-pattern.

### 1.7 langgenius/dify

**Метрики**: 142k stars, v1.14.2. Plugin system по их docs — provider plugins в YAML.

**Observability**: **Opik + Langfuse + Arize Phoenix** интегрируется через config, плюс Grafana с PostgreSQL datasource — это паттерн "Grafana on Postgres metrics" не Prometheus, что нам не подходит.

**3 фишки украсть**: workflow DAG editor — anti-pattern для single-dev (см §3), skip.

### 1.8 lobehub/lobe-chat

**Метрики**: 77.3k stars, v2.2.0. Plugin SDK: `@lobehub/chat-plugin-sdk`, плагин-индекс через `index.json`. **MCP-compatible plugins** — стандарт Anthropic.

**3 фишки украсть**:
1. **MCP-совместимость** для наших Tools — single-dev может подключать любой MCP-сервер. ~10 ч (см §2 MCP).

### 1.9 Mintplex-Labs/anything-llm

**Метрики**: 60.3k stars, v1.12.1, 98.4% JavaScript. RAG-focused, no-code agents, PostHog telemetry.

Для нас неинтересно — Node.js монорепо, не Python.

### 1.10 bentoml/OpenLLM

**Метрики**: 12.3k stars, v0.6.30 (апрель 2025). Использует **uv** для deps — подтверждение нашего выбора. Модели через `bentos` репозитории — это **anti-pattern** для нас (см §5).

### 1.11-1.14 coolify / dokploy / immich / paperless-ngx — DX patterns

**coolify** (55.6k stars, PHP/Blade): templates под `/templates`, 280+ one-click services. **Skip** — PHP не наш язык, паттернов мало.

**dokploy** (34.1k stars, TS): **Traefik native integration** подтверждает наш план. Multi-node через Docker Swarm. **Notification system** (Slack/Discord/Telegram/email) — украсть.

**immich** (TS/NestJS + Python ML 1.6%): мало деталей в README, но их `/deployment/docker/` структура — образец lock-step compose+env+chart.

**paperless-ngx** (Python+Django): `.mypy-baseline.txt` + `.codecov.yml` + Hadolint + Prettier + pre-commit — full stack DX. **Украсть `mypy-baseline`** — позволяет инкрементально вводить strict typing без переписи всего.

**3 фишки украсть из всей группы**:
1. **mypy-baseline** (paperless-ngx/.mypy-baseline.txt) для постепенного включения strict. ~2 ч.
2. **Notification multiplexer** (dokploy) — `agmind notify` через Slack/TG/Discord/email из одного pydantic NotificationDescriptor. ~6 ч.
3. **Hadolint в pre-commit** (paperless-ngx) — мы пишем Docker compose, надо валидировать Dockerfile если будем строить. ~1 ч.

---

## 2. Инновации 2025-2026 — что мы можем пропустить

### Astral uv (текущая 0.11.14, май 2026)
**Факт**: workspace mode (single lockfile для multi-package monorepo), dependency groups (`--dev`, `--group`, `--optional`), conflicting extras декларация. **Рекомендация**: переехать с pip на uv для **single lockfile across all services**. Эстимейт: 6 ч.

### Python 3.14 (free-threading PEP 779) + 3.15 (PEP 810 lazy imports + JIT)
**Факт**: free-threading officially supported в 3.14 (2-4x на 4-core CPU-bound). PEP 810 даёт 30-40% memory reduction, 50-70% startup time reduction. JIT расширен в 3.15. **Рекомендация**: целиться на 3.14 для AGmind (наш CLI startup сэкономит секунды), free-threading **не критично** для нашего use case (GPU bound). Эстимейт миграции 3.12→3.14: 4 ч.

### Pydantic v2.10+
**Факт**: TypeAdapter, partial validation, JSON schema 2020-12. **Рекомендация**: использовать `TypeAdapter` для service descriptor валидации (быстрее чем full model). Эстимейт: уже планировалось.

### OpenTelemetry GenAI Semantic Conventions (стабилизированы начало 2026, многие attrs ещё experimental на март 2026)
**Факт**: стандартные атрибуты `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`. **Рекомендация**: **обязательно использовать** — это даёт интероп с любым APM (Langfuse, Phoenix, Aspire Dashboard, Grafana Tempo). Наши traces будут совместимы. Эстимейт: 8 ч.

### Mojo / Codon / Pyrefly
**Не нашёл** доказательств production-зрелости для self-hosted AI стеков 2026. **Skip**.

### Dagger.io 2025
**Не нашёл** массового usage в self-hosted AI (LocalAI/Harbor/GPUStack используют обычный CI). **Skip** для AGmindx86.

### Pixi (prefix-dev)
**Факт**: conda+poetry hybrid, multi-environment с CUDA/MLX/CPU вариантами автоматически (`pyproject.toml` system-requirements table). **Проблема**: ROCm transitive deps конфликтуют (issue #4561 в prefix-dev/pixi). **Рекомендация**: для нашего ROCm/Vulkan стека uv стабильнее. **Skip Pixi**.

### Inference servers 2026
**Факт**: vLLM на gfx1151 нестабилен; SGLang выигрывает на prefix-heavy (16k tok/s vs vLLM 12k); **TGI ушёл в maintenance mode** (HF официально рекомендует vLLM/SGLang/llama.cpp); **llama.cpp** остаётся королём consumer hardware. **Modular MAX** — нет данных о Strix Halo support. **Рекомендация**: основная ставка llama.cpp; добавить SGLang backend для RAG/multi-turn (если ROCm на gfx1151 заработает). Эстимейт SGLang plugin: 12 ч + риск.

### MCP (Model Context Protocol)
**Факт**: 2026 roadmap включает stateless Streamable HTTP, session resume, OAuth 2.1, MCP Gateways. Stdio для local, HTTP для production. **Рекомендация**: AGmind должен экспозить MCP-сервер для tools (наши сервисы как MCP resources). Эстимейт: 16 ч.

### LMCache / vLLM PD disaggregation
**Факт**: PD disaggregation требует prefiller GPU + decoder GPU отдельно через NIXL/NVLink/RDMA. Mooncake Store интегрирован в vLLM v1 (май 2026). **Рекомендация для нас**: **anti-pattern** для single-node Strix Halo (один GPU, unified memory). См §5.

### Langfuse / Phoenix / Opik (LangSmith self-hosted альтернативы)
**Факт**: Langfuse лидирует (MIT, OTel-native, ClickHouse storage, ~$50-80/mo selfhost). Phoenix — для evaluation/experimentation (Elastic License 2.0). Opik — Apache 2.0, AI prompt optimization, мгновенная скорость логирования. **Рекомендация**: Langfuse как опциональный backend для GenAI traces в AGmindx86 (через OTel GenAI semconv мы получаем интероп бесплатно). Эстимейт: 4 ч на docker-compose интеграцию.

### GitHub Spec Kit / Cookiecutter
**Не нашёл** ничего нового для AI-стеков. **Skip**.

---

## 3. Vector DB / Embeddings / Document parsing (для будущего RAG)

**Vector DB consensus 2026**: pgvector для 1-50M векторов (best operational simplicity); **Qdrant** для latency-sensitive (~2.1 ms p50 на 1M); Milvus только 100M+. **Рекомендация для AGmindx86**: **pgvector** (Postgres у нас уже стоит для всего остального) — 2.1ms на 1M это лишний компонент.

**Document parsing 2026**: **Docling (IBM Research)** — open-source, AI layout detection, best balance; **LlamaParse** — самый быстрый (~6s) но платный; **Unstructured** — версатильный но медленный (51s/page). **Marker-PDF** — для self-hosted альтернатива.

**Embeddings**: BGE-m3 и Nomic лидируют open-source. **Рекомендация**: BGE-m3 локально через llama.cpp.

---

## 4. Anti-patterns (Patterns "которые мы должны явно отвергнуть")

1. **Микросервисы для single-node AI** — Dify/Open-WebUI делают это от scale-out, мы single-dev. Один Python process + threading + entry-points >>> 5 контейнеров с gRPC.
2. **k8s/Helm** — production-stack/gpustack, оверкилл для 1-3 нод. Docker Compose + Ansible достаточно.
3. **gRPC между сервисами** (LocalAI стиль) — увеличивает церемонию тестирования. **REST + Pydantic schemas + OpenAPI** проще, плюс мы можем легко моки делать в pytest.
4. **PD disaggregation / LMCache на single GPU** — это для clusters с разными GPU types. У нас unified memory, нечего разделять.
5. **Visual workflow builder** (Dify, n8n стиль) — не нужно single-dev'у. Python-as-config (наши setuptools entry-points) мощнее.
6. **Modeled-as-OCI artifacts** (LocalAI/OpenLLM) — переусложнение для домашнего сетапа. HuggingFace direct + local cache достаточно.
7. **Go в нашем стеке** (LocalAI/Coolify) — мы уже договорились не идти в Go.

---

## 5. Что АБСОЛЮТНО НИКТО не делает хорошо в 2026

1. **AMD Strix Halo / gfx1151 first-class support** — ни один из 14 проанализированных проектов не имеет нативной поддержки. Все требуют ручного `HSA_OVERRIDE_GFX_VERSION=11.5.1`, `HSA_ENABLE_SDMA=0`. **Возможность**: AGmind может стать **the reference stack for Strix Halo**.
2. **Unified memory budgeting** — никто не умеет грамотно делить 128 GB между CPU/GPU/cache. UMA Frame Buffer = 512MB + динамическое allocation остаётся ручным.
3. **Vulkan vs ROCm dual-track** — kyuz0/amd-strix-halo-toolboxes тестирует только Vulkan/ROCm отдельно, но никто не делает auto-pick ("используй Vulkan на коротком контексте, ROCm на 32K+").
4. **AMDVLK silently overrides RADV** — критический баг производительности без error message (см. hogeheer499 guide). Никто не валидирует это в health checks.
5. **Power profile management** — `tuned vs power-profiles-daemon` конфликт ломает benchmark consistency. Никто не автоматизирует `tuned-adm profile accelerator-performance`.
6. **GPU clock stuck at 900MHz detection** — нужен monitoring через `/sys/class/drm/card*/device/pp_dpm_sclk`. Никто не алёртит.
7. **Frontmatter-driven single-file plugins для backend** — Open WebUI сделал для функций чата, но **никто не сделал для inference backends**. Это наш дифференциатор.

---

## 6. Production case studies (Strix Halo)

- **hogeheer499-commits/strix-halo-guide** — Beelink GTR9 Pro, 128 GB. Реальные числа: Qwen3-Coder 30B Q4_K_S → 98.5 t/s Vulkan; gpt-oss-120b MXFP4 → 55.6 t/s. Перечислены конкретные граблями: AMDVLK silently overrides RADV; firmware 20251125 ломает ROCm.
- **kyuz0/amd-strix-halo-toolboxes** — containerized backend matrix (Vulkan AMDVLK, RADV, ROCm 6.4.4, 7.2.3, nightlies). Rebuild containers on llama.cpp master.
- **Framework Community thread** — Ryzen AI Max+ 395 + Framework Desktop case studies.
- **AMD Developer Resources 2026** — trillion-parameter LLM на AMD Ryzen AI Max+ кластере (multi-node Strix Halo).
- **Level1Techs forum** — Strix Halo LLM benchmark threads, активное сообщество.

---

## 7. Top-10 patterns "украсть прямо сейчас" (ранжированы)

| # | Pattern | Source | Что даёт | Эстимейт |
|---|---------|--------|----------|----------|
| 1 | **Frontmatter+Valves single-file plugin** | Open WebUI `backend/open_webui/utils/plugin.py` | Zero-friction добавление backend без entry-point церемонии | 16 ч |
| 2 | **Cross-service compose layering** `compose.x.a.b.yml` | av/harbor `services/` | Декларативные dependencies без custom code | 6 ч |
| 3 | **OTel GenAI semantic conventions** (`gen_ai.*` attrs) | opentelemetry.io/docs/specs/semconv/gen-ai/ | Free interop с Langfuse/Phoenix/Tempo | 8 ч |
| 4 | **LiteLLM Prometheus metric naming schema** | litellm/integrations/prometheus.py | Production-grade reference (TTFT, overhead, deployment_state) | 8 ч |
| 5 | **URI model schema** `hf://`, `ollama://`, `oci://` | mudler/LocalAI | One-liner для run/install моделей | 8 ч |
| 6 | **Worker join URL+token** | gpustack worker pattern | Простая 2-3 node cluster без ansible-inventory | 10 ч |
| 7 | **mypy-baseline** для инкрементального strict typing | paperless-ngx/.mypy-baseline.txt | Постепенное включение без rewrite | 2 ч |
| 8 | **Profile save/use** persisted env+compose snapshot | harbor profile | "Минимум bash" + reproducibility | 4 ч |
| 9 | **MCP server exposure** для tools | modelcontextprotocol.io 2026 roadmap | Интероп с любым LLM client | 16 ч |
| 10 | **Backend abstract class** `start/health/metrics/stop/capabilities` | gpustack/inference_servers/base.py | Унифицированный контракт для plugins | 4 ч |

**Итого**: ~82 ч на топ-10. Приоритет 1-4 — must-have для DoD первой production-ready версии (~38 ч).

---

## Sources

**Конкуренты (репозитории)**:
- [av/harbor](https://github.com/av/harbor), [adding new service wiki](https://github.com/av/harbor/wiki/7.-Adding-A-New-Service)
- [mudler/LocalAI](https://github.com/mudler/LocalAI)
- [gpustack/gpustack](https://github.com/gpustack/gpustack), [architecture docs](https://docs.gpustack.ai/latest/architecture/)
- [open-webui/open-webui](https://github.com/open-webui/open-webui)
- [bentoml/OpenLLM](https://github.com/bentoml/OpenLLM)
- [vllm-project/production-stack](https://github.com/vllm-project/production-stack)
- [langgenius/dify](https://github.com/langgenius/dify)
- [BerriAI/litellm](https://github.com/BerriAI/litellm), [Prometheus metrics](https://docs.litellm.ai/docs/proxy/prometheus)
- [coollabsio/coolify](https://github.com/coollabsio/coolify), [Dokploy/dokploy](https://github.com/Dokploy/dokploy)
- [immich-app/immich](https://github.com/immich-app/immich), [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)
- [lobehub/lobe-chat](https://github.com/lobehub/lobe-chat), [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm)

**Open WebUI plugin docs**:
- [Pipe Function](https://docs.openwebui.com/features/extensibility/plugin/functions/pipe/)
- [Valves](https://docs.openwebui.com/features/extensibility/plugin/development/valves/)
- [Functions overview](https://docs.openwebui.com/features/extensibility/plugin/functions/)

**Python ecosystem 2026**:
- [PEP 810 Lazy Imports](https://peps.python.org/pep-0810/)
- [Python 3.14 free-threading PEP 779](https://blog.imseankim.com/python-3-14-free-threading-jit-compiler-gil-removal-2026/)
- [uv workspace docs](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [Pixi pytorch](http://pixi.prefix.dev/latest/python/pytorch/)

**Observability / OTel GenAI**:
- [OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [OTel GenAI blog 2026](https://opentelemetry.io/blog/2026/genai-observability/)
- [Langfuse vs Phoenix comparison](https://www.zenml.io/blog/langfuse-vs-phoenix)
- [LLM observability 2026 Spheron guide](https://www.spheron.network/blog/llm-observability-gpu-cloud-langfuse-arize-phoenix-helicone/)

**Inference servers / KV cache**:
- [vLLM/SGLang/llama.cpp 2026 comparison](https://buttondown.com/ultradune/archive/eval-001-the-great-llm-inference-engine-showdown/)
- [LMCache disaggregated prefill](https://docs.lmcache.ai/getting_started/quickstart/disaggregated_prefill.html)
- [MCP 2026 roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)

**Strix Halo (production case studies)**:
- [hogeheer499-commits/strix-halo-guide](https://github.com/hogeheer499-commits/strix-halo-guide)
- [kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes)
- [Framework Community Strix Halo thread](https://community.frame.work/t/amd-strix-halo-ryzen-ai-max-395-gpu-llm-performance-tests/72521)
- [Level1Techs Strix Halo benchmarks](https://forum.level1techs.com/t/strix-halo-ryzen-ai-max-395-llm-benchmark-results/233796)
- [Local AI Master Strix Halo guide 2026](https://localaimaster.com/blog/strix-halo-ai-max-395-guide)
- [AMD trillion-parameter cluster](https://www.amd.com/en/developer/resources/technical-articles/2026/how-to-run-a-one-trillion-parameter-llm-locally-an-amd.html)

**Vector DB / RAG 2026**:
- [pgvector vs Qdrant vs Milvus 2026](https://dev.to/linou518/choosing-the-foundation-for-your-rag-system-pgvector-vs-qdrant-vs-milvus-2026-4i5o)
- [Vector DB benchmarks 2026](https://callsphere.ai/blog/vector-database-benchmarks-2026-pgvector-qdrant-weaviate-milvus-lancedb)
- [Document parsing comparison](https://llms.reducto.ai/document-parser-comparison)

**Infra**:
- [Traefik v3 Docker provider](https://doc.traefik.io/traefik/providers/docker/)
- [Prometheus docker-sd](https://github.com/Sqooba/prometheus-docker-labels-discovery)
