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

## Откат

Capability fields — backwards compat (default empty lists). Откат:

1. `git revert` Phase O commits — descriptors теряют provides/etc,
   но всё ещё валидны.
2. `compatibility.py` + `capability_bindings.py` — изолированные
   модули, удаление не ломает renderer (`inject_capability_env` тогда
   возвращает empty dict).
3. TUI `_check_compatibility` graceful degrade через try/except
   wrapper — если import не работает, validate проходит как раньше.
