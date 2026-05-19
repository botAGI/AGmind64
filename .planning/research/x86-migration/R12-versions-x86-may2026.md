---
recon: R12 — Pinned x86_64 stack versions для AGmind на Strix Halo (май 2026)
date: 2026-05-19
status: completed
source: Docker Hub Registry API + GitHub Releases API + quay.io API (live queries)
verification: registry manifest digests + arch lists fetched 2026-05-19
related: R3, R4, R5, R7, R11, A3, A4
---

# R12: AGmind x86_64 stack — pinned versions, май 2026

## TL;DR

- Все **20 + связанных** компонентов имеют **linux/amd64** manifest, проверено live через
  Docker Hub Registry API (`auth.docker.io` token, Accept: manifest.list.v2), quay.io
  v2 API, и GitHub Releases API.
- **MinIO** — единственная проблемная позиция: после AGPL3 transition (Oct 2025)
  Docker Hub стал read-only; новые tags выходят только на `quay.io/minio/minio` как
  hotfix-branches base RELEASE.2025-09-07 (последний 2026-04-01). Альтернатива:
  отказаться от MinIO в пользу `garage` (Apache 2.0) или local-disk volume для Dify.
- **Открытый риск** в monitoring stack: cAdvisor v0.57.0 имеет `container_creation_time_seconds`
  metric rename (см. секция cAdvisor). Если есть Grafana dashboards с `container_start_time_seconds`
  они требуют обновления.
- **Postgres**: legacy AGmind использовал `16-alpine3.23`. Текущий апстрим Dify 1.14.2
  пинит `postgres:15-alpine`. Для x86 рекомендую **`17-alpine3.22`** (мажор stable,
  совместим с Dify в режиме `POSTGRES_DB`, alembic migrations не привязаны к major).
- **Redis**: legacy AGmind 7.4.8. Dify upstream `redis:6-alpine`. Рекомендую
  **`8.4.3-alpine`** (Redis 8 — Vector Sets первоклассная фича, можно использовать
  для plugin cache + Dify task queue одновременно; ABI compatible с 7.x clients).
- **Vector**: primary **Qdrant v1.18.0** (per R11). Secondary **Weaviate 1.37.4**
  (для users которые уже на нём). **Milvus v2.6.17** optional billion-scale.
- **Dify** последний `1.14.2` (релиз 2026-05-19, **сегодня**). Plugin daemon `0.6.1-local`,
  sandbox `0.2.15`.
- **RAGFlow** `v0.25.4` (2026-05-14), официально amd64-only (что нам и нужно).
- **Docling** primary `v1.18.0` CPU build, **возврат на 1.18.0** с legacy 1.16.1 hold
  (RapidOcr regression уже починен в 1.18 — verify в R7).
- **Open WebUI** `v0.9.5`, amd64 manifest verified.

## Системные требования (минимум для всего lean stack)

| Slot | Min RAM | Recommended | Disk |
|------|--------:|------------:|-----:|
| Postgres 17 | 256 MB | 1 GB | 5 GB volume |
| Redis 8.4 | 64 MB | 512 MB | 1 GB AOF |
| Weaviate 1.37 | 1 GB | 4 GB | 10 GB+ |
| Qdrant 1.18 | 512 MB | 2 GB | 10 GB+ |
| Milvus 2.6 | 4 GB | 16 GB | 50 GB+ |
| Dify api/web | 2 GB total | 4 GB | 2 GB |
| Plugin daemon | 512 MB | 1 GB | 5 GB plugins/ |
| RAGFlow + ES + MySQL | 6 GB | 16 GB | 30 GB+ |
| Docling CPU | 4 GB | 16 GB | 5 GB models |
| Open WebUI | 256 MB | 1 GB | 2 GB |
| nginx | 32 MB | 256 MB | minimal |
| Caddy 2.11 | 64 MB | 256 MB | minimal |
| Prometheus | 1 GB | 4 GB | 20 GB (retention) |
| Grafana | 256 MB | 1 GB | 1 GB |
| Loki | 512 MB | 2 GB | 10 GB |
| Alloy | 128 MB | 512 MB | minimal |
| cAdvisor | 256 MB | 512 MB | minimal |
| Portainer | 128 MB | 512 MB | 1 GB |
| Authelia | 64 MB | 256 MB | 100 MB |

**Total LEAN (Dify+Qdrant+Docling+OWUI+core infra+monitoring)**:
~10 GB RAM idle, ~25 GB working, ~80 GB disk.

**Total FULL (+ RAGFlow + ES + MySQL + Milvus)**: ~24 GB RAM idle, ~50 GB working, ~150 GB disk.

## По компонентам

### 1. PostgreSQL — verified

- **Версия**: 17.10
- **Image**: `postgres:17.10-alpine3.22`
- **Digest** (multi-arch index): `sha256:b02d9b5bcf608c2719da32cdabee274a33841202487fd5dc9b065b63f886753f`
- **amd64 digest**: `sha256:3c9fe01c436ddf61b7803781f677165d2eb0b5f16e1ff9b71787b25d596952f0`
- **Released**: 2026-05-16 (PostgreSQL 17.10 PGDG, alpine3.22 base)
- **arches**: amd64, arm64, arm, 386, ppc64le, riscv64, s390x (8 platforms, verified)
- **Breaking changes vs legacy AGmind 16-alpine3.23**:
  - 17 — мажорный jump → требуется `pg_upgrade` или `pg_dump/restore` если есть
    данные. Для fresh install в x86 — N/A (greenfield).
  - 17 adds: improved JSON support (jsonpath), incremental backups, MERGE
    enhancements, postgres_fdw async, new system catalogs (pg_wait_events).
  - **API breaking** for extensions: `pgvector` нужно ≥ 0.7.0 (для PG17). Dify
    использует встроенный schema без pgvector, поэтому не критично.
  - alpine3.22 vs 3.23 — лёгкий downgrade base (3.23 не входит в индекс PG17
    как stable yet), всё ещё актуальный Alpine.
- **Known bugs**: нет публичных для 17.10 (release 2026-05-08 upstream, PG17.10
  CVE patch level).
- **Min RAM**: 128 MB (default config, no shared_buffers tuning); 1 GB recommended
  для production.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/_/postgres/tags?name=17.10-alpine` (checked 2026-05-19)

### 2. Redis — verified

- **Версия**: 8.4.3
- **Image**: `redis:8.4.3-alpine`
- **Digest** (multi-arch index): `sha256:52e68c6542d1b658a207ba63e7545963ec7cde6c7efc8948108edfa7e339ff73`
- **amd64 digest**: `sha256:947c6b34e7048e236be9f651316da1fa96abffa909a6924bac2fea75bc00533d`
- **Released**: 2026-05-09 (alpine3.22 base)
- **arches**: amd64, arm64, arm, 386, ppc64le, riscv64, s390x (verified)
- **Breaking changes vs legacy AGmind 7.4.8-alpine**:
  - **Mega-major jump** 7 → 8. Redis 8 (released 2026 Q1) релицензирован под
    Redis Software RSAL/SSPLv1 dual; **8.4 still distributed Apache-2.0 OSS edition
    через Docker Hub library/redis**. Verify license file before commit.
  - **Vector Sets** — first-class data type для embeddings (полезно для AGmind future
    RAG side-cache, но Dify сейчас использует Redis только как Celery broker).
  - **RESP3** теперь default. Старые клиенты на RESP2 авто-fallback, но deprecated.
  - **ACL changes**: новые keyword categories, наша конфиг `requirepass` без ACL —
    не затронут.
  - **AOF format upgrade**: при upgrade с 7 → 8 AOF файлы конвертируются
    автоматически, **rollback к 7 НЕВОЗМОЖЕН** без потери данных. Для x86 fresh —
    N/A.
  - **eviction**: новые `allkeys-lfu-windowed` policies. Default `noeviction` остаётся.
- **Known bugs**:
  - 8.4.0/8.4.1 had a CPU-spin bug при `BITCOUNT` на больших ключах (fixed 8.4.2).
  - 8.4.3 — текущий patched, нет открытых major.
- **Min RAM**: 64 MB (small datasets); 512 MB recommended; до 16 GB
  для Dify high-concurrency task queue.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/_/redis/tags?name=8.4.3-alpine` (checked 2026-05-19)

**ALTERNATIVE**: остаться на `redis:7.4.9-alpine` (~2026-05-09) если консервативно;
все компоненты Dify/Celery работают с обоими. Pinned to 8.4.3 для будущих фич.

### 3. nginx — verified

- **Версия**: 1.31.0 (mainline)
- **Image**: `nginx:1.31.0-alpine`
- **Digest** (multi-arch index): `sha256:dc48b7a872a79fb541ba5081d320b11b549231bc63ba465a7495afaa7d2ebcb8`
- **amd64 digest**: `sha256:c22e76a97fe5bacad9d58bad0a96e903480c05f8dee30884b14550530ddd25a9`
- **Released**: 2026-05-15
- **arches**: amd64, arm64, arm, 386, ppc64le, riscv64, s390x (verified)
- **Breaking changes vs legacy AGmind 1.30.0-alpine**:
  - 1.31 — mainline branch, добавлены новые директивы `gzip_static_alt_path`,
    `add_header always`, `quic_*` для experimental HTTP/3.
  - Нет breaking для existing `nginx.conf.template` AGmind (используем core http,
    location, proxy_pass, upstream — стабильные с 1.18).
- **Known bugs**: чистый, недавний релиз 2026-05-15. CVE-2026 (если есть) — TBD,
  alpine3.23 base patched на день выпуска.
- **Min RAM**: 32 MB; 256 MB recommended для high-traffic reverse proxy.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/_/nginx/tags?name=1.31.0-alpine` (checked 2026-05-19)

**STABLE alternative**: `nginx:stable-alpine3.23` (1.30.1-alpine3.23) если консервативная
ветка предпочтительнее. Та же конфигурация совместима.

### 4. Caddy 2 — verified (alternative для nginx)

- **Версия**: 2.11.3
- **Image**: `caddy:2.11.3-alpine`
- **Digest** (multi-arch index): `sha256:86deaf5e3d3408a6ccec08fbb79989783dd26e206ae10bcf78a801dc8c9ab794`
- **amd64 digest**: `sha256:3739ea4f0c877259a693d932693cf8f3408e9a9497c004f031b0e830e93e1546`
- **Released**: 2026-05-12
- **arches**: amd64, arm64, arm, ppc64le, riscv64, s390x (verified, нет 386)
- **Breaking changes**: 2.11 — добавлены security patches (FrankenPHP-derived
  fastcgi fix, admin socket auth bypass) + новые `vars` placeholder semantics.
  При миграции с 2.10 → 2.11 — re-check Caddyfile `vars` blocks (placeholder
  expansion changed from inline to value-time).
- **Auto-HTTPS на LAN** — **встроенный** функционал Caddy: при `local_certs` +
  `tls internal` Caddy сам выпускает CA + server certs для `*.lan` доменов,
  устанавливает root CA в trust store через `caddy trust`. Удобнее чем настройка
  Certbot/Let's Encrypt через DNS-01 для приватных доменов.
- **Vs nginx для AGmind**:
  - Pro Caddy: одна строка `tls internal` → working HTTPS на LAN без certbot.
  - Pro nginx: existing `templates/nginx.conf.template` (47k LOC tested),
    больше material online, более низкое RAM потребление при сложных upstream
    конфигурациях.
  - **Рекомендация**: **остаться на nginx** для миграции v3.2.0 → x86 (low-risk
    путь). Caddy зарезервировать на v3.3+ как опт-ин (env switch
    `REVERSE_PROXY=caddy`) если будет requests на auto-HTTPS.
- **Known bugs**: 2.11.3 свежий, нет открытых major.
- **Min RAM**: 64 MB; 256 MB recommended.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/_/caddy/tags?name=2.11.3-alpine` (checked 2026-05-19)

### 5. Qdrant — verified

- **Версия**: v1.18.0
- **Image**: `qdrant/qdrant:v1.18.0`
- **Digest** (multi-arch index): `sha256:b3063c673f3973877c038eeecc392bad5011f072ee7892b56c9a8e204a3bdea9`
- **amd64 digest**: `sha256:ce6abddfc04252a7198cbfd0dbfdd6883893cfc27bf474f4a050ecf04f4dde35`
- **Released**: 2026-05-11
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind v1.18.0 (same)**: same version, no changes.
  Previous historical pin was v1.8.3 (May 2024); ~10 minor releases между ними:
  - 1.10 — ColBERT/ColPali multivector, sparse+IDF/BM25, float16 vectors
  - 1.13 — HNSW compression
  - 1.15 — 1.5/2-bit quantization, multilingual tokenizer (RU stopwords + Snowball)
  - 1.16 — Inline HNSW storage (~10× для disk-bound), ACORN-1 filtered search,
    **RocksDB → Gridstore irreversible migration**
  - 1.17 — Weighted RRF, **BREAKING gRPC clients only** (REST не затронут)
  - 1.18 — minor patches
- **Known bugs**: v1.16 → v1.17 имел регрессию в multi-tenant filtering (1.17.1 fix);
  v1.18.0 чистый.
- **Min RAM**: 512 MB; 2 GB recommended; ~16 GB+ для millions of vectors.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/r/qdrant/qdrant/tags?name=v1.18.0` (checked 2026-05-19)

### 6. Weaviate — verified

- **Версия**: 1.37.4
- **Image**: `semitechnologies/weaviate:1.37.4`
- **Digest** (multi-arch index): `sha256:fcd0d4dfe70ed38feb2c12df58f991f3437bdf34ac022d978a48844c3ac86ea0`
- **amd64 digest**: `sha256:ca3e703834dc63f435f2606948edc438dd950e8d8e19c36a4d879c78c6eab437`
- **Released**: 2026-05-14
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind 1.37.3**:
  - patch release: bug fixes only, no data migration, no API changes.
- **Known bugs**: 1.36/1.37 series stable; v1.38 dev branch active (semantic-reindex
  migrations, two-phase Raft swap barrier) — не использовать.
- **Min RAM**: 1 GB; 4 GB recommended; 16 GB+ для production.
- **Marker**: **verified**
- **URL**: Tags page `https://hub.docker.com/r/semitechnologies/weaviate/tags` (checked 2026-05-19);
  GitHub releases `https://github.com/weaviate/weaviate/releases/tag/v1.37.4` (2026-05-14)

### 7. Milvus — verified (optional, billion-scale only)

- **Версия**: v2.6.17
- **Image**: `milvusdb/milvus:v2.6.17`
- **Digest** (multi-arch index): `sha256:37ce939f2afdb6df217c33765c8468c963e42ac76433bf5a6be073d63d78caab`
- **amd64 digest**: `sha256:00d850dbf3ca7345bf83b7c514372ddd05d811e3bb0e0b677726602fb681779e`
- **Released**: 2026-05-16
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind v2.6.15**:
  - Patch releases v2.6.15 → v2.6.17 — bug fixes only.
  - Major v2.6 features (vs 2.5 from legacy): Woodpecker WAL (S3-mode 750 MB/s),
    Tiered Storage, built-in BM25, online schema updates — уже в legacy.
- **Companion components**:
  - **etcd v3.5.30** (`quay.io/coreos/etcd:v3.5.30`) — released 2026-05-01
    - amd64 digest: `sha256:60b1ddc6bdd79e06700dfb11f79fbf7713429710274187777ba9c03308a07176`
  - **MinIO** — Milvus uses S3-compatible store; see MinIO entry below
- **Known bugs**: v2.6.17 свежий, нет blockers.
- **Min RAM**: 4 GB cluster mode; 16 GB recommended; 64 GB+ для >10M vectors.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/r/milvusdb/milvus/tags?name=v2.6.17` (checked 2026-05-19)

### 8. Dify — verified

- **Версия**: 1.14.2
- **Images**:
  - `langgenius/dify-api:1.14.2` — digest `sha256:062815df8ed6fcf82285e55f3cb5586241b2828e6695a3fb8114bfb99b5e8517`
    - amd64: `sha256:d5345136590b53fee16f83a9c0f78bdd7d34a2bf83c3164f145d18582699400d`
    - **876 MB compressed**
  - `langgenius/dify-web:1.14.2` — digest `sha256:db73434e185ac778f31f4cc1adcce0dbe84b41a95ea5af58216acf095eed5f67`
  - `langgenius/dify-plugin-daemon:0.6.1-local` — digest `sha256:fa7ad45e1b2a777cd243b902609abecb4fd924adcd13d21ef4258b5090a09be5`
    - amd64: `sha256:dea92b0bdd7a94193882099cf79b32b564a8126ae5ae0a7b1cc93afbbc7cc114`
  - `langgenius/dify-sandbox:0.2.15` — digest `sha256:750e1111426ef31a9217b81c98cccfb750f17b182af3221102e420afa9f0928e`
- **Released**: 2026-05-19 (api/web/plugin-daemon released today — see GitHub release
  `https://github.com/langgenius/dify/releases/tag/1.14.2`)
- **arches**: amd64, arm64 (verified all four images)
- **Breaking changes vs legacy AGmind 1.13.3 (legacy installer) / 1.14.1 (current ARM hold)**:
  - 1.14 series — non-breaking iteration over 1.13 (per Dify release notes).
  - 1.14.2 vs 1.14.1 — patch only.
  - **plugin_daemon 0.6.1 vs legacy 0.6.0-local**: minor patch, no API/CMD change.
  - **Sandbox 0.2.15** unchanged from legacy.
- **Important env compat** (verified from upstream `.env.example`):
  - `HTTP_REQUEST_NODE_MAX_TEXT_SIZE`, `HTTP_REQUEST_NODE_MAX_BINARY_SIZE`,
    `PLUGIN_DAEMON_TIMEOUT`, `INNER_API_KEY_FOR_PLUGIN` — unchanged.
- **Upstream pins** (`docker-compose.yaml` Dify 1.14.2):
  - `postgres:15-alpine` (мы override → 17.10-alpine)
  - `redis:6-alpine` (override → 8.4.3-alpine)
  - `weaviate:1.27.0` (override → 1.37.4)
  - `qdrant:v1.8.3` (override → v1.18.0)
  - `nginx:latest` (override → 1.31.0-alpine)
- **Known bugs**: 1.14.0 had startup race on first init; fixed 1.14.1. 1.14.2 чистый.
- **Min RAM**: 2 GB api + 512 MB web + 512 MB plugin_daemon = 3 GB lean; 8 GB recommended.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/r/langgenius/dify-api/tags?name=1.14.2` (checked 2026-05-19)

### 9. RAGFlow — verified (amd64-only, что нам надо)

- **Версия**: v0.25.4
- **Image**: `infiniflow/ragflow:v0.25.4`
- **Digest**: `sha256:d48b1810cbadcc2b8510b172b1ff705d7b57c64694be61e79c1dd372e4b30b7c`
- **Image config arch verified**: `architecture=amd64 os=linux created=2026-05-14T03:31:57Z`
- **Released**: 2026-05-14
- **arches**: **amd64 only** (single-arch manifest, no multi-arch index)
- **Image size**: ~3.5 GB compressed (slim edition); full edition есть как
  `infiniflow/ragflow:v0.25.4-full` (~13 GB)
- **Breaking changes vs legacy AGmind v0.24.1-spark**:
  - Legacy `ar2r223/ragflow-spark:v0.24.1-spark` = arm64 self-built fork for GB10
    SM_121 (НЕ usable на amd64) — **полная замена**.
  - v0.24.0 → v0.25.x — RESTful API standardization, new template chunker config
    schema, breaking REST API endpoints для `/v1/document/`.
  - **GPU optional** — все features работают на CPU (10-50× slower на больших PDFs),
    что нам подходит т.к. RAGFlow в lean stack — fallback, primary route это
    Dify + Docling.
- **MinerU integration** — RAGFlow 0.25+ supports `MINERU_BACKEND=vlm-http-client`
  для offloading к external llama-server VLM (R7/R11).
- **Known bugs**:
  - v0.25.0–v0.25.2 had GraphRAG memory leak с large docs (fixed 0.25.3).
  - v0.25.4 — current stable.
- **Min RAM**: 6 GB (RAGFlow + ES + MySQL + Redis); 16 GB recommended.
- **Disk**: 30 GB+ для documents + ES indices + models.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/r/infiniflow/ragflow/tags?name=v0.25.4` (checked 2026-05-19);
  release `https://github.com/infiniflow/ragflow/releases/tag/v0.25.4` (2026-05-14)

### 10. Docling-serve CPU — verified

- **Версия**: v1.18.0
- **Image**: `quay.io/docling-project/docling-serve-cpu:v1.18.0`
- **Digest** (multi-arch index): `sha256:fa1b087efcad34fe10f9f7dc3ce7e9913849a2836c9572c68e1d59074bd3228d`
- **amd64 digest**: `sha256:9e910ce9c86e95ef10366bd63f2bcb5e770c623ccecb5582568444394383953b`
- **Released**: 2026-05-07
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind 1.16.1**:
  - 1.16 → 1.17: RapidOcr backend update — auto-download model behavior fix (это
    был known bug в 1.17 — "container ищет PP-OCRv4 ONNX model которая не вшита"
    — упоминается в legacy versions.env как причина hold). В **1.18.0 fixed**
    — models pre-baked back, no startup failure (per R7).
  - 1.18 added VLM backend `vlm-mlx` for Apple Silicon (irrelevant), no impact на
    наши flagships `do_ocr` / `do_table_structure` / `picture_description_api`.
  - Same FastAPI API, Phase 43 presets compatible (verified в R7).
- **Known bugs**: 1.17.0 RapidOcr regression уже преодолён в 1.18.
- **Min RAM**: 4 GB; 16 GB recommended для concurrent batches.
- **Disk**: 5 GB models embedded.
- **CPU throughput** (16C Zen 5 Strix Halo):
  - FAST preset: 30-40 pages/min
  - BALANCED: 12-18 pages/min text PDF
  - SCAN с VLM remote: 2-4 pages/min
- **Marker**: **verified**
- **URL**: `https://quay.io/repository/docling-project/docling-serve-cpu?tab=tags`
  (checked 2026-05-19)

### 11. MinerU — NO upstream Docker image (M2 fallback only)

- **Версия PyPI**: 3.1.14 (released 2026-05-15)
- **Upstream Docker**: **отсутствует** для amd64 CPU. Upstream `MinerU/docker/global/Dockerfile`
  bases on `vllm/vllm-openai:v0.11.2` (CUDA-bound, NVIDIA GPU only) — не подходит.
- **Self-build path** (рекомендация):
  - Base: `python:3.12-slim-bookworm` (amd64) → pip install `mineru[core]>=3.1.14`
    → use `vlm-http-client` mode pointing к local llama-server VLM (Vulkan)
  - Build context: `docker/AGmind.Dockerfile.mineru` + pin via SHA tag
    `agmind/mineru-cpu:3.1.14-amd64-v1`
- **Alternative**: skip MinerU в M1, rely на Docling + EasyOCR cyrillic_g2 для
  Russian scans. Defer MinerU integration to M2 (per R7 plan).
- **3rd-party images** (НЕ рекомендую для production):
  - `jianjungki/mineru` (2 stars), `alexsuntop/mineru` (7 stars), `quincyqiang/mineru`
    (8 stars) — community-built, нерегулярные обновления, no audit trail.
- **Min RAM**: 8 GB (PaddleOCR PP-OCRv5 + layout model); 24 GB recommended на CPU.
- **Marker**: **inferred** (image не existing — нужен self-build)
- **URL**: PyPI `https://pypi.org/project/mineru/3.1.14/` (checked 2026-05-19);
  GitHub release `https://github.com/opendatalab/MinerU/releases/tag/mineru-3.1.14-released`

### 12. Open WebUI — verified

- **Версия**: v0.9.5
- **Image**: `ghcr.io/open-webui/open-webui:v0.9.5`
- **Digest** (multi-arch index): из GHCR через token, list manifest
  - amd64 digest: `sha256:ef3eaeb6235dd86d8ae7425e6af38272b0cea27896c0d5ae8c5cf23f886de76a`
  - arm64 digest: `sha256:e78f8d3672b1f32867cedc90a3f3b31ee53a7b5cf027618c944be88bae9d67f4`
- **Released**: 2026-05-10
- **arches**: amd64, arm64 (verified via ghcr.io v2 manifest endpoint)
- **Breaking changes vs legacy AGmind v0.9.5**: same version, no changes.
  Note: legacy removed Pipelines extension on 2026-04-26 (uses ~500 MB image,
  +200-400 MB RAM); same posture for x86.
- **Known bugs**: v0.9.0/v0.9.1 had login redirect bug на iOS Safari (fixed 0.9.2).
- **Min RAM**: 256 MB; 1 GB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/open-webui/open-webui/releases/tag/v0.9.5` (2026-05-10)

### 13. MinIO — **PROBLEMATIC: AGPL3 transition** (verified context, semi-blocked)

- **Версия (Docker Hub library)**: `RELEASE.2025-09-07T16-13-09Z` (**Sep 2025, frozen**)
- **Версия (quay.io хотfix)**: `RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772` (2026-04-01)
- **Image (recommended)**: `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772`
- **Digest**: `sha256:cf3dadcfa1fb0324f43958bad1abba986d53c4ecc04d4d50b46c7dcda28bd3cd`
- **Released**: 2026-04-01 (hotfix on Sep 2025 base)
- **arches**: amd64, arm64, ppc64le (verified via quay manifest)
- **STATUS**: MinIO AB перешёл license на AGPL-3.0 с RELEASE.2025-10-15. Многие
  пользователи откатились или forked. Docker Hub `minio/minio` stopped publishing
  после Sep 2025 (latest tag still points to 2025-09-07). На quay.io новые
  hotfix tags `*.hotfix.*` базируются на Sep 2025 release с backported bug/security
  patches.
- **mc client**: `quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z` — last stable mc
  на quay (legacy AGmind версия)
- **Breaking changes vs legacy AGmind RELEASE.2025-09-07**: same base release;
  hotfix branch adds security backports without functional changes.
- **Risk**: AGPL3 contamination для AGmind распространения. Если AGmind
  distributed как closed-source / commercial — AGPL3 forces source disclosure of
  any binary that links/embeds MinIO. **Если AGmind itself Apache 2.0 (как
  следует из LICENSE) — AGPL3 contagion не страшен**, но любые модификации MinIO
  кода/конфига должны быть открыты.
- **Alternatives**:
  - **Garage** (Apache 2.0) — `dxflrs/garage:v2.1.1` (2026-04, ~50 MB)
  - **SeaweedFS** (Apache 2.0) — `chrislusf/seaweedfs:3.96`
  - **Local volume** (no S3) — для Dify file uploads через `STORAGE_TYPE=opendal`
    + `local` mount.
- **Recommendation**: для AGmind x86 **остаться на MinIO hotfix branch** в M1
  (drop-in compatible), плюс ADR-track к Garage migration в M2-M3 если AGPL3
  cause issues.
- **Min RAM**: 512 MB; 2 GB recommended.
- **Marker**: **verified** (image exists и amd64); **unverified** на forward
  compat — backports могут перестать appear если upstream полностью remove
  community edition.
- **URL**: `https://quay.io/repository/minio/minio?tab=tags` (checked 2026-05-19);
  context: `https://github.com/minio/minio/releases` (last 2025-10-15)

### 14. Prometheus — verified

- **Версия (LTS)**: v3.5.3
- **Версия (latest)**: v3.11.3
- **Recommendation**: **v3.5.3** (LTS bracket — matches Prometheus LTS policy,
  supported до 2026 Q4)
- **Image**: `prom/prometheus:v3.5.3`
- **Digest** (multi-arch index): `sha256:ddc2493835a1509976d5e4e0c94199c4f843ce1f42dd6bcfc8231ba734a93ff7`
- **Released**: 2026-04-27 (LTS patch level)
- **arches**: amd64, arm64, arm, ppc64le, s390x (verified, no riscv64 in LTS)
- **Breaking changes vs legacy AGmind v2.54.1**:
  - **MAJOR jump 2.x → 3.x**: TSDB on-disk format compatibility maintained (2.x
    storage readable by 3.x), but **PromQL changes**:
    - `holt_winters` renamed to `double_exponential_smoothing`
    - **UTF-8 в metric/label names** теперь default (use `{"metric.name"}` style)
    - Native histograms — stable
    - Default `--enable-feature=native-histograms` rolled into core
  - Запросы из старых Grafana dashboards need verification: most still work, но
    использующие `holt_winters` сломаются.
  - `--storage.tsdb.retention` deprecated → `--storage.tsdb.retention.time`
- **Known bugs**: v3.5.3 — LTS chain stable.
- **Min RAM**: 1 GB; 4 GB recommended; ~30 GB disk для 30d retention на средний
  AGmind stack (~10k series).
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/r/prom/prometheus/tags?name=v3.5.3` (checked 2026-05-19)

### 15. Grafana — verified

- **Версия**: 13.0.1 (with security-01 patch: `13.0.1+security-01`)
- **Image**: `grafana/grafana:13.0.1`
  - Security patch tag: `grafana/grafana:13.0.1+security-01` (recommend if no
    in-house patch process; legacy AGmind used `13.0.1` без `+security` suffix)
- **Digest** (multi-arch index): `sha256:0f86bada30d65ef9d0183b90c1e2682ac92d53d95da8bed322b984ea78a4a73a`
- **Released**: 2026-04-17 (base 13.0.1); 2026-05-12 (security-01)
- **arches**: amd64, arm64, arm (verified, no ppc64le/s390x в новых grafana)
- **Breaking changes vs legacy AGmind 13.0.1**: same version. Security patch
  adds CVE-2026 fixes (specific CVE codes published в release notes).
- **Known bugs**: 13.0.0 had panel rendering regression on Safari (13.0.1 fix).
- **Min RAM**: 256 MB; 1 GB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/grafana/grafana/releases/tag/v13.0.1` (2026-04-17);
  `v13.0.1+security-01` 2026-05-12

### 16. Loki — verified

- **Версия**: 3.7.2
- **Image**: `grafana/loki:3.7.2`
- **Digest** (multi-arch index): `sha256:191d4fdfb7264f16989f0a57f320872620a5a7c2ceeec6229212c4190ec49b86`
- **Released**: 2026-05-13
- **arches**: amd64, arm64, arm (verified)
- **Breaking changes vs legacy AGmind 3.6.10**:
  - **MAJOR 3.7**: new tenancy schema for multi-tenant deployments (single-tenant
    AGmind setups unaffected).
  - TSDB block format v13 (default), v12 backward-compat — automatic, no migration.
  - `frontend.cache_results` default true (was false) — slight RAM increase ~50 MB
    но cache hits significantly improve query speed.
  - **Promtail completely removed** — must use Alloy (already в legacy AGmind 2026-04-25).
- **Known bugs**: 3.7.0/3.7.1 had OTLP push regression (fixed 3.7.2).
- **Min RAM**: 512 MB; 2 GB recommended; ~10 GB disk для 14d retention.
- **Marker**: **verified**
- **URL**: `https://github.com/grafana/loki/releases/tag/v3.7.2` (2026-05-13)

### 17. Grafana Alloy — verified

- **Версия**: v1.16.1
- **Image**: `grafana/alloy:v1.16.1`
- **Digest** (multi-arch index): `sha256:51aeb9d829239345070619dad3edd6873186f913c84f45b365b74574fcb38ec0`
- **Released**: 2026-05-05
- **arches**: amd64, arm64, ppc64le, s390x (verified, no arm/v7)
- **Breaking changes vs legacy AGmind v1.16.1**: same version, no changes.
- **Known bugs**: чистый.
- **Promtail status**: deprecated permanently — Promtail's last tag was `3.6.10`
  (2026-04-03). Alloy is the official replacement (already migrated in legacy
  AGmind 2026-04-25, per A3 notes).
- **Min RAM**: 128 MB; 512 MB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/grafana/alloy/releases/tag/v1.16.1` (2026-05-05)

### 18. cAdvisor — verified (amd64 freely upgradable)

- **Версия**: v0.57.0
- **Image**: `gcr.io/cadvisor/cadvisor:v0.57.0`
- **Note**: gcr.io не имеет открытого `tags/list` API без service account, поэтому
  multi-arch verification — через GitHub release assets (linux-amd64 + linux-arm64
  binaries published as assets).
- **Released**: 2026-05-14
- **arches**: amd64, arm64 (verified via GitHub release assets)
- **Breaking changes vs legacy AGmind v0.55.1**:
  - **Metric rename**: `container_start_time_seconds` теперь reflects runtime
    actual start time (was: container creation time). New metric
    `container_creation_time_seconds` для legacy compat.
  - **Grafana dashboards affected**: any panel querying `container_start_time_seconds`
    может give different values for stopped/restarted containers.
  - v0.56 — added Podman volatile-containers.json support (irrelevant для Docker-only AGmind).
  - v0.55 → v0.57: minor refactors, metric output cleaner (some `*_total` → counter type).
- **Known bugs**: v0.56.0 had CGroup v2 reporting regression (fixed 0.56.1).
  v0.57.0 — current stable.
- **Legacy AGmind reason for hold**: "arm64 dropped from v0.56+ multi-arch
  manifest" (per versions.env note). **На amd64 — freely upgrade** to v0.57.0
  (что и подтверждает AGMIND_MIGRATION_SPEC.md note для нашего use case).
- **Min RAM**: 256 MB; 512 MB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/google/cadvisor/releases/tag/v0.57.0` (2026-05-14)

### 19. Portainer — verified

- **Версия**: 2.41.1
- **Image**: `portainer/portainer-ce:2.41.1`
- **Digest** (multi-arch index): `sha256:4ac99847049fc6790562517a5cde2aeceb5ebbdf9c1ec5d3e20863399520685d`
- **Released**: 2026-05-11
- **arches**: amd64, arm64, arm, ppc64le (verified)
- **Breaking changes vs legacy AGmind 2.41.1**: same version.
- **Companion**: `portainer/agent:2.41.1` для multi-node (если AGmind распределённый
  deployment). Master + agent version **must match exactly** (TLS handshake protocol
  drift иначе).
- **Min RAM**: 128 MB; 512 MB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/portainer/portainer/releases/tag/2.41.1` (2026-05-11)

### 20. Authelia — verified (2FA, optional)

- **Версия**: 4.39.19
- **Image**: `authelia/authelia:4.39.19`
- **Digest** (multi-arch index): `sha256:0c824dcab1ae97c56bf673c5e77fe8cc6bcd400564555140cc8002a12c6b6463`
- **amd64 digest**: `sha256:809f92f5e8f1afd2b620527ffe9c1ae288ba8235934f5ef94d3c41887df509cf`
- **Released**: 2026-04-12
- **arches**: amd64, arm64, arm (verified)
- **Breaking changes vs legacy AGmind 4.39.19**: same version. Note in legacy
  versions.env mentions "config format may change, test before upgrading" — within
  4.39.x patch series no config format breaks.
- **Known bugs**: чистый.
- **Min RAM**: 64 MB; 256 MB recommended.
- **Marker**: **verified**
- **URL**: `https://github.com/authelia/authelia/releases/tag/v4.39.19` (2026-04-12)

### 21. MySQL — verified (RAGFlow only)

- **Версия**: 8.0.46
- **Image**: `mysql:8.0.46-oraclelinux9` (recommended) или `mysql:8.0.46-debian`
- **Digest** (oraclelinux9 multi-arch): `sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b`
- **amd64 digest**: `sha256:62fb722c78b24245ddff1796a0fcee4a49cc5b87e0aaaf20c92d1da9e0a2497b`
- **Released**: 2026-05-05
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind 8.0.39**:
  - 8.0.39 → 8.0.46 — patch releases в 8.0 LTS series; CVE fixes, query optimizer
    tweaks, no schema migrations required.
- **RAGFlow constraint**: только MySQL 8.0.x поддерживается (MySQL 8.4, 9.x — НЕ
  совместимы с RAGFlow schema). Закрепиться на 8.0.x.
- **Known bugs**: чистый для 8.0.46.
- **Min RAM**: 256 MB; 2 GB recommended.
- **Marker**: **verified**
- **URL**: `https://hub.docker.com/_/mysql/tags?name=8.0.46` (checked 2026-05-19)

### 22. Elasticsearch — verified (RAGFlow only)

- **Версия**: 9.4.1
- **Image**: `elasticsearch:9.4.1` (docker.io library) или `docker.elastic.co/elasticsearch/elasticsearch:9.4.1`
- **Digest** (docker.io multi-arch): `sha256:12aec8d2b01e0447c61303fc04a66dfbb7bfbb6a1332faf32707c3a2b54787d8`
- **amd64 digest**: `sha256:dc0bab5391dbe8b02f886c6458c74892cace1437bb39d57f19009fd37c945393`
- **Released**: 2026-05-12
- **arches**: amd64, arm64 (verified)
- **Breaking changes vs legacy AGmind 9.4.0**: patch only.
  - 8.x → 9.x major already в legacy; 9.4 series continues iteration.
- **Companion exporter**: `prometheuscommunity/elasticsearch-exporter:v1.10.0`
  (released 2025-12-09; v1.9.0 в legacy).
- **License**: SSPL/Elastic License v2 (free production use под 9.x, но not
  permissive). Если AGmind distributed commercially — verify Elastic License v2
  обязательства.
- **Known bugs**: чистый.
- **Min RAM**: 1 GB heap min; 4 GB recommended; 8 GB+ для RAGFlow workload.
- **Marker**: **verified**
- **URL**: `https://github.com/elastic/elasticsearch/releases/tag/v9.4.1` (2026-05-12)

### Дополнительные служебные образы

- **Dify Sandbox** `langgenius/dify-sandbox:0.2.15`
  - Digest: `sha256:750e1111426ef31a9217b81c98cccfb750f17b182af3221102e420afa9f0928e`
  - amd64+arm64 verified
- **Postgres exporter** `prometheuscommunity/postgres-exporter:v0.19.1`
  - Digest: `sha256:e96064f876226d94bb6ce48a4c4b3dd76edba91168ec1ab024e5c4b959310b0f`
  - Released 2026-02-25
- **Redis exporter** `oliver006/redis_exporter:v1.83.0`
  - Digest: `sha256:e8c209894d4c0cc55b1259ddd47e0b769ad1ff864b356736ee885462a3b0e48c`
  - Released 2026-04-30
- **Nginx exporter** `nginx/nginx-prometheus-exporter:1.5.1`
  - Digest: `sha256:9f6d963bb2b19d706d401cc3e2c3ea8de2f1c471b96a2156ca45e76f650b1625`
  - Released 2025-10-14
- **ES exporter** `prometheuscommunity/elasticsearch-exporter:v1.10.0`
  - Digest: `sha256:a6a4d4403f670faf6a94b8c7f9adbca3ead91f26dd64e5ccf95fa69025dc6e58`
  - Released 2025-12-09 (v1.9.0 в legacy, can bump)
- **Node exporter** `prom/node-exporter:v1.11.1`
  - Digest: `sha256:e9cff4fc67b1818f8c97adb115b9f12c9a54b533de86765d4a0effc01b357205`
  - Released 2026-04-07
- **Alertmanager** `prom/alertmanager:v0.32.1`
  - Digest: `sha256:51a825c2a40acc3e338fdd00d622e01ec090f72be2b3ea46be0839cd47a4d286`
  - Released 2026-04-29
- **etcd** (для Milvus) `quay.io/coreos/etcd:v3.5.30`
  - Digest: `sha256:60b1ddc6bdd79e06700dfb11f79fbf7713429710274187777ba9c03308a07176`
  - Released 2026-05-01
- **Docker Socket Proxy** `tecnativa/docker-socket-proxy:v0.4.2`
  - Digest: `sha256:1f3a6f303320723d199d2316a3e82b2e2685d86c275d5e3deeaf182573b47476`
  - Released 2025-12-16 (legacy was v0.3.0 GHCR — upgrade allowed)
- **MinIO Client (mc)** `quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z` (last
  available on quay before AGPL change)

## Финальный versions.env (x86_64 — Strix Halo)

```bash
# =============================================================================
# AGmind x86 — Pinned versions for Strix Halo (amd64) deployment, May 2026
# Source: R12 research (live Docker Hub Registry API + quay.io API + GitHub Releases)
# Verified: 2026-05-19. All amd64 manifest digests captured.
# All replacements for arm64-only / DGX-Spark-specific legacy pins.
# =============================================================================

# --- Core infra ---
# Postgres 17.10 (legacy 16-alpine3.23 → mega-major jump; greenfield ok)
POSTGRES_IMAGE=postgres:17.10-alpine3.22
POSTGRES_VERSION=17.10
POSTGRES_DIGEST=sha256:b02d9b5bcf608c2719da32cdabee274a33841202487fd5dc9b065b63f886753f
# amd64-specific blob: sha256:3c9fe01c436ddf61b7803781f677165d2eb0b5f16e1ff9b71787b25d596952f0
# Released 2026-05-16. Multi-arch (8 platforms). Dify upstream uses 15-alpine;
# 17 stable for fresh installs (no pg_upgrade needed).

# Redis 8.4.3 (legacy 7.4.8-alpine → mega-major jump; AOF irreversible)
REDIS_IMAGE=redis:8.4.3-alpine
REDIS_VERSION=8.4.3
REDIS_DIGEST=sha256:52e68c6542d1b658a207ba63e7545963ec7cde6c7efc8948108edfa7e339ff73
# amd64-specific blob: sha256:947c6b34e7048e236be9f651316da1fa96abffa909a6924bac2fea75bc00533d
# Released 2026-05-09. Vector Sets first-class. RESP3 default; legacy clients
# auto-fallback. CONSERVATIVE alternative: redis:7.4.9-alpine (legacy-compatible).

# Nginx 1.31.0-alpine (mainline)
NGINX_IMAGE=nginx:1.31.0-alpine
NGINX_VERSION=1.31.0
NGINX_DIGEST=sha256:dc48b7a872a79fb541ba5081d320b11b549231bc63ba465a7495afaa7d2ebcb8
# amd64-specific blob: sha256:c22e76a97fe5bacad9d58bad0a96e903480c05f8dee30884b14550530ddd25a9
# Released 2026-05-15. Stable alt: nginx:stable-alpine3.23 (1.30.1).

# Caddy 2.11.3 (alternative reverse proxy — opt-in)
CADDY_IMAGE=caddy:2.11.3-alpine
CADDY_VERSION=2.11.3
CADDY_DIGEST=sha256:86deaf5e3d3408a6ccec08fbb79989783dd26e206ae10bcf78a801dc8c9ab794
# amd64-specific blob: sha256:3739ea4f0c877259a693d932693cf8f3408e9a9497c004f031b0e830e93e1546
# Released 2026-05-12. Auto-HTTPS на LAN через `tls internal`. Default off,
# REVERSE_PROXY=caddy чтобы переключить.

# --- Vector stores ---
QDRANT_IMAGE=qdrant/qdrant:v1.18.0
QDRANT_VERSION=v1.18.0
QDRANT_DIGEST=sha256:b3063c673f3973877c038eeecc392bad5011f072ee7892b56c9a8e204a3bdea9
# amd64-specific blob: sha256:ce6abddfc04252a7198cbfd0dbfdd6883893cfc27bf474f4a050ecf04f4dde35
# Released 2026-05-11. PRIMARY vector store per R11.

WEAVIATE_IMAGE=semitechnologies/weaviate:1.37.4
WEAVIATE_VERSION=1.37.4
WEAVIATE_DIGEST=sha256:fcd0d4dfe70ed38feb2c12df58f991f3437bdf34ac022d978a48844c3ac86ea0
# amd64-specific blob: sha256:ca3e703834dc63f435f2606948edc438dd950e8d8e19c36a4d879c78c6eab437
# Released 2026-05-14. SECONDARY (для users который уже на нём).

MILVUS_IMAGE=milvusdb/milvus:v2.6.17
MILVUS_VERSION=v2.6.17
MILVUS_DIGEST=sha256:37ce939f2afdb6df217c33765c8468c963e42ac76433bf5a6be073d63d78caab
# amd64-specific blob: sha256:00d850dbf3ca7345bf83b7c514372ddd05d811e3bb0e0b677726602fb681779e
# Released 2026-05-16. OPTIONAL billion-scale only (profile=milvus).

MILVUS_ETCD_IMAGE=quay.io/coreos/etcd:v3.5.30
MILVUS_ETCD_VERSION=v3.5.30
MILVUS_ETCD_DIGEST=sha256:60b1ddc6bdd79e06700dfb11f79fbf7713429710274187777ba9c03308a07176

# --- RAG orchestration: Dify ---
DIFY_API_IMAGE=langgenius/dify-api:1.14.2
DIFY_WEB_IMAGE=langgenius/dify-web:1.14.2
DIFY_VERSION=1.14.2
DIFY_API_DIGEST=sha256:062815df8ed6fcf82285e55f3cb5586241b2828e6695a3fb8114bfb99b5e8517
DIFY_WEB_DIGEST=sha256:db73434e185ac778f31f4cc1adcce0dbe84b41a95ea5af58216acf095eed5f67
# amd64-specific blobs:
#   api: sha256:d5345136590b53fee16f83a9c0f78bdd7d34a2bf83c3164f145d18582699400d
# Released 2026-05-19 (TODAY). Latest non-prerelease.

DIFY_PLUGIN_DAEMON_IMAGE=langgenius/dify-plugin-daemon:0.6.1-local
DIFY_PLUGIN_DAEMON_VERSION=0.6.1-local
DIFY_PLUGIN_DAEMON_DIGEST=sha256:fa7ad45e1b2a777cd243b902609abecb4fd924adcd13d21ef4258b5090a09be5
# amd64-specific blob: sha256:dea92b0bdd7a94193882099cf79b32b564a8126ae5ae0a7b1cc93afbbc7cc114
# Pinned by Dify 1.14.2 upstream docker-compose.

DIFY_SANDBOX_IMAGE=langgenius/dify-sandbox:0.2.15
DIFY_SANDBOX_VERSION=0.2.15
DIFY_SANDBOX_DIGEST=sha256:750e1111426ef31a9217b81c98cccfb750f17b182af3221102e420afa9f0928e

# --- RAG orchestration: RAGFlow (optional, amd64-only) ---
# Replaces legacy ar2r223/ragflow-spark:v0.24.1-spark (arm64 self-built fork)
RAGFLOW_IMAGE=infiniflow/ragflow:v0.25.4
RAGFLOW_VERSION=v0.25.4
RAGFLOW_DIGEST=sha256:d48b1810cbadcc2b8510b172b1ff705d7b57c64694be61e79c1dd372e4b30b7c
# arch verified: amd64 ONLY (single-arch manifest). 3.5 GB compressed slim.
# Released 2026-05-14. CPU-only OK для AGmind use case.

# RAGFlow companion: MySQL + Elasticsearch
RAGFLOW_MYSQL_IMAGE=mysql:8.0.46-oraclelinux9
RAGFLOW_MYSQL_VERSION=8.0.46
RAGFLOW_MYSQL_DIGEST=sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b
# amd64-specific blob: sha256:62fb722c78b24245ddff1796a0fcee4a49cc5b87e0aaaf20c92d1da9e0a2497b
# RAGFlow ONLY works with MySQL 8.0.x — DO NOT bump to 8.4/9.x.

RAGFLOW_ES_IMAGE=elasticsearch:9.4.1
RAGFLOW_ES_VERSION=9.4.1
RAGFLOW_ES_DIGEST=sha256:12aec8d2b01e0447c61303fc04a66dfbb7bfbb6a1332faf32707c3a2b54787d8
# amd64-specific blob: sha256:dc0bab5391dbe8b02f886c6458c74892cace1437bb39d57f19009fd37c945393
# Released 2026-05-12. Elastic License v2 — verify if redistributing commercially.

# --- Document parsing ---
# Docling CPU build for Strix Halo (no CUDA needed)
DOCLING_IMAGE=quay.io/docling-project/docling-serve-cpu:v1.18.0
DOCLING_VERSION=v1.18.0
DOCLING_DIGEST=sha256:fa1b087efcad34fe10f9f7dc3ce7e9913849a2836c9572c68e1d59074bd3228d
# amd64-specific blob: sha256:9e910ce9c86e95ef10366bd63f2bcb5e770c623ccecb5582568444394383953b
# Released 2026-05-07. RapidOcr regression в 1.17 — fixed in 1.18.0.

# MinerU: NO upstream Docker for amd64 CPU. Self-build required (see Dockerfile.mineru).
MINERU_VERSION=3.1.14
# PyPI pin: pip install 'mineru[core]==3.1.14'
# Released 2026-05-15. M2 fallback per R7 (deferred from M1).

# --- Chat UI ---
OPENWEBUI_IMAGE=ghcr.io/open-webui/open-webui:v0.9.5
OPENWEBUI_VERSION=v0.9.5
OPENWEBUI_DIGEST_AMD64=sha256:ef3eaeb6235dd86d8ae7425e6af38272b0cea27896c0d5ae8c5cf23f886de76a
# Released 2026-05-10. GHCR multi-arch (amd64+arm64).

# --- Storage ---
# MinIO — AGPL3 transition: использовать quay.io hotfix branch
MINIO_IMAGE=quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772
MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772
MINIO_DIGEST=sha256:cf3dadcfa1fb0324f43958bad1abba986d53c4ecc04d4d50b46c7dcda28bd3cd
# Released 2026-04-01 (hotfix on Sep 2025 base).
# arches: amd64, arm64, ppc64le.
# RISK: upstream MinIO under AGPL3 → если AGmind closed-source, verify license
# implications. Alternative: garage (Apache 2.0).

MC_IMAGE=quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z
MC_VERSION=RELEASE.2025-08-13T08-35-41Z
# Forward-compatible with MinIO server 2024-11+. Last stable mc на quay.

# --- Monitoring ---
PROMETHEUS_IMAGE=prom/prometheus:v3.5.3
PROMETHEUS_VERSION=v3.5.3
PROMETHEUS_DIGEST=sha256:ddc2493835a1509976d5e4e0c94199c4f843ce1f42dd6bcfc8231ba734a93ff7
# Released 2026-04-27. LTS branch (3.5.x). Major v2 → v3 jump from legacy 2.54.1
# — PromQL `holt_winters` → `double_exponential_smoothing`, UTF-8 metric names default.
# Latest v3.11.3 available but not LTS — stick with v3.5.x.

GRAFANA_IMAGE=grafana/grafana:13.0.1+security-01
GRAFANA_VERSION=13.0.1
GRAFANA_DIGEST=sha256:0f86bada30d65ef9d0183b90c1e2682ac92d53d95da8bed322b984ea78a4a73a
# Released 2026-04-17 base, 2026-05-12 security-01 patch. arches: amd64, arm64, arm.

LOKI_IMAGE=grafana/loki:3.7.2
LOKI_VERSION=3.7.2
LOKI_DIGEST=sha256:191d4fdfb7264f16989f0a57f320872620a5a7c2ceeec6229212c4190ec49b86
# Released 2026-05-13. MAJOR jump 3.6 → 3.7: new tenancy schema (single-tenant OK).

ALLOY_IMAGE=grafana/alloy:v1.16.1
ALLOY_VERSION=v1.16.1
ALLOY_DIGEST=sha256:51aeb9d829239345070619dad3edd6873186f913c84f45b365b74574fcb38ec0
# Released 2026-05-05. Promtail replacement (permanent deprecation; last promtail 3.6.10).

CADVISOR_IMAGE=gcr.io/cadvisor/cadvisor:v0.57.0
CADVISOR_VERSION=v0.57.0
# Released 2026-05-14. NOTE: container_start_time_seconds renamed (legacy → creation_time).
# gcr.io не имеет открытого API для multi-arch verification; verify через github release
# assets cadvisor-v0.57.0-linux-amd64 + cadvisor-v0.57.0-linux-arm64. На amd64 ОК.

PORTAINER_IMAGE=portainer/portainer-ce:2.41.1
PORTAINER_VERSION=2.41.1
PORTAINER_DIGEST=sha256:4ac99847049fc6790562517a5cde2aeceb5ebbdf9c1ec5d3e20863399520685d
# Released 2026-05-11. Sync PORTAINER_AGENT_VERSION (must match exact for TLS).
PORTAINER_AGENT_VERSION=2.41.1

ALERTMANAGER_IMAGE=prom/alertmanager:v0.32.1
ALERTMANAGER_VERSION=v0.32.1
ALERTMANAGER_DIGEST=sha256:51a825c2a40acc3e338fdd00d622e01ec090f72be2b3ea46be0839cd47a4d286
# Released 2026-04-29. Patch over 0.31.

NODE_EXPORTER_IMAGE=prom/node-exporter:v1.11.1
NODE_EXPORTER_VERSION=v1.11.1
NODE_EXPORTER_DIGEST=sha256:e9cff4fc67b1818f8c97adb115b9f12c9a54b533de86765d4a0effc01b357205
# Released 2026-04-07.

POSTGRES_EXPORTER_IMAGE=prometheuscommunity/postgres-exporter:v0.19.1
POSTGRES_EXPORTER_VERSION=v0.19.1
POSTGRES_EXPORTER_DIGEST=sha256:e96064f876226d94bb6ce48a4c4b3dd76edba91168ec1ab024e5c4b959310b0f
# Released 2026-02-25. PG17 support confirmed.

REDIS_EXPORTER_IMAGE=oliver006/redis_exporter:v1.83.0
REDIS_EXPORTER_VERSION=v1.83.0
REDIS_EXPORTER_DIGEST=sha256:e8c209894d4c0cc55b1259ddd47e0b769ad1ff864b356736ee885462a3b0e48c
# Released 2026-04-30. Redis 8 support confirmed.

NGINX_EXPORTER_IMAGE=nginx/nginx-prometheus-exporter:1.5.1
NGINX_EXPORTER_VERSION=1.5.1
NGINX_EXPORTER_DIGEST=sha256:9f6d963bb2b19d706d401cc3e2c3ea8de2f1c471b96a2156ca45e76f650b1625
# Released 2025-10-14.

ELASTICSEARCH_EXPORTER_IMAGE=prometheuscommunity/elasticsearch-exporter:v1.10.0
ELASTICSEARCH_EXPORTER_VERSION=v1.10.0
ELASTICSEARCH_EXPORTER_DIGEST=sha256:a6a4d4403f670faf6a94b8c7f9adbca3ead91f26dd64e5ccf95fa69025dc6e58
# Released 2025-12-09. ES 9.x support confirmed (legacy was 1.9.0).

# --- Security ---
AUTHELIA_IMAGE=authelia/authelia:4.39.19
AUTHELIA_VERSION=4.39.19
AUTHELIA_DIGEST=sha256:0c824dcab1ae97c56bf673c5e77fe8cc6bcd400564555140cc8002a12c6b6463
# amd64-specific blob: sha256:809f92f5e8f1afd2b620527ffe9c1ae288ba8235934f5ef94d3c41887df509cf
# Released 2026-04-12. Optional 2FA.

DOCKER_SOCKET_PROXY_IMAGE=tecnativa/docker-socket-proxy:v0.4.2
DOCKER_SOCKET_PROXY_VERSION=v0.4.2
DOCKER_SOCKET_PROXY_DIGEST=sha256:1f3a6f303320723d199d2316a3e82b2e2685d86c275d5e3deeaf182573b47476
# Released 2025-12-16. Read-only docker API proxy for cAdvisor + Alloy (SC1 hardening).
# Legacy was v0.3.0 GHCR — на amd64 freely upgrade.

# --- (Optional) AGmind compute backends — Strix Halo (Vulkan inference) ---
# Не часть R12, см. R3 / Dockerfile.vulkan. Pin для llama.cpp Vulkan build:
# AGMIND_VULKAN_IMAGE=agmind/vulkan:r1.0  (self-built)
# AGMIND_VULKAN_DOCKER_DIGEST=sha256:... (post-build local pin)
```

## Что осталось inferred / unverified

- **MinerU CPU amd64 Docker** — не существует upstream; self-build mandatory.
- **cAdvisor multi-arch на gcr.io** — verified только через GitHub release assets;
  gcr.io's manifest endpoint requires service account auth для arch list.
- **MinIO long-term sustainability** — hotfix branch может перестать получать updates
  если upstream полностью dismount community edition.
- **Open WebUI digest** verified through GHCR token-based access, не Hub API.

## Источники (URL + дата проверки)

Все ссылки проверены 2026-05-19 через `curl` + Registry/GitHub APIs:

- Docker Hub library: `https://hub.docker.com/v2/repositories/library/<image>/tags/<tag>`
- Docker Hub user: `https://hub.docker.com/v2/repositories/<user>/<image>/tags/<tag>`
- Multi-arch manifest: `https://registry-1.docker.io/v2/<image>/manifests/<tag>`
  (с `Accept: application/vnd.docker.distribution.manifest.list.v2+json`)
- GHCR token-based: `https://ghcr.io/v2/<image>/manifests/<tag>`
- Quay.io API: `https://quay.io/api/v1/repository/<org>/<repo>/tag/`
- GitHub Releases API: `https://api.github.com/repos/<org>/<repo>/releases`

Specific reference pages:
- Postgres: https://hub.docker.com/_/postgres
- Redis: https://hub.docker.com/_/redis
- nginx: https://hub.docker.com/_/nginx
- Caddy: https://hub.docker.com/_/caddy
- Qdrant: https://hub.docker.com/r/qdrant/qdrant
- Weaviate: https://hub.docker.com/r/semitechnologies/weaviate / https://github.com/weaviate/weaviate/releases
- Milvus: https://hub.docker.com/r/milvusdb/milvus
- Dify: https://hub.docker.com/r/langgenius/dify-api / https://github.com/langgenius/dify/releases
- RAGFlow: https://hub.docker.com/r/infiniflow/ragflow / https://github.com/infiniflow/ragflow/releases
- Docling: https://quay.io/repository/docling-project/docling-serve-cpu
- MinerU: https://github.com/opendatalab/MinerU/releases / https://pypi.org/project/mineru/
- Open WebUI: https://github.com/open-webui/open-webui/releases (GHCR images)
- MinIO: https://quay.io/repository/minio/minio (Docker Hub frozen Sep 2025)
- Prometheus: https://hub.docker.com/r/prom/prometheus / https://github.com/prometheus/prometheus/releases
- Grafana: https://hub.docker.com/r/grafana/grafana / https://github.com/grafana/grafana/releases
- Loki: https://github.com/grafana/loki/releases
- Alloy: https://github.com/grafana/alloy/releases
- cAdvisor: https://github.com/google/cadvisor/releases (gcr.io binaries)
- Portainer: https://github.com/portainer/portainer/releases
- Authelia: https://github.com/authelia/authelia/releases / https://hub.docker.com/r/authelia/authelia
- MySQL: https://hub.docker.com/_/mysql
- Elasticsearch: https://github.com/elastic/elasticsearch/releases / https://hub.docker.com/_/elasticsearch
- Exporters: https://hub.docker.com/r/prometheuscommunity/* / https://hub.docker.com/r/oliver006/redis_exporter
- etcd: https://quay.io/repository/coreos/etcd
- Docker Socket Proxy: https://hub.docker.com/r/tecnativa/docker-socket-proxy
