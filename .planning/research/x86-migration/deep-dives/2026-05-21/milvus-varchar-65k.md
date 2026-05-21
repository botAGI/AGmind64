# R18 — Milvus 2.6 VARCHAR 65k limit и длинные таблицы в RAG

Дата: 2026-05-21
Контекст: AGmindx86, Milvus v2.6.17. Юзер репортит "milvus принимает только
65k а таблица 138k". Документ — одна HTML-таблица 138 000 символов, резать
семантически нельзя.

## 1. VARCHAR лимит в Milvus 2.6 — ТОЧНЫЕ ЦИФРЫ

- `max_length` параметр VARCHAR-поля: **диапазон 1 … 65 535**.
  Единица измерения — **байты UTF-8**, НЕ символы. Кириллица в UTF-8 это
  2 байта/символ, китайский/эмодзи 3-4 байта. Для CP1251-русского
  138 000 символов в UTF-8 ≈ 276 000 байт, что в **4.2×** превышает
  лимит. Для ASCII-таблицы 138 000 байт всё равно > 65 535.
- Документация: `len(bytes(str, encoding='utf-8'))` — именно эту длину
  Milvus сравнивает с `max_length`. Источник: docs/string.md и FAQ.
- `max_length` **нельзя изменить после создания коллекции** (Disc #34478).
- Жёсткий лимит на уровне SDK — это default. **Можно поднять** через
  server-side config:

  ```yaml
  proxy:
    maxVarCharLengthBytes: 1048576   # 1 MiB, до 512KB-1MB безопасно
  ```

  Параметр был добавлен PR #38883 (merged в Milvus 2.5, январь 2025),
  default в 2.5.x = **1 MiB**. В 2.6.x наследуется.
- Trade-offs при подъёме (прямо от maintainers, Disc #45653):
  - segment size раздувается (500k rows × 1 MiB = 500 GB segment);
  - rewriting/reorganizing сегментов жрёт ресурсы;
  - load segment latency растёт unacceptable.
- **Вывод**: технически 138 000 байт пройдёт, если поднять
  `proxy.maxVarCharLengthBytes` до 262144 или выше. Но это анти-паттерн
  по архитектуре Milvus.

Ссылки:
- https://milvus.io/docs/string.md
- https://milvus.io/docs/limitations.md
- https://github.com/milvus-io/milvus/pull/38883
- https://github.com/milvus-io/milvus/discussions/45653
- https://github.com/milvus-io/milvus/discussions/34478

## 2. JSON field — тот же тупик

- Лимит на JSON-документ в одном поле = **65 536 байт** (Disc #24715).
  По сути та же VARCHAR-инфраструктура под капотом.
- 138 000 символов в JSON-поле НЕ влезут без подъёма того же
  `maxVarCharLengthBytes`.
- Источник: https://github.com/milvus-io/milvus/discussions/24715,
  https://milvus.io/docs/json-field-overview.md

## 3. TEXT / long-text тип — RFC, не GA

- Milvus 2.5 (декабрь 2024) добавил **Full-Text Search** через
  `Function(BM25)` и `SPARSE_FLOAT_VECTOR`. Текст всё равно хранится в
  VARCHAR с `enable_analyzer=True` — **тот же лимит 65 535 байт**.
  Назначение: keyword + dense hybrid retrieval, не storage для длинных
  документов.
- **TEXT data type** (issue #39818, открыт 2025-02-12) — отдельный план:
  - до **10 MB на поле**;
  - без сортировки/индексации, только storage + text-match + FTS;
  - planned for Milvus **3.0**, на 2026-05-21 ещё **не GA**.
- В 2.6.17 нативного long-text типа НЕТ.

Ссылки:
- https://milvus.io/blog/introduce-milvus-2-5-full-text-search-powerful-metadata-filtering-and-more.md
- https://github.com/milvus-io/milvus/issues/39818

## 4. Best practice для длинных документов (Milvus team)

Прямой совет maintainers в Disc #34478 и #45653:

> "Don't stuff full documents into VARCHAR. Store vector + short
> metadata + chunk_id, keep the raw blob in object store (MinIO/S3),
> fetch by id on retrieval."

Конкретный паттерн для AGmindx86:

| Поле               | Тип                  | Назначение                               |
|--------------------|----------------------|------------------------------------------|
| `id`               | INT64 / VARCHAR(64)  | PK                                       |
| `embedding`        | FLOAT_VECTOR(1024)   | dense-вектор chunk-summary               |
| `sparse`           | SPARSE_FLOAT_VECTOR  | BM25 от chunk-text (для hybrid)          |
| `chunk_text`       | VARCHAR(8192)        | сам chunk (≤ embedding context)          |
| `doc_id`           | VARCHAR(64)          | ссылка на полный документ                |
| `chunk_index`      | INT32                | порядковый номер chunk внутри doc        |
| `metadata`         | JSON(< 64 KB)        | headers, page, row-range, source         |
| `raw_blob_uri`     | VARCHAR(512)         | s3://bucket/doc_id/table.html            |

Полная таблица 138 KB ложится в MinIO как объект, в Milvus только
векторизованные чанки + reference. Это и есть Milvus reference
storage-compute decoupling (см. docs/object_storage_operator.md).

Можно ли хранить 138k как один VARCHAR(262144) chunk? Технически да
(после bump config), но: один chunk → один эмбеддинг bge-m3 (8192
токенов context), 138k символов = ~25k-40k токенов, **обрежется в
эмбеддинге**, retrieval будет работать только по первым ~8k токенам.
Это сломает RAG. См. секцию 7.

## 5. RAG best practices для больших таблиц (2025-2026)

### LangChain — multi-vector retriever (бенчмарк blog post)

- **Не резать таблицу row-by-row** — структура и заголовки потеряются.
- Pattern (рекомендация LangChain team):
  1. LLM генерирует **summary** таблицы (что в ней, каких колонок, range
     значений) → embedding from summary.
  2. Оригинальная таблица целиком в **docstore** (MinIO/Postgres bytea).
  3. Multi-vector retriever: vector над summary → возврат **родительского
     блоба** (исходный HTML).
- Page-based splitting > naive token chunking для таблиц: "many tables
  respect page boundaries". Но для 138k single-table не поможет.

### Unstructured library — chunk_by_title strategy

- Table element **никогда не комбинируется** с другими элементами,
  структура сохраняется как HTML.
- Если table > `max_characters` (hard limit), она остаётся **одним
  chunk** — Unstructured не режет таблицу.
- Известный bug: title таблицы и content уходят в разные chunks
  (issue #3012, актуален). Обходится через parent_id linking.

### Table-aware chunking (ensemble pattern)

Современный RAG-2026 stack для таблиц:

1. **Row-group chunking**: N строк (например, 10-20) + shared header в
   каждом chunk. Сохраняет колоночный контекст.
2. **Hierarchical**: vector над table-summary (родитель) + vector над
   row-group (child), retrieval по child → возврат parent context.
3. **Hybrid scoring**: BM25 (Milvus 2.5+ sparse) для keyword (числа,
   названия колонок) + dense для semantics; для таблиц BM25 обычно
   выигрывает.
4. Citation: каждый row-group chunk хранит `row_start..row_end` в
   metadata + `doc_id` → click-through к оригиналу.

Ссылки:
- https://www.langchain.com/blog/benchmarking-rag-on-tables
- https://docs.unstructured.io/open-source/core-functionality/chunking
- https://unstructured.io/blog/preserving-table-structure-for-better-retrieval
- https://weaviate.io/blog/chunking-strategies-for-rag

## 6. Альтернативы Milvus — лимиты

| База     | Hard limit per record/payload                | Long-text стратегия                          |
|----------|----------------------------------------------|----------------------------------------------|
| Milvus 2.6 | VARCHAR 65 535 B default, до 1 MiB через config | TEXT(10MB) в 3.0 (не GA)                    |
| Qdrant   | **33 554 432 B (32 MiB) per point JSON payload** | `on_disk_payload: true`, no-RAM storage    |
| Weaviate | нет жёсткого byte-лимита, упирается в vectorizer-context + RAM | не документирован hard limit          |
| pgvector | Postgres TEXT/bytea до **1 GB**              | natively no problem                          |

- Qdrant: 32 MiB / point — комфортно для 138 KB. Maintainers всё равно
  предупреждают "don't stuff PDFs in payload, store id + snippet".
- Weaviate: text property без hard byte limit, но vectorizer-token (8192
  у bge) всё равно ограничивает осмысленное embedding.
- pgvector: 1 GB / row, проблем с 138 KB нет, но vector-index slower на
  large-scale (R11).

Для AGmindx86, по R11 склон к Qdrant — это решит storage-side. Но
embedding-side (bge-m3 8192 токенов) остаётся ботлнэком — см. секцию 7.

Ссылки:
- https://qdrant.tech/documentation/faq/qdrant-fundamentals/
- https://github.com/orgs/qdrant/discussions/3934
- https://qdrant.tech/documentation/manage-data/storage/

## 7. Embedding bottleneck — bge-m3 ≠ решение

- bge-m3 context = **8192 tokens** (BAAI doc).
- 138 000 символов ≈ 25 000-40 000 токенов (зависит от языка/таблицы) —
  это **3-5× больше окна**, эмбеддер просто отрежет хвост.
- BAAI собственными бенчами показал: даже на 8192-context single
  embedding **проигрывает chunked (512 tokens) embeddings** по MRR@K на
  long docs. Их recommendation: **chunk 512 tokens**.
- Вывод: даже если Milvus поднять до 1 MB и положить таблицу одним
  chunk — vector retrieval работать корректно НЕ будет. Резать
  обязательно. Вопрос только в стратегии.

Ссылки:
- https://huggingface.co/BAAI/bge-m3
- https://saeedesmaili.com/notes/to-chunk-or-not-to-chunk-with-the-long-context-single-embedding-models/

## 8. Citation для chunked-таблиц

Рабочие схемы (papers/blogs 2025-2026):

1. **Cell-level citation**: каждый chunk = N rows table, metadata
   `{table_id, row_start, row_end, headers}`. На LLM-ответе модель
   возвращает row-range, фронт рисует подсветку оригинала.
2. **Multi-vector parent retrieval**: child-chunk (row-group) → vector;
   при retrieval LLM получает parent (full HTML table) + child-pointer.
   Citation ссылается на full table, контекст полный.
3. **Hybrid с BM25**: для числовых/именованных запросов BM25 находит
   точный row, dense — семантически близкие. Score-blended ranking,
   citation на оба источника.

## ИТОГОВАЯ РЕКОМЕНДАЦИЯ ДЛЯ AGmindx86

Проблема "138k > 65k" решается НЕ подъёмом `maxVarCharLengthBytes` (это
анти-паттерн), а правильным schema-паттерном:

1. Хранить таблицу целиком в **MinIO** (s3://agmind/tables/<doc_id>.html).
2. Резать таблицу на **row-groups по ~512 токенов с shared header**.
3. В Milvus коллекция:
   - `embedding` (bge-m3 dense 1024d, от row-group text);
   - `sparse` (BM25 от row-group, для hybrid — Milvus 2.5+ native);
   - `chunk_text` VARCHAR(8192) — текст row-group;
   - `metadata` JSON — `{doc_id, table_id, row_start, row_end, headers,
     blob_uri}`.
4. Retrieval: hybrid (dense + sparse) → top-K row-groups → если нужен
   полный контекст, подгрузка table.html из MinIO по `blob_uri`.
5. Citation: row-range в metadata → клик ведёт в подсвеченный фрагмент
   оригинала.

`proxy.maxVarCharLengthBytes` ОСТАВИТЬ default (65535) — нет ни одной
причины его трогать в этом юзкейсе.

Если в roadmap Milvus 3.0 / TEXT(10MB) подъедет до GA — пересмотреть,
но это **2026-H2 как минимум**, и всё равно не отменит chunking-need
из-за bge-m3 8192-context.
