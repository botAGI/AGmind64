# ADR-0002: Runtime compute backend abstraction (Vulkan / ROCm / CPU / NPU-stub)

- **Status:** accepted (shipped 2026-05-19 via Phase C/D + D1 HTTP)
- **Date:** 2026-05-19
- **Accepted:** 2026-05-19
- **Authors:** AGmind core team
- **Related:** ADR-0001 (миграция), `AGMIND_MIGRATION_SPEC.md` Part 1.2 + 1.4 + 5.3

## Контекст

AGmind после миграции на Strix Halo должен запускать LLM inference,
embeddings, rerank на разном железе:
- Reference: AMD Strix Halo (gfx1151) — два полезных backend'а:
  **Vulkan/RADV** (лучше tg по бенчам) и **ROCm/HIP** (лучше pp/batch).
- Secondary: обычные x86_64 без AMD GPU — fallback на **CPU**.
- Future: XDNA 2 NPU на Strix Halo — пока **stub** (RyzenAI-SW#366
  блокирует STX-H под Linux).

В одной кодовой базе нельзя хардкодить вызовы конкретного backend'а
(`import torch; tensor.to("cuda")` стиль) — это создаёт coupling и
делает невозможным CI matrix `backend ∈ {cpu, vulkan, rocm}`.

`AGMIND_MIGRATION_SPEC.md` Part 1.2 уже зафиксировал приоритет
бэкендов и порядок auto-select. Этот ADR формализует abstraction
интерфейс и runtime-выбор.

## Рассмотренные варианты

### A. Прямые вызовы конкретного backend'а в коде

```python
import llama_cpp
# или torch+ROCm, или onnxruntime-rocm
```

- **Плюсы:** меньше кода, проще читать в isolated context.
- **Минусы:** невозможно сменить backend через env-var, невозможно CI
  matrix, невозможно «один и тот же тест на 3 backends». Karpathy
  Recipe «verify loss @ init» провалится — нельзя проверить что
  CPU-fallback корректен.

### B. Adapter pattern с одним runtime check в каждой call-site

```python
def inference(...):
    if os.environ["AGMIND_BACKEND"] == "rocm":
        from torch_rocm import ...
    elif "vulkan":
        from llama_cpp_vulkan import ...
    else:
        from llama_cpp_cpu import ...
```

- **Плюсы:** проще ABC.
- **Минусы:** дублирование env-check во многих местах. Lazy-imports
  внутри branches трудно тестировать. Нет single source of truth для
  «какой backend сейчас».

### C. ABC-based runtime abstraction (`agmind/compute/`)

```python
from agmind.compute import get_backend
backend = get_backend()      # один раз, чтение AGMIND_BACKEND + auto-select
out = backend.load_llm(model_path).generate(prompt)
```

- **Плюсы:**
  - Single source of truth для текущего backend.
  - Lazy imports внутри конкретного backend модуля
    (`backends/vulkan.py` импортит llama-cpp с GGML_VULKAN — только если
    реально используется).
  - Contract tests параметризованные маркером `backend_*` — один тест
    три раза на CI.
  - Простой mock через `Backend` mock-класс.
- **Минусы:**
  - +1 indirection.
  - Закладывание ABC может породить «спекулятивный API» — нарушение
    Karpathy «simplicity first» если методы выбраны заранее.

### D. Plugin system с entry_points

```python
# pyproject.toml
[project.entry-points."agmind.backends"]
cpu = "agmind.compute.backends.cpu:CPUBackend"
vulkan = "agmind.compute.backends.vulkan:VulkanBackend"
rocm = "agmind.compute.backends.rocm:ROCmBackend"
```

- **Плюсы:** третьи стороны могут регистрировать новые backends.
- **Минусы:** YAGNI на этой стадии. Добавляем когда реально третий
  backend появится (а не сейчас).

## Решение

**Выбран вариант C: ABC-based runtime abstraction в `agmind/compute/`.**

Контракт ABC (`agmind/compute/base.py`, по `AGMIND_MIGRATION_SPEC.md`
Part 5.3) минимален и **расширяется по факту нужды** (Karpathy
«simplicity first»):

```python
class Backend(ABC):
    name: str  # "cpu" | "vulkan" | "rocm" | "npu"

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Можно ли использовать на текущей машине."""

    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def load_llm(self, model_path: str, **kwargs) -> "LLMHandle": ...

    @abstractmethod
    def embed(self, texts: list[str], model: str) -> "np.ndarray": ...

    # БОЛЬШЕ методов НЕ закладывать спекулятивно.
```

Auto-select правило (Part 1.2 + Part 2.5 D3 спеки):
- profile=`tg`, gfx1151 detected → `vulkan`
- profile=`pp`, gfx1151 detected → `rocm`
- profile=`mixed` → `vulkan` (default)
- no GPU → `cpu`

Env override: `AGMIND_BACKEND=cpu|vulkan|rocm|auto` (default `auto`).

Plugin система (D) — backlog, добавляется когда third-party backend
впервые появится (не сейчас).

## Последствия

### Положительные

- Один интерфейс, 4 реализации.
- CI matrix `{cpu, vulkan, rocm}` × `contract tests` — детерминированно.
- Тестируется в isolation через mock.
- Auto-select прозрачен (env var + heuristic).
- Lazy imports защищают от «не установлен rocm на машине разработчика».

### Отрицательные / технический долг

- ABC начально с 4 методами (available/device_info/load_llm/embed).
  Расширение требует добавления в base + 4 backends + контрактные тесты.
- Если backend нужно сменить mid-stream (LLM на vulkan, embed на rocm) —
  потребуется явное создание двух экземпляров. Это запланированная
  гибкость, не баг.

### Что нужно сделать

- [ ] PR-C1: `agmind/compute/base.py` (ABC)
- [ ] PR-C2: `agmind/compute/detect.py` (vulkaninfo/rocminfo/lspci)
- [ ] PR-C3: `agmind/compute/config.py` (env var reader)
- [ ] PR-C4: `agmind/compute/backends/cpu.py`
- [ ] PR-C5: `agmind/compute/backends/npu_stub.py`
- [ ] PR-C6: `agmind/compute/__init__.py` с auto-select логикой
- [ ] PR-C7: `tests/compute/test_contract.py` + `test_detect.py`
- [ ] PR-D1: `agmind/compute/backends/vulkan.py`
- [ ] PR-D2: `agmind/compute/backends/rocm.py`

## Update 2026-05-19 — engine selection внутри backend

После ресерчей R3 (llama.cpp), R4 (vLLM-ROCm), R5 (TEI/Infinity)
оказалось что **внутри** Vulkan и ROCm backends есть выбор inference
engine, не только compute pipeline. Это вторая ось абстракции.

Engines на gfx1151 в порядке приоритета:

**Vulkan backend:**
- **LlamaCppVulkanEngine** (primary) — llama-server, ~97 t/s decode на
  Qwen3-Coder 30B, наиболее зрелый
- MLC-LLM Vulkan — backlog (нет бенчей)

**ROCm backend:**
- **LlamaCppHIPEngine** (primary) — llama-server, ~48-51 t/s decode но
  ~986 t/s prefill (для long-context pp-bound, GDN-моделей)
- **VLLMROCmEngine** (M2 upgrade) — community fork patches (kyuz0/hec-ovi),
  TheRock nightlies, `--enforce-eager`. Используется когда нужны: tool
  calling, structured outputs, speculative decoding. Перформанс 20×
  медленнее llama.cpp на gfx1151.
- **InfinityEngine** (M2 для embed) — dynamic batching, OpenTelemetry.
  Для production embed workloads c high concurrency.

**Контракт расширенный:**

```python
class Backend(ABC):
    name: str
    engines: list[str] = []  # ["llama_cpp", "vllm", "infinity", ...]

    @classmethod
    @abstractmethod
    def available(cls) -> bool: ...

    @classmethod
    @abstractmethod
    def make(cls, engine: str = "auto") -> "Backend":
        """Factory: создать backend с конкретным engine.
        engine == 'auto' → выбрать по env + heuristic."""

    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def load_llm(self, model_path: str, **kwargs) -> "LLMHandle": ...

    @abstractmethod
    def embed(self, texts: list[str], model: str) -> "np.ndarray": ...

    @abstractmethod
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Rerank scores (новый метод, рекомендован после R5)."""
```

Auto-select правило обновляется (per R3/R4/R10):

```
gfx1151 detected:
  profile=tg → vulkan/llama_cpp
  profile=pp → rocm/llama_cpp + ROCWMMA_FATTN
  profile=mixed → vulkan/llama_cpp
  workload=tool_calling/structured → rocm/vllm  (M2, accept penalty)
  workload=embed_batch ≥ 4 → rocm/infinity   (M2)
  workload=embed_single → vulkan/llama_cpp (--pooling cls)
  model=GDN_family (Qwen3-Next) → rocm/llama_cpp (Vulkan shader missing)
  no GPU → cpu
```

Env override остаётся:
- `AGMIND_BACKEND=vulkan|rocm|cpu|auto`
- `AGMIND_ENGINE=llama_cpp|vllm|infinity|auto`

### Дополнительные PR для engine support (после core C/D):

- [ ] PR-E1.5: `agmind/compute/backends/vulkan/llama_cpp.py` (engine impl)
- [ ] PR-E1.6: `agmind/compute/backends/rocm/llama_cpp.py` (engine impl)
- [ ] PR-M2.1: `agmind/compute/backends/rocm/vllm.py` (tool calling M2)
- [ ] PR-M2.2: `agmind/compute/backends/rocm/infinity.py` (embed M2)

## Бенчмарки

Будут зафиксированы в `docs/BENCHMARKS.md` после фазы D — `tg/pp` на 7B,
30B, 70B моделях через CPU / Vulkan / ROCm.

## Откат

Если ABC окажется неудобным:
1. Деаннотировать `@abstractmethod`, оставить как «recommended interface».
2. Сделать `agmind.compute.get_backend()` опциональным — call-sites могут
   импортить backend напрямую.
3. Создать ADR-XXXX «relax compute abstraction».

## Ссылки

- `AGMIND_MIGRATION_SPEC.md` Part 1.2, 1.4, 5.3
- `docs/MIGRATION_PLAN.md` §2 (hot path), §4 (фазы C, D)
- Local migration research notes (simplicity-first backend boundary)
