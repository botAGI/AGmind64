# ADR-0011: Service Capability Graph (provides / conflicts_with / consumes)

- **Status:** accepted
- **Date:** 2026-05-20
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0005 (ServiceDescriptor), ADR-0006 (Python renderer +
  Traefik routing), Phase J wizard, Phase O
- **Driver:** user request: "подтяни логику зависимостей… RAGflow со своей
  БД, выбрать milvus вместо qdrant/weaviate и чтобы он был из коробки
  подтянут и в dify и в ragflow"

## Контекст

К концу Phase L/N у нас 33 service descriptor'а. Каждый знает свои
`depends_on` (что нужно), но **не знает** что **предоставляет** другим
сервисам и с кем **не совместим**:

- **RAGflow** и **Dify** — два различных RAG стека, нельзя одновременно
  (база `mysql` для ragflow vs `postgres` для Dify, конфликт сетевых
  имён через ENV).
- **qdrant**, **weaviate**, **milvus** — три vector DB. Один достаточен.
  Раньше user мог по ошибке выбрать все три → 3 запущенных vector DB,
  один реально используется.
- **traefik**, **caddy**, **nginx** — три reverse proxy. Аналогично.
- **Главное**: user выбрал milvus → Dify и RAGflow должны автоматически
  знать **MILVUS_URI**, **VECTOR_STORE=milvus**. Сейчас env vars
  hardcoded в descriptor → пересборка template при смене backend = боль.

Phase O вводит **capability graph** поверх service catalog для решения
обеих задач.

## Рассмотренные варианты

### A: оставить как есть; user должен знать что выбирать
- ➖ Невозможно реализовать "выбери milvus и оно автоматически попадёт
  в Dify+RAGflow"
- ➖ Detection конфликтов руками = пользователь видит compose conflict
  только когда docker compose up уже сломался

### B: жёсткие service "стеки" (predefined bundles)
- ➕ Простая mental model: "выбери RAG stack: ragflow-stack | dify-stack"
- ➖ Не масштабируется: добавление gpt-oss-stack, или milvus vs qdrant
  ⇒ комбинаторный взрыв bundles
- ➖ Теряем гибкость per-service selection (Phase J.1.8)

### C: capability graph (выбран)
- ➕ Декларативные provides/conflicts_with/consumes на каждом descriptor
- ➕ Renderer резолвит provider per capability на этапе render →
  injects env vars consumer'у
- ➕ Conflict detection trivial: пересечение selected × conflicts_with
- ➕ Backwards compat: capabilities optional, старые descriptors просто
  с пустыми lists
- ➖ Требует mapping table (capability_bindings.BINDINGS) — это knowledge
  что dify ждёт VECTOR_STORE+MILVUS_URI, ragflow ждёт DOC_ENGINE+MILVUS_URI.
  Поддерживать вручную при добавлении новых stack'ов.

## Решение

Вариант **C** с двумя слоями:

### O.A — ServiceDescriptor extension

Schema (`agmind/schemas/service.py`):

```python
class ServiceDescriptor(BaseModel):
    ...
    provides: list[str]        # capability tags, e.g. ['vector_db']
    conflicts_with: list[str]  # incompatible service names
    consumes: list[str]        # capabilities этот сервис uses
```

Annotated 18 production descriptors:

| Service          | provides                          | conflicts_with        | consumes                              |
|------------------|------------------------------------|------------------------|---------------------------------------|
| qdrant           | `vector_db`                        | weaviate, milvus       | —                                     |
| weaviate         | `vector_db`                        | qdrant, milvus         | —                                     |
| milvus           | `vector_db`                        | qdrant, weaviate       | —                                     |
| traefik          | `reverse_proxy`, `tls_termination` | caddy, nginx           | —                                     |
| caddy            | `reverse_proxy`, `tls_termination` | traefik, nginx         | —                                     |
| nginx            | `reverse_proxy`                    | traefik, caddy         | —                                     |
| ragflow          | `rag_stack`                        | dify-* (5 services)    | llm_inference, embedding, vector_db   |
| dify-api/web/... | `dify_stack`                       | ragflow                | llm_inference, embedding, vector_db   |
| llama-llm        | `llm_inference`                    | —                      | —                                     |
| llama-embed      | `embedding_inference`              | —                      | —                                     |
| llama-rerank     | `reranker`                         | —                      | —                                     |
| openwebui        | —                                  | —                      | llm_inference                         |
| postgres / mysql / redis / minio / elasticsearch | postgres_db / mysql_db / redis_cache / object_storage / search_index | — | — |

### O.A.checker — agmind/services/compatibility.py

```python
@dataclass
class CompatIssue:
    severity: 'error' | 'warning'
    kind: 'conflict' | 'redundant_provider' | 'missing_capability'
    services: tuple[str, ...]
    capability: str | None
    message: str

def check_service_compatibility(selected) -> CompatReport: ...
def resolve_capability_provider(selected, capability) -> str | None: ...
```

Три категории issues:
- **error · conflict**: A.conflicts_with[B] и оба выбраны — блокирует Apply
- **warning · redundant_provider**: 2+ сервисов с одной capability —
  показываем warning, не блокируем
- **warning · missing_capability**: consumer без provider — informational

### O.B — capability_bindings + injection в renderer

`agmind/services/capability_bindings.py`:

```python
BINDINGS = {
    'vector_db': {
        'milvus': {
            'dify-api':  {'VECTOR_STORE': 'milvus',  'MILVUS_URI': 'http://milvus:19530'},
            'dify-worker': {'VECTOR_STORE': 'milvus', 'MILVUS_URI': 'http://milvus:19530'},
            'ragflow':   {'DOC_ENGINE':   'milvus', 'MILVUS_URI': 'http://milvus:19530'},
        },
        'qdrant':  { 'dify-api': {...}, 'ragflow': {...} },
        'weaviate': { 'dify-api': {...}, 'ragflow': {...} },
        'elasticsearch': { 'ragflow': {...} },  # ragflow's default
    },
    'llm_inference': { 'llama-llm': { 'dify-api': {...}, 'openwebui': {...}, 'ragflow': {...} } },
    'embedding_inference': { 'llama-embed': { 'dify-api': {...}, 'ragflow': {...} } },
    'reranker': { 'llama-rerank': { 'dify-api': {...}, 'ragflow': {...} } },
}
```

`agmind/services/renderer.py::inject_capability_env(selected)` walks
consumers, resolves provider per capability через
`resolve_capability_provider`, lookup table, returns
`{consumer_name: {ENV_KEY: value}}`.

`render_compose` merges injection в `services[consumer].environment`,
**respect existing manual override** (setdefault — не перетирает).

### TUI integration

`setup_wizard.py::_check_compatibility()` calls
`check_service_compatibility` после Preview / before Apply.
- **error** severity → `_validate` добавляет в errors → Apply отвергается.
- **warning** severity → отображается в `#status-msg` под формой.

## Последствия

### Положительные

- **User-asked scenario работает**: выбрал ragflow + milvus + llama-* →
  render автоматически injects `DOC_ENGINE=milvus` + `MILVUS_URI=...` в
  ragflow env. Test `test_real_catalog_milvus_injects_into_ragflow`
  проверяет это.
- **Hard conflicts блокируются раньше**: traefik + caddy = ошибка в
  wizard, не silent поломка docker compose up.
- **Redundancy detected**: qdrant + weaviate + milvus = warning, user
  видит почему 3 vector DB лишние.
- **Decoupled mapping**: добавить новый vector DB (например chroma) =
  добавить ChromaDescriptor с `provides=['vector_db']` + entries в
  BINDINGS[vector_db][chroma]. Никаких изменений в Dify / RAGflow
  descriptors.

### Отрицательные / технический долг

- **BINDINGS table maintain руками**. При добавлении новой capability
  (e.g. observability metrics) или нового consumer'а (e.g. n8n) —
  расширить таблицу. Mitigation: tests `test_bindings_have_vector_db_entries`
  + reality-check через `test_real_catalog_*`.
- **Provider resolution с tie-break alphabetical**. Если user умудрился
  выбрать qdrant + milvus одновременно (warning, не error) — в Dify
  пойдёт milvus (m < q). Доминанта документирована в docstring
  `resolve_capability_provider`.
- **Env var values hardcoded в BINDINGS** (e.g. `'http://milvus:19530'`).
  Если port в milvus.yaml поменяется — нужно sync. Mitigation: на этапе
  smoke test caught (compose не стартует если env != actual port).
- **Manual override priority**: если user в descriptor вписал
  `env: VECTOR_STORE: weaviate`, capability injection не перепишет.
  Это **намеренно** (advanced user может тестить override), но может
  путать тех кто не знает.

### Что нужно сделать

- [x] O.A.1: schema extended (`provides`, `conflicts_with`, `consumes`)
- [x] O.A.2: 18 descriptors annotated
- [x] O.A.3: `compatibility.py` with `check_service_compatibility` +
      `resolve_capability_provider`
- [x] O.B.1: `capability_bindings.py` BINDINGS table
- [x] O.B.2: `inject_capability_env` в renderer, merged в compose YAML
- [x] O.A.4 + O.B.3: TUI wizard wired (_check_compatibility →
      _validate + Preview status)
- [x] O.5: 24 unit tests + this ADR
- [ ] O.6 (future): добавить **chroma**, **pgvector** в catalog
- [ ] O.7 (future): JSON Schema для capability list (validate provides
      против известного vocabulary)
- [ ] O.8 (future): rich `_render_compat_panel` в TUI (отдельная Static
      секция с per-capability resolution table)

## Amendment 2026-05-20 — research-based correction

Изначальная версия этого ADR содержала выдуманные конфликты, не основанные
на research. User указал на это явно ("ты ебанулся? RAGflow и Dify даже
имеют плагин по API чтобы совместить"). Post-research correction:

### Выдуманные conflicts (removed)

| Pair                       | Фейк ADR утверждал | Реальность (verified)                                         |
|----------------------------|---------------------|---------------------------------------------------------------|
| ragflow ⟂ dify-*           | "не сосуществуют"   | [marketplace.dify.ai/plugin/witmeng/ragflow-api](https://marketplace.dify.ai/plugin/witmeng/ragflow-api) — официальный plugin для интеграции |
| qdrant ⟂ weaviate ⟂ milvus | "fatal conflict"     | Разные ports (6333/8080/19530), могут coexist для разных consumer'ов |
| traefik ⟂ caddy ⟂ nginx    | "fatal conflict"     | Port-level conflict ТОЛЬКО при mapping 80/443 у обоих наружу — это deploy concern, не service |

**Все три convicted как fakes.** `conflicts_with` field оставлен в schema
для backward compat но **никем не заполнен** в production descriptors.
Compat checker больше не emit'ит `severity='error'` — только soft warnings.

### Verified env vars (replaces fake earlier values)

**Dify** (per [docs.dify.ai env reference](https://docs.dify.ai/getting-started/install-self-hosted/environments)):
- `VECTOR_STORE` ∈ {qdrant, milvus, weaviate, pgvector, chroma, opensearch, oracle, +20 more}
- Per-backend keys: `QDRANT_URL`, `MILVUS_URI`+`MILVUS_TOKEN`,
  `WEAVIATE_ENDPOINT`+`WEAVIATE_API_KEY`, etc.

**RAGFlow** (per [github.com/infiniflow/ragflow/blob/main/docker/.env](https://github.com/infiniflow/ragflow/blob/main/docker/.env)):
- `DOC_ENGINE` ∈ {**elasticsearch, infinity, oceanbase, opensearch, seekdb**}
- **RAGFlow НЕ supports milvus/qdrant/weaviate** как DOC_ENGINE — это
  Dify-only options. Раньше я писал что user может "выбрать milvus и он
  попадёт в ragflow" — **это было невозможно**.

### Capability graph (corrected)

```
                    ┌───────────────┐
                    │   llama-llm   │ provides: llm_inference
                    └───┬─────┬─────┘ (внутренний port 8080,
                        │     │       host 8080 — публикация)
        ┌───────────────┘     └─────────────┐
        ▼                                   ▼
  ┌──────────┐                       ┌────────────┐
  │ dify-api │                       │  ragflow   │
  └────┬─────┘                       └──────┬─────┘
       │ consumes: vector_db ←   │   ←consumes: search_index
       │  → BINDINGS[vector_db]  │     → BINDINGS[search_index]
       ▼                         │       ▼
  ┌────────────────────────┐     │    ┌─────────────────┐
  │ qdrant/milvus/weaviate │     │    │ elasticsearch / │
  └────────────────────────┘     │    │ infinity / etc. │
                                 │    └─────────────────┘
                                 │
                consumes: dify_external_kb
                provided by ragflow → BINDINGS[dify_external_kb][ragflow][dify-api]
                = RAGFLOW_API_ENDPOINT=http://ragflow:9380/api/v1
```

### Pin updates (verified via registry manifest probe 2026-05-20)

- `infiniflow/ragflow:v0.25.4` → **`v0.25.5`** (released 2026-05-20,
  digest `sha256:1025603bd79a373ab0f65e8ee3730710a1bccfb2ba88fd443d57078ebbf24724`)
- `langgenius/dify-api:1.14.2` — **already latest** per github releases
  (v1.14.2 from 2025-05-19, no newer)

### Lessons (для будущих ADRs)

1. **WebFetch GitHub UI lies** — ghcr.io packages page показал b9246 tag
  который физически не существует в registry. Same trap: github.com releases
  pages могут показывать old tags. Always verify через registry manifest API.
2. **Не выдумывай conflicts** — две opensource системы которые часто упоминают
  вместе обычно интегрируются. Перед declaring "fatal" — `WebSearch "$A $B
  integration"` и поискать plugin marketplaces.
3. **Capability bindings — verifiable knowledge**. Env var keys должны
  цитировать конкретные docs/source URLs. Mock test проверяет что keys
  совпадают с upstream defaults.

### Что было исправлено (commit log)

1. `conflicts_with` снят со всех 23 descriptors
2. `compatibility.py`: removed "error · conflict" emission
3. `capability_bindings.py`: rewrite с verified env keys, drop fake
   milvus/ragflow binding, add `dify_external_kb` (ragflow → dify-api)
4. Ports fix: внутри docker network все llama-* = `:8080` (host ports
   8080/8081/8082 — это публикация на 127.0.0.1)
5. `ragflow.yaml`: pin v0.25.4 → v0.25.5, `provides=['rag_stack',
   'dify_external_kb']`, `consumes=['llm_inference', 'embedding_inference',
   'search_index']`
6. `dify-api.yaml`: добавил `consumes=['dify_external_kb', ...]`
7. Tests rewritten: 24 passing с правильной моделью, в т.ч.
   `test_real_catalog_ragflow_and_dify_coexist`,
   `test_real_catalog_ragflow_dify_integration_env_injected`,
   `test_real_catalog_milvus_does_not_inject_into_ragflow`.

## Откат

Capability fields — backwards compat (default empty lists). Откат:

1. `git revert` Phase O commits — descriptors теряют provides/etc,
   но всё ещё валидны.
2. `compatibility.py` + `capability_bindings.py` — изолированные
   модули, удаление не ломает renderer (`inject_capability_env` тогда
   возвращает empty dict).
3. TUI `_check_compatibility` graceful degrade через try/except
   wrapper — если import не работает, validate проходит как раньше.
