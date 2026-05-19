# AGmind internal dependencies

Scanned 2026-05-19. **No circular imports detected.**

## Import graph (intra-agmind)

### Tier 0 — foundation (no internal imports)
- `agmind.log` — logging utilities. **9+ callers** (всё дерево использует).
- `agmind._env` — stdlib-only .env parser.
- `agmind.i18n` — minimal i18n (stdlib + JSON).
- `agmind.config.env` — render_env + write_env (stdlib only).
- `agmind.compute.base` — ABC (Backend, DeviceInfo, LLMHandle). **8+ callers**.
- `agmind.compute.config` — ComputeConfig + Profile enum.

### Tier 1 — detection / utilities
- `agmind.secrets` ← `agmind.log`
- `agmind.compute.detect` ← `agmind.log`  — **4 callers** (backends, models, doctor, agmind.compute via __init__).

### Tier 2 — orchestrators / engines
- `agmind.compute._registry` ← `compute.base`, `compute.config`, `log`
  - Lazy imports: backends (cpu/vulkan/rocm/npu_stub) on-demand
- `agmind.compute.backends.{cpu,vulkan,rocm}` ← `compute.base`, `compute.detect`, `log`
  - Lazy imports: `_engines/*` через `from agmind.compute.backends._engines.X import Y`
- `agmind.compute.backends.npu_stub` ← `compute.base`
- `agmind.compute.clients.llama_server` ← `log` (stdlib urllib only)
- `agmind.compute.backends._engines.llama_cpp_cpu` ← `compute.base`, `log`
  - Lazy imports: `llama_cpp` (только при load())
- `agmind.compute.backends._engines.llama_cpp_vulkan/hip` ← `compute.base`, `_engines.llama_cpp_cpu` (для shared helpers), `log`
- `agmind.compute.backends._engines.llama_server_handle` ← `compute.base`, `compute.clients`, `log`
- `agmind.compute.backends._engines.http_helper` ← `compute.base`, lazy `compute.config`, `compute.clients`
- `agmind.services.registry` ← `log` — **2 callers** (services.__init__, models)
- `agmind.models` ← `log`, `compute.detect`, `services.registry._parse_yaml`
- `agmind.cluster.peer` ← `log`
- `agmind.cluster.router` ← `cluster.peer`, `log`
- `agmind.diagnostics.doctor` ← `compute.detect`, `log`

### Tier 3 — CLI (leaf layer)
- `agmind.cli.__init__` ← `agmind.__version__`, `log` (lazy typer)
- `agmind.cli.models_cmd` ← `log`, `models`
- `agmind.cli.deploy_cmd` ← `log`
- `agmind.cli.chat_cmd` ← `log`, `compute.clients`
- `agmind.cli.embed_cmd` ← `compute.clients`
- `agmind.cli.audit` — wraps `subprocess scripts/audit_forbidden.py`

### Tier 4 — entry point
- `agmind.__main__` ← `agmind.cli.app`

## Cycle detection

**NONE** — graph ациклический. Foundation libs (`agmind.log`,
`agmind.compute.base`) на самом дне; CLI на вершине.

## External dependencies (pyproject.toml)

### Hard
- `numpy ≥2.0` — compute foundation
- `typer ≥0.12` — CLI (soft в практике; lazy в `cli/__init__.py` через try/except)
- `rich ≥13.7` — terminal output
- `questionary ≥2.0` — interactive prompts
- `platformdirs ≥4.2` — XDG paths
- `pydantic ≥2.7` — data validation

### Optional extras
- `cpu`: torch (CPU), onnxruntime, llama-cpp-python, scipy
- `vulkan`: llama-cpp-python (built с `GGML_VULKAN=ON`), scipy
- `rocm`: torch (ROCm index), onnxruntime, llama-cpp-python (`GGML_HIP=ON`), scipy
- `dev`: pytest+cov+benchmark, ruff, mypy, pre-commit, types-PyYAML

## Lazy imports (deferred)

- `llama_cpp` — в `_engines/llama_cpp_*.py` через `from llama_cpp import Llama` внутри методов
- `typer` — в `cli/__init__.py` через `try/except ImportError`
- `torch` — в engines (только если backend выбран)
- `onnxruntime` — то же
- `yaml` — в `services/registry.py::_parse_yaml` с fallback на bundled mini-parser
- `urllib.request` — в `clients/llama_server.py` (stdlib только)

## Refactor candidates (most-imported)

| Module | Caller count | Notes |
|--------|--------------|-------|
| `agmind.log` | 9+ | Well-isolated foundation. **No refactor needed.** |
| `agmind.compute.base` | 8+ | Stable ABC. Adding LLMHandle methods OK; removing — breaking. |
| `agmind.compute.detect` | 4 | Возможна extraction в `agmind.hardware` если другие проекты переиспользуют. |
| `agmind.services.registry` | 2 | `_parse_yaml` зашарен с `agmind.models` — выделить в `agmind.util.yaml`. |
| `agmind.cluster.peer` | 2 | Тонкий, стабилен. |

## Architecture patterns

1. **Lazy heavy deps** — backends/_engines не импортируются на module load,
   только при `backend.load_llm()`. Это позволяет import `agmind` без
   llama_cpp/torch установленных.
2. **Config-driven selection** — Profile enum + env vars drive backend +
   engine choice. Нет статической coupling backend↔engine.
3. **HTTP / in-process duality** — `LlamaServerHandle` (HTTP) и
   `LlamaCpp*Engine` (in-process) реализуют один LLMHandle ABC.
   Backend выбирает через `try_http_handle()` helper.
4. **YAML с fallback parser** — `services/registry.py::_parse_yaml` пробует
   PyYAML, если нет — bundled mini-parser. Это позволяет работать без
   `pip install pyyaml` (некритично для dev, важно для slim Dockerfile).
5. **stdlib HTTP** — `clients/llama_server.py` использует только urllib,
   без requests/httpx. Меньше surface, больше portable.

## High-confidence stable

- `agmind.log` — no need to refactor
- `agmind.compute.base` — ABC contract, не менять без `LLMHandle.chat()` semver bump
- `agmind.services.registry` — schema stable за исключением field additions

## Volatile (могут меняться)

- `agmind.cli.*` — будут добавляться commands в M2 (backup/upgrade/config)
- `agmind.models.py` — может добавиться model categories beyond LLM tiers (e.g. coder/chat/embed sub-trees)
- `agmind.cluster.peer.probe_peer` — async версия planned
