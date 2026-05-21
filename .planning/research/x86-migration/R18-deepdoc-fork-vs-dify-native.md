---
recon: R18 — DeepDoc как Dify plugin vs нативная связка Dify ↔ RAGFlow + Milvus 65k
date: 2026-05-21
status: completed
source_agents: 5 параллельных general-purpose (~120 источников суммарно)
related: R5, R7, R11, R12
deep_dives: deep-dives/2026-05-21/milvus-varchar-65k.md
---

# R18: можно ли форкнуть DeepDoc RAGFlow и упаковать в Dify-плагин

## Контекст вопроса

Юзер: "форкнуть стек обработки сложных таблиц из RAGFlow, упаковать в Dify-
plugin; работа специфическая, таблицы резать нельзя, Milvus принимает
только 65k а таблица 138k". AGmindx86 на AMD Strix Halo (gfx1151,
ROCm 7.2.3, 16C Zen5, 128 GB unified RAM). Текущие пины (см. R12):
Dify `1.14.2` + plugin-daemon `0.6.1-local`, RAGFlow `v0.25.4`,
Milvus `v2.6.17`.

## TL;DR — рекомендация

**НЕ форкать DeepDoc как `.difypkg`.** Три независимых блокера:

1. **Размер**: дефолтный лимит `.difypkg` = 50 MB ([dify-plugin-daemon/.env.example](https://github.com/langgenius/dify-plugin-daemon/blob/main/.env.example)). Минимальный set DeepDoc ONNX-весов = **103 MB** (det+rec+tsr+layout), полный = ~331 MB. Поднять `MAX_PLUGIN_PACKAGE_SIZE` можно, но `nginx`/unzip-память/install-timeout 15 мин делают это контр-продуктивным.
2. **Русский язык**: `ocr.res` charset в [InfiniFlow/deepdoc](https://huggingface.co/InfiniFlow/deepdoc) содержит CJK + латиницу + пунктуацию. **Кириллицы НЕТ**. DeepDoc out-of-the-box не парсит русские таблицы корректно — нужен либо swap recognizer (как в [hoaivannguyen/deepdoc_vietocr](https://github.com/hoaivannguyen/deepdoc_vietocr) для вьетнамского), либо дообучение PaddleOCR rec на кириллический корпус.
3. **ROCm/Strix Halo**: код [deepdoc/vision/ocr.py L96-131](https://github.com/infiniflow/ragflow/blob/v0.25.4/deepdoc/vision/ocr.py) знает только `CUDAExecutionProvider`/`CPUExecutionProvider`. ROCm/MIGraphX EP в `onnxruntime` 1.23+ удалён ([ONNX Runtime docs](https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html)), нужны AMD-форк-wheels из [Radeon native install](https://rocm.docs.amd.com/projects/radeon/en/latest/docs/install/native_linux/install-onnx.html). На gfx1151 это **не валидировано** (NOT FOUND in issues).

**ВМЕСТО форка — нативная связка `Dify ↔ RAGFlow` через `/api/v1/dify/retrieval`.** RAGFlow `v0.25.x` экспонирует Dify-совместимый External-KB endpoint напрямую, прокладок не нужно. Связка работоспособна именно в паре `Dify ≥1.13.1` (фикс #32765) + `RAGFlow ≥0.25.2` (PR с GET health-probe) — наши пины 1.14.2 + 0.25.4 совместимы.

**Milvus 65k не относится к самой задаче.** `65535` — это **байты UTF-8**, не символы; кириллический 138k-char пассаж это ≈276 KB ≈ 4.2× over. Реальный bottleneck — context window embedding-модели **bge-m3 = 8192 токенов**. Даже подъём `proxy.maxVarCharLengthBytes` (PR #38883, configurable до 1 MiB) даст хранение, но не качество retrieval'а. Правильный паттерн: **row-group chunking + полный HTML-blob в MinIO + reference в `metadata.table_uri`** (см. deep-dive [milvus-varchar-65k.md](deep-dives/2026-05-21/milvus-varchar-65k.md)).

---

## Часть A. DeepDoc как извлекаемый модуль из RAGFlow v0.25.4

### A.1 Граница чистого кода

В монорепо [v0.25.4](https://github.com/infiniflow/ragflow/tree/v0.25.4) подпакет `deepdoc/` это `parser/` (17 файлов) + `vision/` (9 файлов). Отдельного `pyproject` для него **нет** — единый пакет `ragflow` (`[tool.setuptools] packages=['agent','api','deepdoc','graphrag',...]`).

| Подмодуль | Чистота извлечения |
|---|---|
| `deepdoc/vision/*` (OCR, layout, TSR, recognizer) | **чисто** — только `common.file_utils.get_project_base_directory` (тривиально вырезается) |
| `deepdoc/parser/pdf_parser.py` | **грязно**: импорты `common.constants/file_utils/settings/misc_utils`, `rag.nlp.rag_tokenizer`, `rag.prompts.generator.vision_llm_describe_prompt` |
| `deepdoc/parser/docx/excel/...` | импортируют `rag.nlp` + `rag.utils.lazy_image.LazyImage` |
| `deepdoc/__init__.py` | вызывает `beartype.claw.beartype_this_package()` — рантайм type-check на весь пакет, **сносить** |
| `rag/app/*.py` (наивный/template chunker) | сильно завязан на `api.db.services.llm_service`, MinIO, Celery, ES — **не извлекать** |

**Не тянет**: Celery, MinIO, Elasticsearch, peewee/MySQL, Flask, RAGFlow API. Эти приходят через `rag/app/*.py`, а не через сам DeepDoc.

**Объём чистого извлечения**: ~25-30 файлов, ~250-350 KB исходников.

### A.2 ONNX-модели на HuggingFace InfiniFlow/deepdoc

| Файл | Размер | Назначение | Архитектура |
|---|---|---|---|
| `det.onnx` | 4.75 MB | OCR text detection | PaddleOCR DB-net (ONNX export) |
| `rec.onnx` | 10.8 MB | OCR text recognition | PaddleOCR CRNN/SVTR |
| `tsr.onnx` | 12.2 MB | **Table Structure Recognition** | DETR-like, 6 labels (table/column/row/col-header/projected-row-header/spanning-cell) |
| `layout.onnx` | 75.7 MB | Generic layout detector | **YOLOv10** (класс `LayoutRecognizer4YOLOv10` в [layout_recognizer.py L168](https://github.com/infiniflow/ragflow/blob/v0.25.4/deepdoc/vision/layout_recognizer.py)) |
| `layout.{laws,manual,paper}.onnx` | 75.7 MB × 3 | Domain-tuned layout | YOLOv10 fine-tunes |
| `ocr.res` | 26.2 KB | Charset dict | **CJK + ASCII Latin + пунктуация, БЕЗ кириллицы** |

**Минимальный set для PDF без domain-fine-tuning**: `det + rec + tsr + layout` = **103 MB**.
**Полный**: ~331 MB.

### A.3 Дефицит русского OCR в DeepDoc

Анализ `ocr.res` показывает отсутствие кириллических кодпойнтов. Это блокер для российского use-case AGmindx86. Варианты:

| Подход | Effort | Качество |
|---|---|---|
| Swap `TextRecognizer` на EasyOCR `cyrillic_g2` | 1-2 дня | средне (см. R7: EasyOCR хуже PaddleOCR PP-OCRv5) |
| Swap на Tesseract `rus.traineddata` | часы | посредственное (R7) |
| Дообучить PaddleOCR rec на русском корпусе + ONNX export | 5-10 дней + GPU train | best, но не оправдывает форк |
| Использовать [RapidOCR](https://github.com/RapidAI/RapidOCR) русскую модель в standalone TSR-стеке | 2-3 дня | хорошее (см. часть D) |

### A.4 ROCm/Strix Halo пригодность

В DeepDoc-коде явный switch:
```python
# deepdoc/vision/ocr.py L96-131
if cuda_is_available():
    providers = ['CUDAExecutionProvider']
else:
    providers = ['CPUExecutionProvider']
```

Других провайдеров не предусмотрено. Чтобы запустить на Strix Halo:

1. Патч ocr.py (+ recognizer.py + layout_recognizer.py + table_structure_recognizer.py) — ~30-50 строк, добавить ветку `MIGraphXExecutionProvider` (`ROCMExecutionProvider` упразднён в ORT 1.23+).
2. Установить `onnxruntime-rocm` wheel из [AMD Radeon ONNX install](https://rocm.docs.amd.com/projects/radeon/en/latest/docs/install/native_linux/install-onnx.html) — но **официальной сборки под gfx1151 нет**, придётся либо собирать из исходников, либо использовать community AMD-форк.
3. Проверить совместимость YOLOv10/DETR custom-ops с MIGraphX backend ([microsoft/onnxruntime#18052](https://github.com/microsoft/onnxruntime/issues/18052) — исторически были проблемы с custom ops в layout-моделях).

**Никаких готовых ROCm-сборок DeepDoc в природе нет** (NOT FOUND in issues/PR upstream). Это означает 1-3 недели работы под валидацию на одном железе.

### A.5 Существующие попытки выделить DeepDoc

| Проект | ⭐ | Last commit | Статус |
|---|---|---|---|
| [`Zire-Young/DeepDoc`](https://github.com/Zire-Young/DeepDoc) | 16 | 2025-02-13 | мёртв, нет pyproject |
| [`hedon-ai-road/deepdoc_pdfparser`](https://github.com/hedon-ai-road/deepdoc_pdfparser) ([PyPI](https://pypi.org/project/deepdoc-pdfparser/)) | 9 | 2025-06-18 | **самый близкий к проду**, заброшен, без GPU/ROCm |
| [`hoaivannguyen/deepdoc_vietocr`](https://github.com/hoaivannguyen/deepdoc_vietocr) | 17 | 2026-05-05 | **пример swap recognizer** на не-CJK язык — референс для рус |
| `aceliuchanghong/myDeepdoc`, `lemonguess/deepdoc`, etc. | 3-8 | abandoned | hobby-форки |

**InfiniFlow официально DeepDoc-standalone не выделяет.** Поддерживать форк = взять на себя regular merge-back security/perf фиксов upstream.

### A.6 Лицензионная чистота

- RAGFlow [LICENSE@v0.25.4](https://github.com/infiniflow/ragflow/blob/v0.25.4/LICENSE) = **Apache 2.0** ✓
- PaddleOCR (det/rec backbone) = Apache 2.0 ✓
- YOLOv10 архитектура (Tsinghua THU-MIG) формально AGPL-3.0; веса InfiniFlow заявлены Apache 2.0 (собственная тренировка). Для коммерческого продукта — серая зона, для self-host — OK
- `xgboost` BSD/Apache, `shapely` BSD, `opencv` Apache — чисто

---

## Часть B. Dify 1.14 plugin runtime — реальные лимиты

### B.1 Категории плагинов (May 2026)

[docs.dify.ai/en/plugins/introduction](https://docs.dify.ai/en/plugins/introduction):

| Тип | Status | Применимость к doc-parser |
|---|---|---|
| Tool | stable | ✅ — тонкий HTTP proxy к sidecar (паттерн `langgenius/mineru`, `langgenius/llama_parse`) |
| Model | stable | ❌ |
| Agent Strategy | stable | ❌ |
| Extension / Endpoint | stable | ✅ — генерит уникальный URL, можно повесить webhook-ingestion |
| Datasource | stable c 1.9 | ⚠ только web crawler / online document / online drive — локальный parser **не предусмотрен** |
| Trigger | stable c 1.14 | ❌ |
| Bundle | stable | meta-тип |

### B.2 Runtime изоляция (plugin-daemon 0.6.1)

Из [internal/types/app/config.go](https://github.com/langgenius/dify-plugin-daemon/blob/main/internal/types/app/config.go):

- **`local` runtime** (наш случай в docker-compose): subprocess + STDIN/STDOUT, `uv venv` per plugin
- **`debug`**: TCP duplex
- **`serverless`**: AWS Lambda HTTP
- **Sandbox**: НЕТ gVisor/seccomp/firejail для плагинов. DifySandbox существует только для Code-node ([docs.dify.ai/development/backend/sandbox](https://docs.dify.ai/development/backend/sandbox))
- **CPU/mem limits**: `resource.memory` в manifest = hint для AWS Lambda, на self-host **не enforced**. Только `PLUGIN_LOCAL_LAUNCHING_CONCURRENT`, `ROUTINE_POOL_SIZE`
- **Network egress**: через `ssrf_proxy` (Squid) с anti-SSRF blacklist private-IP — для inter-container запросов нужно либо whitelist в Squid, либо `NO_PROXY` ([#9917](https://github.com/langgenius/dify/issues/9917), [#18752](https://github.com/langgenius/dify/issues/18752))
- **Python**: ≥ 3.12 (SDK `dify-plugin` 0.8.0+)

### B.3 Размер бандла

Default `MAX_PLUGIN_PACKAGE_SIZE = 52428800` (50 MiB) ([.env.example](https://github.com/langgenius/dify-plugin-daemon/blob/main/.env.example)). Self-hosted можно поднять до ~500 MB ([discussion #26207](https://github.com/langgenius/dify/discussions/26207)), но:

- `nginx.NGINX_CLIENT_MAX_BODY_SIZE` (default 100M) режет upload
- unzip-памяти plugin-daemon на больших бандлах не хватает
- `PLUGIN_INSTALL_TIMEOUT=15min` блокирует процесс установки моделей
- Issue [#405 dify-plugin-daemon](https://github.com/langgenius/dify-plugin-daemon/issues/405) — реальные пользователи упираются в 50 MB

**Для DeepDoc 103 MB minimum / 331 MB full — упаковка в `.difypkg` непрактична.**

### B.4 Реальный паттерн для тяжёлых ML-плагинов

Marketplace показывает консистентный подход:

| Плагин | Внутренний размер | Архитектура |
|---|---|---|
| [`langgenius/mineru`](https://marketplace.dify.ai/plugin/langgenius/mineru) | memory 256 MB, storage 1 MB | HTTP proxy к user-deployed MinerU Web API |
| [`langgenius/llama_parse`](https://marketplace.dify.ai/plugin/langgenius/llama_parse) | memory 1 MB | cloud-only, HTTP к LlamaIndex Cloud |
| Docling/Unstructured/EasyDoc/Mistral OCR | n/a | declared в Knowledge Pipeline blog, паттерн тот же |

**100% случаев: heavy ML живёт в sidecar-контейнере, плагин = 5-50 KB HTTP-клиент.**

### B.5 SDK и tooling

- Python SDK `dify-plugin` 0.8.0 (PyPI, 8 May 2026), repo HEAD 0.9.0 (20 May 2026)
- CLI scaffold: `dify plugin init` ([init docs](https://docs.dify.ai/plugins/quick-start/develop-plugins/initialize-development-tools))
- Go/TypeScript официальных SDK для plugins — **NOT FOUND**
- Repo SDK: [langgenius/dify-plugin-sdks](https://github.com/langgenius/dify-plugin-sdks)

### B.6 Datasource plugin vs External Knowledge API

| | Datasource plugin (1.9+) | External Knowledge API |
|---|---|---|
| Что отдаёт в Dify | сам документ (raw blob/URL) | retrieved chunks `[{content, score, metadata, title}]` |
| Куда уходит | Knowledge Pipeline (Dify сам парсит и индексирует) | прямо в LLM context на retrieval-step |
| Подходит для doc-parser? | как source, не как parser | **best fit** — RAGFlow делает parsing+retrieval, отдаёт чанки |

Для нашего кейса (RAGFlow уже умеет всё) — **External Knowledge API**, не Datasource.

---

## Часть C. Нативная связка Dify ↔ RAGFlow через External KB

### C.1 Контракт API (Dify side)

[docs.dify.ai/en/use-dify/knowledge/external-knowledge-api](https://docs.dify.ai/en/use-dify/knowledge/external-knowledge-api):

**Endpoint**: `POST {base_url}/retrieval` (Dify сам дописывает `/retrieval` к зарегистрированному base URL).
**Auth**: `Authorization: Bearer <api_key>`.

**Request**:
```json
{
  "knowledge_id": "<dataset_uuid>",
  "query": "...",
  "retrieval_setting": { "top_k": 8, "score_threshold": 0.5 },
  "metadata_condition": { "logical_operator": "and|or", "conditions": [...] }
}
```

**Response**:
```json
{
  "records": [
    { "content": "string", "score": 0.0..1.0, "title": "string", "metadata": {...} }
  ]
}
```

Ошибки: `1001/1002` auth, `2001` knowledge_id not found, плюс HTTP 4xx/5xx.

**Лимит на `content`**: в спеке не задокументирован. Внутренний Dify `INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH=4000` касается parent-child retrieval для internal KB, не External KB (issue [#12500](https://github.com/langgenius/dify/issues/12500)). Реальный потолок = max-input-tokens у LLM-узла.

### C.2 RAGFlow экспозиция

**Подтверждено: нативно**. `v0.25.x` экспонирует Dify-совместимый endpoint:
```
POST http://<ragflow-host>:9380/api/v1/dify/retrieval
GET  http://<ragflow-host>:9380/api/v1/dify/retrieval   # health probe (v0.25.2+)
```

Release notes:
- **v0.25.2**: add GET method support для Dify health-check
- **v0.25.3**: guard retrieval argument error в Dify-интеграции
- **v0.25.4** (наш target): оба фикса включены

Generic-эндпоинт `/api/v1/retrieval` (другой payload: `question`, `dataset_ids[]`, `similarity_threshold`, `vector_similarity_weight`, `use_kg`, `toc_enhance`) — для прямого API, не для Dify.

Прокладка [`mobiusy/dify-external-datasets`](https://github.com/mobiusy/dify-external-datasets) — dormant (3 коммита, 2024), **не нужна сейчас**.

### C.3 UI-настройка (точные поля)

**Шаг 1: зарегистрировать API**. *Knowledge → External Knowledge API → Add*:
- **Name**: label
- **API Endpoint**: `http://ragflow:9380/api/v1/dify` (без `/retrieval` — Dify допишет)
- **API Key**: создать в RAGFlow → avatar → API → API KEY → Create New Key

**Шаг 2: создать external KB**. *Knowledge → Connect to External Knowledge Base*:
- **External Knowledge ID**: UUID dataset из URL RAGFlow → пойдёт в `knowledge_id`
- **Top K**, **Score Threshold**

⚠ Поля `API` и `Knowledge ID` нельзя изменить после создания — пересоздавать KB.

⚠ В docker-compose `RAGFLOW_HOST` должен резолвиться — в одной сети использовать имя сервиса, иначе IP/FQDN ([#9917](https://github.com/langgenius/dify/issues/9917)).

### C.4 Известные ограничения

- **Метаданные теряются**: `metadata.table_id` сохранится во внутреннем pipeline (виден в `KnowledgeRetrieval` node output), но **не пропустится в outward response** `/chat-messages` → `retriever_resources` ([#11422 closed as not planned](https://github.com/langgenius/dify/issues/11422)). Workaround: класть человекочитаемое в `title` ("Table 3, page 12 — sales report").
- **Ranking полностью отдан внешнему KB**: Dify не делает свой rerank, `score_threshold` применяется на RAGFlow. Полей Rerank Model / Hybrid Search в external-KB UI нет.
- **`use_kg` (RAGFlow knowledge graph) недоступен** через External KB ([#24002 closed as not planned](https://github.com/langgenius/dify/issues/24002)). Если нужен — переключаться на Tool-plugin [`witmeng/ragflow-api`](https://marketplace.dify.ai/plugin/witmeng/ragflow-api), теряя автоматические citations.
- **N+1 calls** если несколько KB в одной retrieval-ноде ([#22561](https://github.com/langgenius/dify/issues/22561)) — Dify зовёт каждый KB отдельным POST. Workaround: единый dataset_id в RAGFlow.
- **HTML в content**: RAGFlow с 0.23.0 умеет Image & Table Context Window (chunk = таблица + сопровождающий текст). Dify передаёт content в prompt как есть → LLM получит сырой HTML. Для table-heavy лучше переключить parser RAGFlow на Markdown.

### C.5 Версионная совместимость

| Component | Min | Наш pin | OK? |
|---|---|---|---|
| Dify External KB API | 0.14.0 (Dec 2024), стабилизирован к 1.0 | 1.14.2 | ✅ |
| Dify fix SourceMetadata bug | 1.13.1 (PR [#32765](https://github.com/langgenius/dify/pull/32765) merged 2026-03-02) | 1.14.2 | ✅ |
| RAGFlow Dify endpoint | ~v0.16 (early 2025), production-ready v0.25.2+ | v0.25.4 | ✅ |

---

## Часть D. Альтернативы DeepDoc для Strix Halo CPU

См. подробнее R7. Здесь — фокус именно на **TSR (table structure recognition)** для сложных spanning-таблиц.

### D.1 Сравнительная матрица (CPU only, gfx1151 fallback)

| Tool | Backend | License | sec/table CPU* | Spanning | Multi-page merge | XLSX path | Verdict для AGmind |
|---|---|---|---|---|---|---|---|
| **GMFT 0.4.3** | TATR via HF transformers + pypdfium2 | MIT | **1.17s** (Colab CPU) | semantic_spanning_cells + multi_header (0.3+) | нет | n/a | **PRIMARY** — без detectron2/poppler |
| Docling TableFormer | PyTorch (CPU/MPS) | MIT | 1.74s fast / 3-5s accurate | OTSL | нет | через docling XLSX | secondary (уже в R7) |
| RapidTable + UniTable | onnxruntime CPU | Apache 2.0 | 0.15s SLANet ONNX / 6s UniTable | да | нет | n/a | **ONNX-fallback** — чистый CPU без torch |
| PP-StructureV3 SLANeXt | Paddle / ONNX (`use_onnx=True`) | Apache 2.0 | ~0.77s | wired + wireless | нет | docx export | хорошее качество, но Paddle dep weight |
| Granite-Docling 258M VLM | transformers VLM | Apache 2.0 | 20-60s/page CPU | E2E VLM | нет | n/a | TEDS-struct **0.97** на FinTabNet — лучшее качество, цена — скорость |
| Microsoft TATR raw | PyTorch DETR | MIT | 2-4s | partial (нужен post-proc) | нет | n/a | использовать через GMFT |
| Marker (datalab) | Surya OCR + optional LLM | GPL-3 | ~30× Tesseract | через LLM-loop | да (внутри pipeline) | n/a | GPL + медленный на CPU |
| Unstructured 0.18 hi_res | YOLOX + TATR | Apache 2.0 | 10× медленнее GMFT | да | нет | n/a | толстый wrapper, обходим |
| **openpyxl + tablepyxl** | pure Python | MIT/Apache | мс/sheet | merged_cells API | n/a | streaming `read_only=True` | **XLSX native, без OCR** |
| Camelot/Tabula | Ghostscript/Java | MIT | <1s/page | нет | manual stitch | n/a | fallback для lattice grid PDFs |

\* sec/table Zen5 — экстраполяция; точных бенчмарков на gfx1151 нет.

### D.2 Большие multi-page таблицы (138k chars)

**Никто из TSR-инструментов не делает нативный multi-page table merge** (NOT FOUND). Гибридный подход:

1. **Per-page TSR** через GMFT → DataFrame per page
2. **Header-signature matching** между страницами (cosine similarity column names) → merge groups
3. **Drop повторяющиеся headers** на стыках
4. Export → единый HTML/Markdown blob → MinIO как `s3://agmind/tables/<doc_id>.html`

Аналоги ad-hoc: AWS Textract `merge_tables_across_pages`, pdfplumber + custom concatenation, коммерческие DataSnipper/Parseur. В open-source — собственный layer.

### D.3 XLSX-источник

Если 138k-char "таблица" приходит в XLSX, **TSR не нужен вообще**:

```python
from openpyxl import load_workbook
wb = load_workbook("big.xlsx", read_only=True, data_only=True)
ws = wb.active
# merged_cells доступны через ws.merged_cells.ranges
```

`read_only=True` — потоковый SAX XML без загрузки в RAM (тестировано на 200 MB+ / 1M+ rows). Для HTML-export — `tablepyxl` с корректным сохранением merge-cells.

### D.4 Recommendation stack

**Primary path (PDF тяжёлые таблицы):**
```
Docling fast (CPU)  →  GMFT 0.4.3 fallback на spanning-heavy  →  Granite-Docling 258M VLM для краевых
```

**ONNX-only path** (если хотим обойтись без torch на gfx1151):
```
RapidTable + SLANet ONNX  +  RapidOCR (русский поддержан)
```

**XLSX path**: openpyxl read_only + tablepyxl, обход TSR.

**Избегать**: Marker base (без LLM слабый, GPL), Unstructured hi_res (медленный wrapper над TATR), Nougat (frozen), DeepDoc (нет кириллицы, нет ROCm).

---

## Часть E. Milvus 65k vs 138k-char таблица — деконструкция

См. полный разбор в [deep-dives/2026-05-21/milvus-varchar-65k.md](deep-dives/2026-05-21/milvus-varchar-65k.md).

### E.1 65535 — это БАЙТЫ UTF-8, не символы

- Milvus `VARCHAR.max_length` range = `1..65535`, единица — байты UTF-8 ([milvus.io/docs/string.md](https://milvus.io/docs/string.md))
- Кириллица в UTF-8 = 2 байта/символ → 138 000 символов = 276 000 байт = **4.2× over**
- Китайский/эмодзи = 3-4 байта/символ → ещё хуже
- Даже ASCII 138 000 байт > 65 535

### E.2 Подъём лимита возможен, но это анти-паттерн

[PR #38883](https://github.com/milvus-io/milvus/pull/38883) merged в Milvus 2.5 (Jan 2025) — `proxy.maxVarCharLengthBytes` стал configurable, default бампнут до **1 MiB**. Maintainers ([discussion #45653](https://github.com/milvus-io/milvus/discussions/45653)) предупреждают:
- 500k rows × 1 MiB = 500 GB segment
- Load latency unacceptable
- Решает симптом, не корень

### E.3 TEXT type не GA

[Issue #39818](https://github.com/milvus-io/milvus/issues/39818) RFC для `TEXT(10MB)` запланирован на **Milvus 3.0** — на 2026-05-21 не GA.

### E.4 FTS != long-text storage

Milvus 2.5+ добавил Full-Text Search через BM25 + `SPARSE_FLOAT_VECTOR`. Но текст всё равно лежит в VARCHAR с тем же 65535-байтовым лимитом. FTS решает retrieval, не storage.

### E.5 Главный bottleneck — embedding context

**bge-m3 (наш rec в R5/R11) принимает 8192 токена.** 138 000 символов = ~25-40k токенов = **3-5× over**.

BAAI собственным бенчмарком ([HF BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)) показал: **chunked(512) >> single(8192)** по MRR@K. То есть положить таблицу одним блобом не получится **никаким** vector store — даже Qdrant с его 32 MiB/point payload или Weaviate без hard limit.

### E.6 Правильный schema-паттерн

```python
# Milvus collection
embedding:     FLOAT_VECTOR(1024)   # bge-m3 dense
sparse:        SPARSE_FLOAT_VECTOR  # BM25 (2.5+)
chunk_text:    VARCHAR(8192)        # row-group ~512 tokens
metadata:      JSON                 # {doc_id, table_id, row_start, row_end,
                                    #  headers, blob_uri, page_number}
```

**Chunking strategy** для большой таблицы:
1. Header row → запоминаем как shared header
2. Резать по N rows (например, 20 строк per chunk), каждый chunk = header + rows
3. Embedding по chunk_text (shared header даёт контекст ranking'у)
4. Полная HTML-таблица → `s3://agmind-minio/tables/<doc_id>.html`
5. На retrieval: hybrid (dense + sparse) → top-K row-groups → optional подгрузка `blob_uri` для full context

### E.7 Альтернативные stores (если Milvus совсем не подходит)

| Store | VARCHAR/payload limit | Embedding bottleneck остаётся? |
|---|---|---|
| Milvus 2.6.17 | 65535 default, 1 MiB configurable | ✅ |
| Qdrant | 32 MiB/point payload | ✅ |
| Weaviate | без hard limit | ✅ |
| pgvector / Postgres | TEXT 1 GB | ✅ |

**Никакой смены store не отменит ограничение bge-m3 на 8192 токена.** Менять Milvus имеет смысл только по другим причинам (R11 уже предложил Qdrant как primary для lean stack).

---

## Часть F. Архитектурная рекомендация для AGmindx86

### F.1 Развёртывание (compose)

```yaml
# agmind/install/templates/compose.rag-table.yaml (предложение)

services:
  ragflow:
    image: infiniflow/ragflow:v0.25.4
    environment:
      DEVICE: cpu
      MINERU_BACKEND: vlm-http-client
      VLM_ENDPOINT: http://llm-vision:8083/v1
    networks: [agmind_internal]
    ports: ["9380:9380"]  # для health probe Dify
    volumes:
      - ragflow_data:/ragflow
    depends_on:
      - es01     # RAGFlow's own ES (или Infinity 2.x в новых версиях)
      - mysql

  dify-api:
    image: langgenius/dify-api:1.14.2  # already pinned R12
    environment:
      # whitelist ragflow в anti-SSRF
      SSRF_HTTP_PROXY_URL: ""
      NO_PROXY: ragflow,minio,milvus
    networks: [agmind_internal]

  milvus:
    image: milvusdb/milvus:v2.6.17     # already pinned R12
    # дефолтные maxVarCharLengthBytes (65535) — НЕ поднимать
    networks: [agmind_internal]

  minio:
    image: minio/minio:RELEASE.2026-04-xx  # R12
    networks: [agmind_internal]

  # OPTIONAL: standalone TSR sidecar за Dify Tool-plugin
  gmft-svc:
    image: agmind/gmft-svc:0.4.3       # custom thin FastAPI wrapper
    networks: [agmind_internal]
    # 2 GB RAM достаточно для TATR на CPU
```

### F.2 Wiring в Dify UI

1. *Knowledge → External Knowledge API → Add* → endpoint `http://ragflow:9380/api/v1/dify`, API key из RAGFlow
2. *Knowledge → Connect to External Knowledge Base* → dataset UUID из RAGFlow URL → Top K=2 (а не 8 default; для 138k таблиц безопаснее)
3. В workflow: Knowledge Retrieval node → ссылка на external KB → LLM node (модель с ≥128k context window — Qwen3-Coder-30B или llama 3.1 70B)

### F.3 RAGFlow side настройка для big-table

- Parser → **Markdown export** для таблиц (не HTML — экономит токены, лучше для LLM)
- Chunk method: `naive` с `chunk_token_count=1024..2048`, `delimiter` — empty (не резать таблицы по rows)
- Image & Table Context Window: **enable** (v0.23+) — chunk = таблица + сопровождающий текст
- `Top N` retrieval setting в RAGFlow: 2-4

### F.4 LLM context budget

138k chars ≈ 30-50k токенов. Доступные в стеке (по R-llm-models):
- Qwen3-Coder-30B Q4_K_XL — 256k context (через ik-llama-cpp, R17)
- llama 3.1 70B — 128k native
- Mistral Large 2 — 128k

С Top K=2 RAGFlow вернёт 2 чанка ≤8k токенов каждый → запас по context window есть.

---

## Часть G. Trade-off matrix: 3 пути решения

| Путь | Effort | Качество тех.таблиц | Strix-Halo risk | Поддержка long-term |
|---|---|---|---|---|
| **A. Dify ↔ RAGFlow нативно через `/api/v1/dify/retrieval`** | **минимум** (compose + UI wiring) | 8/10 (RAGFlow DeepDoc уже сильный TSR) | ✅ CPU-only, gfx1151 не нужен | upstream поддерживает обе стороны |
| B. Тонкий Tool-plugin для Dify + GMFT/Docling sidecar (без RAGFlow) | средне (1-2 недели) | 7/10 на CPU | ✅ pure CPU | свой код + upstream libs |
| C. Форк DeepDoc как extracted package + Tool-plugin + ROCm patch | **большой** (3-4 недели) | 8/10 если решить русский | ⚠ MIGraphX EP не валидирован на gfx1151 | свой форк ≈ daily merge-back |

**Рекомендация: путь A.** Если на нём окажется недостаточным качество русского OCR в RAGFlow (DeepDoc внутри тоже без кириллицы!) — переключиться на путь B с GMFT + RapidOCR русским.

**Путь C запускать только если** есть конкретные RAGFlow-баги, которые форк должен пофиксить, или специфические DeepDoc-фичи, которые недоступны через standalone GMFT/Docling.

---

## Часть H. Proposed updates в AGMIND_MIGRATION_SPEC.md

### H.1 Part 1.4 versions.env (новые пины, не upgrade)

```
# RAG-table стек (Phase TBD)
RAGFLOW_IMAGE=infiniflow/ragflow:v0.25.4         # already pinned R12
# Дополнительно — для path B (без RAGFlow):
GMFT_VERSION=0.4.3                                # MIT, CPU TSR via TATR
RAPIDTABLE_VERSION=...                            # если нужен ONNX path
```

### H.2 Part 1.2 add «RAG table integration»

```
- Primary path (table-heavy docs): Dify 1.14.2 ↔ RAGFlow v0.25.4
  через нативный POST /api/v1/dify/retrieval (External Knowledge API).
- Russian OCR caveat: RAGFlow DeepDoc не имеет кириллицы в ocr.res —
  для RU-документов переключить parser на Docling/MinerU sidecar
  (см. R7).
- Big-table chunking: Markdown export + chunk 1024-2048 tokens,
  full HTML-blob в MinIO с reference в chunk metadata.
- TSR fallback (без RAGFlow): GMFT 0.4.3 + RapidOCR sidecar за
  тонкий Dify Tool-plugin (паттерн langgenius/mineru).
```

### H.3 Part 1.3 «Запреты» (новый пункт)

```
| DeepDoc fork as .difypkg | 50MB plugin limit, ROCm not supported,
                            no cyrillic in ocr.res — три блокера
                            одновременно. Использовать RAGFlow
                            целиком (path A) или GMFT (path B) |
```

### H.4 Phase plan (предложение)

- **M.NN.1** — compose.ragflow.yaml + wiring в Dify UI (path A) — 1-2 дня
- **M.NN.2** — benchmark RU PDF/XLSX через RAGFlow на Strix Halo + measure pages/min
- **M.NN.3** — если path A провалится по русскому OCR качеству → path B с GMFT sidecar — 1 неделя

---

## Открытые вопросы для следующих recon'ов

1. **R19?** — производительность RAGFlow DeepDoc на Strix Halo Zen5 в pages/min (16 cores, 128 GB RAM) на представительном корпусе. Issues говорят "забивает 128-core", нужны наши числа.
2. **R20?** — реальное качество RAGFlow на русских отсканированных таблицах (DeepDoc без кириллицы, но RAGFlow с v0.20+ имеет MinerU backend, у которого PaddleOCR PP-OCRv5 кириллицу знает).
3. **R21?** — onnxruntime-rocm wheel build под gfx1151 — feasibility отдельным треком (полезно не только для DeepDoc, но и для других ONNX-моделей в стеке).

---

## Sources (по разделам)

### DeepDoc internals
- [RAGFlow v0.25.4 release](https://github.com/infiniflow/ragflow/releases/tag/v0.25.4), [LICENSE](https://github.com/infiniflow/ragflow/blob/v0.25.4/LICENSE), [pyproject.toml](https://github.com/infiniflow/ragflow/blob/v0.25.4/pyproject.toml)
- [deepdoc/parser tree](https://github.com/infiniflow/ragflow/tree/v0.25.4/deepdoc/parser), [deepdoc/vision tree](https://github.com/infiniflow/ragflow/tree/v0.25.4/deepdoc/vision)
- [pdf_parser.py raw](https://raw.githubusercontent.com/infiniflow/ragflow/v0.25.4/deepdoc/parser/pdf_parser.py), [ocr.py raw](https://raw.githubusercontent.com/infiniflow/ragflow/v0.25.4/deepdoc/vision/ocr.py), [layout_recognizer.py](https://github.com/infiniflow/ragflow/blob/v0.25.4/deepdoc/vision/layout_recognizer.py)
- [InfiniFlow/deepdoc HF](https://huggingface.co/InfiniFlow/deepdoc)
- Forks: [Zire-Young/DeepDoc](https://github.com/Zire-Young/DeepDoc), [hedon-ai-road/deepdoc_pdfparser](https://github.com/hedon-ai-road/deepdoc_pdfparser), [hoaivannguyen/deepdoc_vietocr](https://github.com/hoaivannguyen/deepdoc_vietocr)
- RAGFlow perf issues: [#5711](https://github.com/infiniflow/ragflow/issues/5711), [#11822](https://github.com/infiniflow/ragflow/issues/11822), [#8805](https://github.com/infiniflow/ragflow/issues/8805)
- [ONNX Runtime ROCm EP docs](https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html), [AMD Radeon native ONNX install](https://rocm.docs.amd.com/projects/radeon/en/latest/docs/install/native_linux/install-onnx.html)

### Dify plugin runtime
- [docs.dify.ai/en/plugins/introduction](https://docs.dify.ai/en/plugins/introduction), [datasource-plugin](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/datasource-plugin)
- [dify-plugin-daemon repo](https://github.com/langgenius/dify-plugin-daemon), [.env.example](https://github.com/langgenius/dify-plugin-daemon/blob/main/.env.example), [config.go](https://github.com/langgenius/dify-plugin-daemon/blob/main/internal/types/app/config.go)
- [dify-plugin-sdks](https://github.com/langgenius/dify-plugin-sdks), [pypi dify-plugin 0.8.0](https://pypi.org/project/dify-plugin/)
- [marketplace langgenius/mineru](https://marketplace.dify.ai/plugin/langgenius/mineru), [marketplace langgenius/llama_parse](https://marketplace.dify.ai/plugin/langgenius/llama_parse)
- [Knowledge Pipeline blog](https://dify.ai/blog/knowledge-pipeline-plugin-ecosystem-co-build-enterprise-grade-rag-with-global-partners), [Extension Plugin Endpoint blog](https://dify.ai/blog/extension-plugin-endpoint-bringing-serverless-flexibility-to-dify)
- Issues: [#26207 raise plugin size](https://github.com/langgenius/dify/discussions/26207), [#405 daemon 50MB cap](https://github.com/langgenius/dify-plugin-daemon/issues/405), [#18752 proxy env](https://github.com/langgenius/dify/issues/18752)

### Dify ↔ RAGFlow integration
- [Dify External Knowledge API](https://docs.dify.ai/en/use-dify/knowledge/external-knowledge-api), [Connect external KB](https://docs.dify.ai/en/use-dify/knowledge/connect-external-knowledge-base)
- [RAGFlow HTTP API ref](https://ragflow.io/docs/http_api_reference), [RAGFlow releases](https://github.com/infiniflow/ragflow/releases)
- Practical guide: [aisharenet.com/dify-v101waiguarag](https://aisharenet.com/en/dify-v101waiguarag/)
- Issues: [#33665 SourceMetadata](https://github.com/langgenius/dify/discussions/33665), [#11422 metadata loss](https://github.com/langgenius/dify/issues/11422), [#22561 N+1](https://github.com/langgenius/dify/issues/22561), [#24002 use_kg](https://github.com/langgenius/dify/issues/24002), [#9917 docker host](https://github.com/langgenius/dify/issues/9917)
- Tool-plugin alternative: [witmeng/ragflow-api](https://marketplace.dify.ai/plugin/witmeng/ragflow-api)
- RAGFlow 0.23 image/table context: [Medium @infiniflowai](https://medium.com/@infiniflowai/ragflow-0-23-0-advancing-memory-rag-and-agent-performance-e5901a853b09)

### Milvus 65k
- [Milvus VARCHAR docs](https://milvus.io/docs/string.md), [limitations](https://milvus.io/docs/limitations.md)
- [discussion #45653 max length tuning](https://github.com/milvus-io/milvus/discussions/45653), [issue #39818 TEXT type RFC](https://github.com/milvus-io/milvus/issues/39818), [PR #38883 configurable max length](https://github.com/milvus-io/milvus/pull/38883), [discussion #34478](https://github.com/milvus-io/milvus/discussions/34478)
- [Qdrant FAQ payload limits](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)
- [BAAI/bge-m3 HF](https://huggingface.co/BAAI/bge-m3) (8192 ctx + chunked > single)
- [LangChain RAG-on-tables benchmark](https://www.langchain.com/blog/benchmarking-rag-on-tables)

### Alt TSR parsers
- [conjuncts/gmft](https://github.com/conjuncts/gmft), [gmft docs](https://gmft.readthedocs.io/en/latest/usage.html), [gmft PyPI](https://pypi.org/project/gmft/)
- [microsoft/table-transformer](https://github.com/microsoft/table-transformer), [HF table-transformer-detection](https://huggingface.co/microsoft/table-transformer-detection)
- [RapidAI/RapidTable](https://github.com/RapidAI/RapidTable), [DeepWiki RapidTable](https://deepwiki.com/RapidAI/RapidTable/2.2-quick-start-guide)
- [PaddleOCR PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-StructureV3/PP-StructureV3.en.md), [Paddle2ONNX](https://paddlepaddle.github.io/PaddleOCR/main/en/version2.x/legacy/paddle2onnx.html)
- [IBM Granite-Docling 258M](https://huggingface.co/ibm-granite/granite-docling-258M), [announce](https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion)
- [datalab-to/marker](https://github.com/datalab-to/marker), [issue #875 perf](https://github.com/datalab-to/marker/issues/875)
- [Docling Tech Report arXiv](https://arxiv.org/html/2408.09869v4)
- [Multi-page table tools 2026](https://www.extend.ai/resources/multi-page-table-extraction-tools)
- [openpyxl merged_cells](https://gist.github.com/tchen/01d1d61a985190ff6b71fc14c45f95c9), [tablepyxl](https://github.com/martsberger/tablepyxl)
