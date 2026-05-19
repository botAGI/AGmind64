# ADR-0004: Engine selection inside compute backend

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** AGmind core team
- **Related:** ADR-0002 (compute abstraction), `R3-llama-cpp-vulkan-hip.md`,
  `R4-vllm-rocm-engines.md`

## Контекст

ADR-0002 определил Backend ABC + 4 backends (cpu/vulkan/rocm/npu_stub).
Но **внутри** backend есть выбор inference engine. Например, ROCm backend
может использовать:

1. `llama.cpp` HIP build (наша primary M1 — verified perf 50-100 t/s)
2. vLLM ROCm community fork (M2 — для tool calling, structured outputs)
3. Infinity (M2 — для production embed/rerank batching)
4. PyTorch + sentence-transformers (offline tooling, low concurrency)

Простой Backend.make(engine) factory с `engine ∈ {llama_cpp, vllm, infinity}`
не enough — нужен explicit decision matrix.

## Рассмотренные варианты

### A. Один backend = один engine (refactor split)

- ROCmLlamaCppBackend, ROCmVLLMBackend, ROCmInfinityBackend как
  separate classes.
- **Pro:** Простая семантика.
- **Con:** 4 backends × 3 engines = 12 classes. Cardinality explodes
  на каждом новом engine.

### B. Engine factory inside backend (selected design)

- ROCmBackend.make(engine="llama_cpp"|"vllm"|"infinity")
- Backend хранит engine choice, dispatches к correct _engines/X.py
- M1: only `llama_cpp` supported; vllm/infinity → `NotImplementedError("M2")`.

### C. Strategy pattern через registry

- `agmind.compute.engines` registry. Backend ничего не знает про engines.
- **Pro:** Plugin-friendly.
- **Con:** YAGNI на стадии M1 (только один engine реально работает).

## Решение

**Выбран вариант B**: factory-based engine selection inside Backend.

Реализация:

```python
class ROCmBackend(Backend):
    _SUPPORTED_ENGINES = ("llama_cpp",)  # M1
    _M2_ENGINES = frozenset({"vllm", "infinity"})

    @classmethod
    def make(cls, engine: str = "auto") -> "ROCmBackend":
        if engine in cls._M2_ENGINES:
            raise NotImplementedError(
                f"ROCm backend engine={engine!r} is planned for M2 upgrade."
            )
        if engine not in cls._SUPPORTED_ENGINES:
            raise ValueError(f"Engine {engine!r} not supported. M1 allowed: {cls._SUPPORTED_ENGINES}")
        return cls(engine=engine)
```

Selection rules (config-driven через `AGMIND_BACKEND_PROFILE` + workload
type) — см. `AGMIND_MIGRATION_SPEC.md::§1.2.6`.

## Последствия

### Положительные

- Backwards-compatible: добавление M2 engines = enum extension + new
  `_engines/X.py`, не refactor существующих backends.
- Single Backend instance per backend × engine choice.
- ABC unchanged: `Backend.make(engine)` returns Backend instance,
  caller doesn't know which engine inside.
- Clear M2 upgrade path: NotImplementedError → real impl.

### Отрицательные

- Каждый backend duplicates engine-check logic (но это 5-7 lines).
- Adding new engine = touch к 2 places (backend class + _engines/).
- Mitigation: `EXTENSION_POINTS.md::E.2` documents the process.

### Что нужно сделать

- [x] `agmind/compute/backends/{cpu,vulkan,rocm}.py` имеют `_SUPPORTED_ENGINES`
- [x] ROCmBackend `_M2_ENGINES` с NotImplementedError
- [x] `agmind/compute/backends/_engines/llama_server_handle.py` для HTTP
- [x] tests `tests/compute/test_engines.py`
- [ ] M2: `agmind/compute/backends/_engines/vllm_rocm.py` (community fork)
- [ ] M2: `agmind/compute/backends/_engines/infinity_rocm.py`

## Бенчмарки (R3 + R4 confirmed)

- llama.cpp HIP @ gfx1151: 50-100 t/s decode, 350-986 t/s prefill
- vLLM ROCm (community patched): ~4.3 t/s decode (V1 engine instability,
  must `--enforce-eager`) — **20× медленнее llama.cpp**
- Infinity ROCm @ gfx1100: 487 embeds/sec (Snowflake Arctic-M)

**Justifies M1 = llama_cpp only.** vLLM не оправдывает inefficiency для
chat. Infinity reasonable для high-concurrency embed.

## Откат

Если M2 engines окажутся unstable / unfit:
1. Stay на llama_cpp only forever
2. Remove `_M2_ENGINES` (становится simple ValueError)
3. Новый ADR documenting decision

## Ссылки

- R3-llama-cpp-vulkan-hip.md
- R4-vllm-rocm-engines.md
- R5-tei-embed-rerank.md (alternative embed engines)
- `agmind/compute/backends/{cpu,vulkan,rocm}.py::_SUPPORTED_ENGINES`
- `agmind/compute/backends/_engines/`
