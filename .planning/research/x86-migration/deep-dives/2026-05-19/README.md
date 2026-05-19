# Deep-dive research — 2026-05-19

Пакет глубокого ресерча, проведённый перед началом серьёзного архитектурного рефакторинга AGmindx86 (post-Phase G, pre-Phase H'). Цель — заложить качественный фундамент: гибкость, modularity, безболезненное добавление сервисов, observability.

## Контекст

После завершения Phase A-G миграции (compute abstraction, CLI, cluster, Docker, ADR, audit clean, legacy quarantine удалён) пользователь поставил задачу:

> "Нужно заложить очень серьёзный фундамент чтобы далее было проще намного. Гибкость, модульность, чтобы я мог fast добавить новый сервис и он быстро интегрировался в весь стек — подхватывался метриками, получал нейминг, и т.д. Минимум bash. Качественный debug и тесты."

Запущено 5 параллельных subagent'ов на детальный веб-ресёрч с требованием: конкретные version-pins, working config snippets, Strix Halo проверка, признание "не нашёл" вместо выдумок.

## Отчёты

| # | Тема | Файл | Длительность |
|---|---|---|---|
| 1 | Traefik v3 + llama-server SSE/mTLS/wildcard | [01-traefik-llama-server.md](01-traefik-llama-server.md) | 4.3 мин |
| 2 | Hydra-core profiles for stack-per-task | [02-hydra-profiles.md](02-hydra-profiles.md) | 2.5 мин |
| 3 | OTel + structlog + llama.cpp metrics pipeline | [03-observability-pipeline.md](03-observability-pipeline.md) | 3.5 мин |
| 4 | Zero-friction service onboarding + plugin system | [04-service-onboarding.md](04-service-onboarding.md) | 4.3 мин |
| 5 | Go vs Python — where to rewrite | [05-go-vs-python.md](05-go-vs-python.md) | 2.5 мин |
| 6 | Competitor steal-fest + 2025-2026 innovations | [06-steal-fest.md](06-steal-fest.md) | 6.7 мин |

## Top decisions (synthesis)

1. **НЕ переписывать на Go** — yak shaving, 2-3 мес потерь. PEP 810 lazy imports + `uv tool install` решают CLI startup. Bottleneck не в Python, а в llama.cpp инференсе.
2. **НЕ Hydra сразу** — overkill для 1 axis (task). OmegaConf + pydantic v2 + Hydra-совместимый layout `conf/` сейчас, Hydra при появлении >3 осей композиции.
3. **Service Descriptor as SSoT** — один YAML на сервис (`templates/services/<name>.yaml`) + Pydantic v2 schema + JSON Schema export для VSCode autocomplete + pre-commit `check-jsonschema`.
4. **Plugin system через setuptools entry_points** group `agmind.backends` — устраняет 7-8 мест правки для нового backend.
5. **Auto-discovery через docker labels** + whitelist: Prometheus `docker_sd_configs` + `prometheus.scrape=true`; Alloy `discovery.docker` + `loki.scrape=true`; Traefik Docker provider native.
6. **3 chain-middleware в Traefik file provider** (`chain-llm`, `chain-internal`, `chain-public`) — "tier-by-label" native не работает, file provider — компромисс.

## Critical findings (white spots)

1. **🚨 Strix Halo GPU exporter не существует** — `amd/amd_smi_exporter` и `ROCm/device-metrics-exporter` datacenter-only (gfx1151 не поддержан, ROCm issue #6035). Workaround: textfile collector + bash из `/sys/class/drm/card*/device/hwmon/`. Готового Grafana dashboard нет. **R-recon R13 нужен.**
2. **llama.cpp `/metrics` беден**: нет TTFT histograms, нет per-model labels, имена `llamacpp:tokens_predicted_total` с двоеточиями (нарушает Prometheus convention), router mode сломан (issue ggml-org/llama.cpp#19811). TTFT histograms строим в приложении через OTel `span.add_event("first_token")`. **R-recon R14.**
3. **`*.lan` через Let's Encrypt не работает** — LE не выдаёт серты на non-ICANN TLD. Варианты: купить публичный домен (~$10/год) + Cloudflare DNS-01 wildcard, либо `step-ca` internal CA. **R-recon R15.**

## Implementation roadmap

См. `migration_progress.json::phases.H_prime` для детального плана Phase H' (Foundation refactor) с 12 задачами на 3-5 дней работы.

## Sources groups

- **Traefik / SSE**: doc.traefik.io v3.7, community.traefik.io, GitHub issues #503, #7930, ollama/ollama#13949
- **Hydra / config**: facebookresearch/hydra docs, lightning-hydra-template, MarkTechPost 2025-11
- **llama.cpp metrics**: ggml-org/llama.cpp issues #5850, #19811, discussions #19197, #10325
- **Observability**: art-vish/llamacpp-llm-observer, flox/llamacpp-monitoring, grafana.com/docs/alloy
- **Service onboarding**: coollabsio/coolify templates, docs.dokploy.com, backstage.io
- **Strix Halo GPU**: ROCm/ROCm#6035, tinycomputers.io, ROCm/device-metrics-exporter

См. подробные ссылки в каждом отчёте.
