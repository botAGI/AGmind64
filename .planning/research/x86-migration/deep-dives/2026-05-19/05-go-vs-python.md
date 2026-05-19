# Ресёрч: Go vs Python для AGmindx86 — конкретные trade-offs

## 1. Кандидаты на Go — пройдёмся по каждому

### A. CLI binary `agmind` — НЕ окупится (для нашего масштаба)

**Цифры.** Holistic-бенчмарки [bdrung/startup-time](https://github.com/bdrung/startup-time) и [chocolateboy/startup-time](https://github.com/chocolateboy/startup-time): Go ~0.88 ms vs Python3 ~22 ms на пустом "hello world". Реальный Typer-CLI с импортами доходит до 100-300 ms из-за дерева зависимостей (Click, pydantic, rich, httpx).

**Но:** [Hugo van Kemenade, 2025](https://hugovk.dev/blog/2025/lazy-imports/) показал 2.9× ускорение Typer-CLI (104 → 36 ms) одними lazy imports. [PEP 810](https://peps.python.org/pep-0810/) (Python 3.15, осень 2025) даёт это нативно через `lazy import` — без переписывания. Реалистично уложиться в 40-60 ms cold-start для `agmind doctor`.

**Вердикт.** Для `agmind doctor`, который делает HTTP-запросы и `docker ps` (это десятки-сотни ms на саму работу), startup даже 200 ms — шум. Переписывание ~600 LOC Python → ~1500-2000 LOC Go ради экономии 150 ms на холодном запуске — отрицательный ROI. Под нагрузкой 1-3 ноды и одиночного оператора `agmind` запускается единицы раз в день.

**Действие.** Оставить Python. Если хочется single-binary distribution — `uv tool install agmind` ([Astral uv docs](https://docs.astral.sh/uv/concepts/tools/)) даёт изолированную установку за миллисекунды, или собрать через PyOxidizer/Nuitka один файл ~30 MB.

### B. Cluster router — НЕ окупится сейчас, может окупиться позже

**Цифры.** Bifrost (Go) vs LiteLLM (Python) на 5000 RPS: P99 1.6-1.7 s vs "десятки секунд" — gap ~50× ([dev.to/hadil](https://dev.to/hadil/litellm-vs-bifrost-comparing-python-and-go-for-production-llm-gateways-4dg5)). На 500 RPS LiteLLM уже спайкует до 4 минут на отдельных запросах.

**Реальность AGmind.** llama-server на Strix Halo при 70B модели выдаёт ~10-30 tok/s = единицы запросов/сек на ноду. 1-3 ноды × 1-3 RPS = 3-10 RPS пиковая нагрузка. Это в **500-1500 раз** ниже точки, где Python async становится узким местом. Overhead Python router'а — единицы ms против latency самого инференса в десятки секунд → 0.01% от end-to-end.

**Вердикт.** Goroutines дадут выигрыш на 100+ RPS sustained. Для домашнего/полу-production сервера — никогда не дойдём. Останется bottleneck на самом llama.cpp.

### C. Service descriptor generator (`services.yaml` → compose) — возможно окупится в DX, но не критично

**Альтернативы Ansible Jinja2.** [`compose-go`](https://pkg.go.dev/github.com/compose-spec/compose-go) — официальная Docker-библиотека, парсит и валидирует Compose-файлы. Можно собрать `agmind-gen --task=rag > docker-compose.yml` за миллисекунды.

**Что выигрываешь.** Тип-сейф (Go struct vs Jinja2 строковая подстановка), валидация Compose-спецификации из коробки, single binary без Python/Ansible на хосте.

**Что теряешь.** Дублирование domain-модели (`Service`, `Backend`, `Task`) между Python и Go. CI на два языка. Жирность мейнтенанса для 1 dev возрастает заметно.

**Вердикт.** **Единственный реалистичный кандидат**, если очень хочется убрать Ansible. Но стоит сначала спросить: 1241 LOC YAML — это пережиток или это явный декларативный контракт, который ценен сам по себе? Скорее второе.

### D. Health-checker daemon — НЕ нужен на Go

[`blackbox_exporter`](https://github.com/prometheus/blackbox_exporter) уже написан на Go, production-ready, делает ровно это. Своё писать — изобретать велосипед. Подключить exporter + Prometheus scrape — 30 строк YAML.

### E. Config preprocessor — НЕ окупится

Ansible role уже делает это надёжно, idempotent, с handlers. Go-binary тут чистая дупликация без выигрыша.

## 2. Где Go объективно НЕ окупится

- `agmind/compute/` — `llama-cpp-python`, `vLLM`, `infinity`, `sentence-transformers`. ML-экосистема Python безальтернативна.
- Тесты — pytest + fixtures + parametrize в Go не воспроизводится без боли.
- Plugin system для backends — `entry_points` через PEP-621. В Go только Go-plugins (`.so`) или RPC, и то и то хуже.

## 3. Что выбрали другие — pattern

- **Ollama** — Go + CGo обёртка над llama.cpp. **Но это model server**, не оркестратор. У AGmind llama-server отдельно — не пересекается ([yuv.ai](https://yuv.ai/blog/self-hosting-llms-with-ollama)).
- **vLLM, LiteLLM** — Python (нужен PyTorch / model clients).
- **Coolify** — Laravel (PHP) + Go services ([cherryservers](https://www.cherryservers.com/blog/coolify-vs-dokploy)).
- **Dokploy** — TypeScript/Node + Docker Swarm.
- **Portainer** — Go backend + Vue. Но это GUI manager, не AI стек.

**Pattern подтверждается:** оркестрация/инфра-CLI → Go или TS, ML/inference → Python. AGmind = "оркестрация + ML" → теоретически split-stack уместен. На практике для 1 dev — overhead двух стеков съест выигрыш.

## 4. Реальные кейсы переписывания (lessons)

- [Medium: "10x faster API rewrite that added 0 value"](https://medium.com/@build_break_learn/python-vs-go-the-10x-faster-api-rewrite-that-added-0-value-46f71c06ec68) — 2 месяца переписывания, 10× throughput, 1/3 memory, итог 7 ms real impact для пользователя. **Negative ROI.**
- [S3CloudHub: "Rewrote backend in Go and regretted instantly"](https://medium.com/@S3CloudHub./i-rewrote-our-backend-in-go-and-regretted-it-instantly-613adb4e7824) — типичная история для команд с малым опытом Go.
- [Telemetry Harbor](https://harborscale.com/blog/from-python-to-go-why-we-rewrote-our-ingest-pipeline-at-telemetry-harbor/) — успех, но условие: реальный bottleneck (FastAPI ingest на тысячах RPS). У AGmind такого нет.

**Общий паттерн:** Go выигрывает, когда (а) уже упёрлись в Python bottleneck в production, (б) есть команда с Go-опытом. Преждевременная миграция почти всегда теряет время.

## 5. Альтернативы Go для "single binary"

| Инструмент | Размер | Startup | Trade-off |
|---|---|---|---|
| **uv tool install** | 0 (изоляция через uv) | ms | Лучший вариант сейчас, нужен только uv |
| **PyOxidizer** | ~30 MB | близко к Python | Заброшен ([readthedocs](https://pyoxidizer.readthedocs.io/)) — не рекомендую |
| **Nuitka** | 20-40 MB | medium, чуть быстрее CPython | Активный, компилит в C |
| **PyInstaller** | 30-50 MB | медленный (распаковка в /tmp) | Простой, но не для "fast CLI" |
| **Shiv (PEX)** | ~требует Python | Python-level | Зависит от системного Python |
| **Go rewrite** | 8-15 MB | <10 ms | Месяцы работы |

**Рекомендация по distribution:** `uv tool install agmind` — реальный конкурент Go-binary'у. Для пакетного менеджера — собрать `.deb` из uv-installed env.

## 6. Migration cost для `agmind-cli` на Go

Грубая оценка для опытного Go-разработчика: ~80-120 часов на CLI с ~10 командами + subprocess вызовы Python backend. Плюс пожизненный налог на синхронизацию типов между Python и Go (cluster Node, Backend, Task, ServiceDescriptor). Для 1 dev — недели потерянного фокуса на core задачи (миграция на Strix Halo, ROCm/Vulkan тесты).

## 7. Рекомендация — прямо

**Не переписывать ничего на Go.** Конкретно:

1. **CLI startup** — решается PEP 810 lazy imports или `uv tool install`, экономия 60-80% без переписывания.
2. **Cluster router** — текущая Python-реализация лежит на ~3 RPS, до 500× headroom. Go не нужен до production-нагрузок, которых не будет на 1-3 нодах Strix Halo.
3. **Ansible/Jinja2** — если действительно болит "тонны YAML", замени на typed Python + pydantic models, рендерящие Compose. **Не Go**. Останется один язык.
4. **Bash скрипты** — если их много, замени их на Python-функции в `agmind/cli/`. Bash → Python даёт типы, тесты, кросс-платформенность. Bash → Go даёт всё то же + цену второго стека.

**Если очень руки чешутся попробовать Go** — единственный безопасный эксперимент: маленький `agmind-gen` (services.yaml → compose) на ~500 LOC Go с `compose-go`. Изолированный, заменяемый, не блокирует основной стек. Если зайдёт — расширишь. Не зайдёт — выбросишь без потерь.

**Bottom line:** Strix Halo migration — где реальная ценность сейчас. Go-rewrite — это yak shaving, который украдёт 2-3 месяца у настоящей задачи.

## Sources
- [Bifrost vs LiteLLM benchmark — dev.to](https://dev.to/hadil/litellm-vs-bifrost-comparing-python-and-go-for-production-llm-gateways-4dg5)
- [Bifrost 50× faster LLM gateway — dev.to](https://dev.to/the_greatbonnie/how-a-go-based-llm-gateway-achieves-extreme-performance-gains-bifrost-vs-litellm-1l3o)
- [Startup time benchmarks — bdrung](https://github.com/bdrung/startup-time)
- [PEP 810 lazy imports — Python.org](https://peps.python.org/pep-0810/)
- [3× faster Python startup via lazy imports — Hugo van Kemenade](https://hugovk.dev/blog/2025/lazy-imports/)
- [uv tool install — Astral docs](https://docs.astral.sh/uv/concepts/tools/)
- [10x faster API rewrite that added 0 value — Medium](https://medium.com/@build_break_learn/python-vs-go-the-10x-faster-api-rewrite-that-added-0-value-46f71c06ec68)
- [Rewrote backend in Go and regretted — Medium](https://medium.com/@S3CloudHub./i-rewrote-our-backend-in-go-and-regretted-it-instantly-613adb4e7824)
- [Telemetry Harbor: Python → Go success](https://harborscale.com/blog/from-python-to-go-why-we-rewrote-our-ingest-pipeline-at-telemetry-harbor/)
- [Ollama architecture — yuv.ai](https://yuv.ai/blog/self-hosting-llms-with-ollama)
- [Coolify vs Dokploy architecture — Cherry Servers](https://www.cherryservers.com/blog/coolify-vs-dokploy)
- [compose-go library — pkg.go.dev](https://pkg.go.dev/github.com/docker/compose/v2)
- [PyOxidizer comparison docs](https://pyoxidizer.readthedocs.io/en/stable/pyoxidizer_comparisons.html)
- [Typer performance discussion — GitHub](https://github.com/fastapi/typer/discussions/744)
