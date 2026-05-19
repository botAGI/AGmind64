# AGmind Migration Spec & GSD Plan
## GB10 (aarch64+CUDA) → x86-64 / AMD Strix Halo

> **Этот файл — рабочая спека миграции.**
> Источник правды для технических решений; меняется по мере накопления
> фактов из ресерчей. Каждое изменение должно быть подкреплено recon
> отчётом в `.planning/research/x86-migration/`.
> Claude Code: читай его целиком в начале каждой сессии.
> Любое архитектурное решение — фиксировать новым ADR (`docs/adr/NNNN-*.md`).

> **Дата последнего ревью:** 2026-05-19 (обновлено после R1-R11 recons).
> Раздел «Compute backends» пересматривать раз в квартал — ROCm/Vulkan
> ландшафт на gfx1151 быстро меняется.
>
> **Changelog:**
> - 2026-05-18: первая версия (drafted)
> - 2026-05-19: применены recon R1-R11. Compute backends pinned к
>   конкретным версиям. Добавлены selection rules. Удалена/переписана
>   часть запретов вокруг inference engines (TEI broken на gfx1151, vLLM
>   деградирует, SGLang/MLC non-viable). Файловая структура расширена
>   engine-selection слоем.
> - 2026-05-19 (Phase C/D): отгружен `agmind/compute/` skeleton — Backend
>   ABC + 4 backends (cpu/vulkan/rocm/npu_stub) + lazy registry + detect
>   через vulkaninfo/rocminfo/lspci. Contract tests параметризованы по
>   backend.
> - 2026-05-19 (Phase D1 — real LLMHandle): `agmind/compute/clients/llama_server.py`
>   (~450 LOC urllib REST + SSE streaming, без httpx/requests dep);
>   LLMHandle ABC расширен `chat/generate_stream/chat_stream`;
>   `_engines/llama_server_handle.py` обёртка. +48 тестов.
> - 2026-05-19 (Phase E + E2): `agmind/cli/` через typer (lazy import) —
>   17+ subcommands: doctor/secrets/i18n/models/deploy/chat/embed.
>   diagnostics/doctor.py: 9 preflight checks (BIOS UMA, GTT pool,
>   AMDVLK absence, Mesa version, ROCm minimums, kernel HWE, render+video
>   groups, llama.cpp build, model cache).
> - 2026-05-19 (Phase E3 — multi-node): `agmind/cluster/{peer,router}.py`
>   с 4 стратегиями (round-robin / least-loaded / sticky-session / random).
>   Ansible role `cluster/` + inventory `cluster.yml`. 17 tests.
> - 2026-05-19 (Phase F): 4 Docker images (base/cpu/vulkan/rocm), CI
>   workflow, pre-commit. **TODO:** REPLACE_WITH_DIGEST placeholders в
>   base/rocm Dockerfile не разрешены.
> - 2026-05-19 (Phase G + ADR): docs/ guides + ADR-0001/0002 accepted +
>   ADR-0003 (GTT-based memory budgeting на Strix Halo, BIOS UMA = min) +
>   ADR-0004 (engine selection inside backend factory: M1=llama_cpp,
>   M2=vllm/infinity NotImplementedError).
> - 2026-05-19 (Phase B + cleanup): legacy/gb10/ как quarantine с
>   deprecation policy до 2027-Q1; legacy bash trees (`_food_staging/`,
>   `scripts/*.sh`, старый `install.sh`) перенесены. Audit 0 violations.
>   **BLK-LEGACY-RM:** physical `rm -rf legacy/` блокируется classifier'ом,
>   ждёт user terminal.
> - 2026-05-19 (GSD planning): `.planning/` дерево — PROJECT, REQUIREMENTS
>   (119 REQ-IDs), STATE, ROADMAP, BACKLOG (Phase H/I/J/K) +
>   codebase/{INDEX,DEPENDENCIES,ARCHITECTURE,PITFALLS,INVARIANTS,EXTENSION_POINTS}.md.
>   `migration_progress.json` reconciled: post-G, milestone v0.1.0-dev.
> - 2026-05-19 (deferred to next session): Phase H (hardware validation
>   on real Strix Halo), Phase I (git baseline + initial commit), Phase J
>   (day-2 ops CLI), Phase K (observability — Grafana/Prometheus/alerts).

---

# Часть 1. Правила (HARD RULES)

## 1.1. Целевая платформа

- **Primary:** `linux/amd64`, baseline `-march=x86-64-v3`, opt-in `-march=znver5`.
- **Reference hardware:** AMD Ryzen AI Max+ 395 «Strix Halo» — Zen 5 (16C/32T) + Radeon 8060S (gfx1151, RDNA 3.5, 40 CU) + 128 GB unified LPDDR5X.
- **Secondary:** обычные x86-64 серверы (Zen 4 / Ice Lake / Sapphire Rapids) — должно собираться и проходить тесты с CPU-бэкендом.
- **Откуда мигрируем:** NVIDIA GB10 (Grace Blackwell, `linux/arm64` + CUDA SBSA).
- **Универсальность** = собирается на любой x86-64 Linux машине без специальных драйверов; ускорение — опционально и за абстракцией.

## 1.2. Compute backends — порядок приоритета

Бэкенды выбираются runtime'ом через `agmind.compute.get_backend()`.
**Не хардкодить вызовы конкретного бэкенда нигде, кроме `agmind/compute/backends/`.**
Внутри backend есть выбор engine — `agmind.compute.get_backend().engine`
(см. §1.2.5).

### 1.2.1 Vulkan (RADV) — primary

На gfx1151 Vulkan RADV объективно быстрее HIP/ROCm на decode и
prefill коротких контекстов. Бенчмарки май-2026 (llama.cpp ≥ b8765,
рекомендуется b9049):

| Tier | Model | Quant | pp t/s | tg t/s | Source |
|------|-------|-------|-------:|-------:|--------|
| S | Llama 2 7B (smoke) | Q4_0 | 881 | 52.8 | kyuz0 |
| M | gemma-4-26B-A4B-it | UD-Q4_K_M | 1196 | 52.9 | slb350 |
| L | Qwen3.6-35B-A3B (MoE) | UD-Q4_K_XL | 1029 | 60 | slb350 |
| L | Qwen3.6-35B-A3B Strix DYNAMIC | DYNAMIC | 1100 | 64 | 0xSero |
| L | Qwen3-Coder 30B (b9049) | UD-Q4_K_XL | 1321 | 96.8 | hogeheer499 |
| XL | GPT-OSS 120B | MXFP4_MOE | 339 | 49 | R3, blog.yifei.sg |
| XL | Qwen3-Coder-Next 80B-A3B | UD-Q4_K_XL | 531 | 42.7 | visorcraft |
| XXL | MiniMax M2.5 228B | Q3_K_M | 156 | 32.8 | visorcraft |

Полный inventory в `templates/models.yaml`. Tier autodetect по RAM —
`agmind.models.detect_tier()`.

**Mandatory:**
- `AMD_VULKAN_ICD=RADV`
- `VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json`
  (надёжнее `AMD_VULKAN_ICD` — последний игнорится некоторыми loader'ами,
  см. GPUOpen-Drivers/AMDVLK#222)
- Mesa **≥ 25.2.8** (recommended 26.0+ через `ppa:kisak/kisak-mesa`)
- AMDVLK **запрещён** — discontinued AMD 2025-09-15; имеет hard 2 GiB
  cap на `VkDeviceMemory` который ломает LLM ≥30B dense. Удалять
  файлы `/etc/vulkan/{icd,implicit_layer}.d/amd_icd64.json` на установке.

**Required Vulkan extensions** (assert at startup):
- `VK_KHR_cooperative_matrix` — CRITICAL, без него PP в 2× медленнее
- `VK_KHR_shader_float16_int8`
- `VK_KHR_shader_integer_dot_product`
- `VK_KHR_buffer_device_address` (core 1.2)
- `VK_EXT_external_memory_host` — UMA zero-copy

### 1.2.2 ROCm/HIP 7.x — secondary

Используется для случаев, где Vulkan проигрывает:
- Long-context pp ≥130K (HIP rocWMMA даёт ~40 t/s, RADV ~17)
- Concurrent batch ≥16 (HIP до 168 t/s aggregate vs ~85 single)
- PyTorch workloads (sentence-transformers offline, fine-tune)

**Note:** GDN-семейство моделей (Qwen3-Next, Qwen3.5/3.6 A3B) теперь
работает на Vulkan RADV после Mar 2026 (llama.cpp#20354 closed, GDN
Vulkan shader landed). Требует llama.cpp build **≥ b8765**.

**Mandatory minimums:**
- ROCm **≥ 7.2.0** (не 7.0.x — крашится на gfx1151, ROCm/#5534)
- Kernel **≥ 6.18.4** mainline / **6.17.0-19** HWE (старые видят только
  ~15.5 GiB VRAM — ROCm/#5444)
- linux-firmware **≥ 20260110**

**Build env (CMAKE/build-time):**
```
PYTORCH_ROCM_ARCH=gfx1151
AMDGPU_TARGETS=gfx1151
GPU_TARGETS=gfx1151
GGML_HIP_NO_VMM=ON
GGML_HIP_ROCWMMA_FATTN=ON
GGML_HIP_MMQ_MFMA=ON
GGML_NATIVE=OFF
```

**Runtime env:**
```
HSA_ENABLE_SDMA=0
ROCBLAS_USE_HIPBLASLT=1   # +15% pp
PYTORCH_ALLOC_CONF=expandable_segments:True
TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1   # +19× attention (critical)
HIP_PLATFORM=amd
MIOPEN_LOG_LEVEL=3
```

**ВАЖНО:** `HSA_OVERRIDE_GFX_VERSION=11.5.1` нужен **только** со stock
PyPI/pytorch.org wheels (которые на gfx1151 не работают). С AMD nightly
wheels (`https://rocm.nightlies.amd.com/v2/gfx1151/`) override **не
нужен** и может вызывать subtle bugs.

**Запрещено:**
- `PYTORCH_HIP_ALLOC_CONF=backend:malloc` — крашит
- stock `https://download.pytorch.org/whl/rocm*` wheels — `HIP error:
  invalid device function` на gfx1151

### 1.2.3 CPU (Zen 5, AVX-512, BF16) — fallback

llama.cpp CPU build + OpenBLAS/BLIS. Для x86_64 без AMD GPU
(datacenter Zen 4 / Ice Lake / Sapphire Rapids).

Throughput на 16C Zen 5 Strix Halo CPU: ~120-200 docs/sec на bge-m3-small
(embedding workload).

### 1.2.4 XDNA 2 NPU — НЕ ИСПОЛЬЗОВАТЬ

Ryzen AI SW под Linux официально не принимает Strix Halo
(см. `amd/RyzenAI-SW#366`). Заложить интерфейс как stub
(`NotImplementedError`), пересмотреть когда AMD добавит STX-H.

### 1.2.5 Engine selection внутри backend

Внутри Vulkan / ROCm backend есть выбор inference engine
(`AGMIND_ENGINE` env, default `auto`):

**Vulkan backend engines:**
- `llama_cpp` (default) — `llama-server` через llama-cpp-python с
  `GGML_VULKAN=ON`. **Primary engine — наиболее зрелый**.

**ROCm backend engines:**
- `llama_cpp` (default) — `llama-server` через llama-cpp-python с
  `GGML_HIP=ON`. **Primary engine**.
- `vllm` (M2 upgrade) — community fork (kyuz0-style патчи поверх
  TheRock nightlies). На gfx1151 vLLM v1 engine fails → `--enforce-eager`
  → 20× медленнее llama.cpp. Использовать **только** когда нужны:
  tool-calling parsers, structured outputs (JSON schema), Eagle-v3
  speculative decoding.
- `infinity` (M2 upgrade для embed/rerank) — michaelfeil/infinity с
  dynamic batching, OpenTelemetry, Prometheus. Для production embed
  workloads ≥4 concurrency.

### 1.2.6 Selection rules — decision matrix

```
gfx1151 detected:
  workload=chat single short_ctx     → vulkan/llama_cpp
  workload=chat long_ctx ≥130K       → vulkan/llama_cpp (tg)
  workload=long_ctx pp-bound         → rocm/llama_cpp + ROCWMMA_FATTN
  workload=concurrent batch ≥16      → rocm/llama_cpp -np 16
  workload=embed single (≤4 batch)   → vulkan/llama_cpp --pooling cls
  workload=embed batch ≥4 prod       → rocm/infinity (M2)
  workload=rerank                    → vulkan/llama_cpp --reranking
  model=GDN_family (Qwen3-Next)      → rocm/llama_cpp (Vulkan shader missing)
  workload=tool_calling/structured   → rocm/vllm (M2, accept penalty)
  workload=speculative_decoding      → rocm/vllm (M2)
  model=GDN_family + llama.cpp<b8765 → rocm/llama_cpp (Vulkan shader не landed в старом build)
  model=GDN_family + llama.cpp≥b8765 → vulkan/llama_cpp (default — shader landed)
no AMD GPU detected:
  any workload                       → cpu/llama_cpp

## 1.3. Жёсткие запреты

В основном дереве (всё кроме `legacy/`) запрещено:

| Категория | Конкретно |
|---|---|
| **CUDA runtime** | `cudaMalloc`, `cudaMemcpy`, `cublas*`, `cudnn*`, `nvinfer*`, `nccl`, `cuFFT`, `curand` |
| **Python CUDA** | `import pycuda`, `import cupy`, `import tensorrt`, `torch.cuda.*`, `.cuda()`, `device="cuda"`, `tensor.to("cuda")` |
| **CUDA пути** | `/usr/local/cuda*`, `/opt/nvidia/*`, `nvcr.io/*`, `nvidia/cuda:*` |
| **ARM/aarch64** | `aarch64`, `arm64`, `--platform=linux/arm64`, `platform_machine == "aarch64"` без x86_64 ветки |
| **NVIDIA hw** | `GB10`, `GB200`, `Grace`, `Blackwell`, `Hopper`, `H100/H200/A100`, `Jetson`, `Orin`, `DGX`, `Xavier`, `Tegra`, `TensorRT-LLM`, `Triton-Inference` |
| **CUDA flags** | `CUDA_ARCHITECTURES`, `CMAKE_CUDA_*`, `nvcc`, `sm_XX`, `compute_XX` |
| **Сборка** | `-march=native` в shippable артефактах (Dockerfile, CI, setup.py) |

**Разрешено / предпочтительно:**
- `llama-cpp-python` собранный с `GGML_VULKAN=ON` (primary backend)
  или `GGML_HIP=ON` (secondary).
- `torch` с ROCm-сборкой ИЛИ CPU-сборкой:
  - Production: AMD nightly index `https://rocm.nightlies.amd.com/v2/gfx1151/ --pre`
    либо stable wheels с `https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/`
  - CPU fallback: `https://download.pytorch.org/whl/cpu`
- `onnxruntime` (CPU only). `onnxruntime-rocm` 1.22.x **НЕ
  production-ready на gfx1151** в мае 2026 (silent CPU fallback).
- `vllm` версии **только** через community fork (kyuz0/hec-ovi patches
  + TheRock nightlies) для M2 upgrade. НЕ CUDA wheel, НЕ stock upstream
  vLLM (gfx1151 не supported — issue #16621 closed not planned).
- `michaelfeil/infinity` для M2 production embed/rerank (gfx1100 image
  как reference, для gfx1151 — собирать локально).
- `quay.io/docling-project/docling-serve-cpu:v1.18.0` (или новее) для
  document parsing. Drop-in replacement для `docling-serve-cu130` со
  Phase 43 preset API.
- Базовые образы: `ubuntu:24.04` (pinned digest),
  `rocm/dev-ubuntu-24.04:7.2-runtime` (production) или `7.2-complete`
  (build/debug). **Всегда пинить по digest**, не по тегу.

**Broken на gfx1151 в мае 2026 (не использовать):**
- TEI ROCm direct (PR #860 stalled, MI200/MI300-only)
- SGLang на gfx1151 (Instinct-only support)
- MLC-LLM в production (нет публичных бенчмарков для gfx1151)
- TGI на gfx1151 (Instinct-only)
- Ollama как production engine (vendored llama.cpp stale, -56% от upstream)
- FP8 anything (RDNA 3.5 lacks hardware)
- GPTQ/Marlin / MXFP4 quants (CUDA-only kernels или требуют CDNA)
- AITER + MoE на RDNA (CDNA-specific assumptions)
- fastembed-gpu (CUDA-only)
- bitsandbytes 4/8-bit на gfx1151 (`libbitsandbytes_rocm72.so` missing)
- `PYTORCH_HIP_ALLOC_CONF=backend:malloc` (крашит)
- AMDVLK ICD (`/etc/vulkan/icd.d/amd_icd64.json`) — discontinued + 2 GiB cap
- `HSA_OVERRIDE_GFX_VERSION=11.5.1` с native gfx1151 wheels (subtle bugs)
- stock PyPI/pytorch.org torch wheels на gfx1151 (`HIP error: invalid device function`)
- rootless Docker/Podman + ROCm (не работает на cgroups v2)

**Опт-аут для конкретной строки:** комментарий `# audit: allow` или `// audit: allow` (только с обоснованием рядом).

**Опт-аут для целого файла/директории:** добавить в
`scripts/audit_forbidden.py` `EXCLUDED_PATHS` (per-file) или
`EXCLUDED_PREFIXES` (per-directory). Используется для meta-файлов
(спека, MIGRATION_PLAN, ADR, recon-отчёты) которые описывают запреты
как rules.

## 1.4. Файловая структура

Архитектура трёх-слойная (per discussion 2026-05-19):
- **Ansible** = bootstrap / install / upgrade (declarative orchestration,
  заменяет старый `install.sh` на 1700 LOC bash → ~1200 LOC YAML)
- **Python `agmind/`** = compute abstraction + runtime CLI + diagnostics
- **`templates/services.yaml                # Container catalog (pinned semver)
templates/models.yaml                  # GGUF inventory per tier`** = single source of truth для container
  stack; читается обоими (Ansible через `lookup('file')|from_yaml`,
  Python через `agmind.services.registry`)

```
ansible/                           # Orchestration (install/upgrade)
  ansible.cfg                      # config
  install.yml                      # main playbook (replaces install.sh)
  inventory/
    hosts.yml                      # default single-node localhost
  group_vars/
    all.yml                        # platform/kernel/Strix Halo defaults
  roles/
    preflight/                     # x86_64, kernel, GPU detection, disk
    bootstrap/                     # apt, user/groups, sysctl
    strix_halo/                    # AMDVLK purge, GRUB cmdline, ttm
    docker/                        # docker-ce install + daemon.json
    agmind_python/                 # pip install -e .[dev] в /opt/agmind/venv
    models/                        # download GGUF per tier (auto-detected)
    services/                      # render compose из services.yaml
    observability/                 # prometheus/grafana/loki/alloy
    security/                      # UFW, fail2ban, optional Authelia
    smoke_test/                    # post-install verification
agmind/                            # Python package (новое дерево)
  __init__.py
  __main__.py                      # python -m agmind
  log.py                           # log_info / log_error utilities
  _env.py                          # safe .env reader
  secrets.py                       # credentials.txt management
  config/
    __init__.py
    env.py                         # .env generation + placeholder substitution
  compute/
    __init__.py                    # public: get_backend(), Backend
    base.py                        # ABC: Backend, DeviceInfo, LLMHandle
    detect.py                      # vulkaninfo / rocminfo / lspci / sysfs
    config.py                      # env vars AGMIND_BACKEND, AGMIND_ENGINE etc
    backends/
      cpu.py                       # llama.cpp CPU + onnxruntime CPU
      vulkan.py                    # выбор engine: llama_cpp (default)
      rocm.py                      # выбор engine: llama_cpp (default) / vllm M2 / infinity M2
      npu_stub.py                  # NotImplementedError для XDNA 2
      _engines/                    # engine implementations (внутри backend)
        __init__.py
        llama_cpp_vulkan.py
        llama_cpp_hip.py
        llama_cpp_cpu.py
        vllm_rocm.py               # M2
        infinity_rocm.py           # M2 embed
  diagnostics/
    __init__.py
    doctor.py                      # health + bundle
    health.py                      # service health checks
  cli/
    __init__.py                    # typer app
    install.py                     # agmind install
    status.py                      # agmind status
    doctor_cmd.py                  # agmind doctor
    embed_cmd.py                   # agmind embed (REPL/file)
    chat_cmd.py                    # agmind chat
  i18n/
    __init__.py
    en.json
    ru.json
  install/
    __init__.py
    phases.py                      # phase engine + resume
    preflight.py                   # host preflight checks
  state.py                         # state store (/var/lib/agmind/state)
  migrations.py                    # state schema migrations
legacy/
  gb10/
    README.md                      # что было, почему deprecated, как откатить
    install.sh                     # старый installer
    lib/                           # старые Bash модули
    scripts/                       # старые utility-скрипты
    templates/                     # старые docker-compose / env
    tests/                         # старые bash unit tests
    docs/                          # старые ADR + matrices
    documentation/
    .planning/                     # старые planning документы
    pipelines/ plugins/ workflows/ # старые Dify/n8n content
    monitoring/ benchmarks/
docker/
  Dockerfile.base                  # общий x86-64 слой
  Dockerfile.cpu                   # CPU-only backend
  Dockerfile.vulkan                # Vulkan RADV backend
  Dockerfile.rocm                  # ROCm/HIP backend
docs/
  adr/
    0000-template.md
    0001-migration-to-x86-strix-halo.md
    0002-compute-backend-abstraction.md
    0003-memory-budgeting-strix-halo.md   # gtt_total vs vram_total
    0004-engine-selection-within-backend.md
    ...
  MIGRATION_PLAN.md                # генерится в Фазе A
  BENCHMARKS.md                    # генерится в Фазе G
  HARDWARE.md                      # host setup BIOS+kernel+sysctl
scripts/
  audit_forbidden.py               # см. Часть 4
  preflight_strix_halo.sh          # bash-обёртка для CI / install
tests/
  compute/
    test_contract.py               # параметризовано backend_*
    test_detect.py
    test_engines.py
  cli/
  diagnostics/
  fixtures/
benchmarks/
  llm_tg_pp.py
  embed_throughput.py
.planning/
  research/x86-migration/          # recon-отчёты R*
  sessions/                        # session journals
migration_progress.json            # persistent state (Karpathy R1)
pyproject.toml
Makefile
.github/
  workflows/ci.yml
.pre-commit-config.yaml
CLAUDE.md                          # 8-line operational rules + spec pointer
AGMIND_MIGRATION_SPEC.md           # this file
README.md
LICENSE
.gitignore
```

## 1.4.1. Orchestration: Ansible вместо Bash

**Зачем не bash:** старый `install.sh` (1700 LOC) делал idempotency руками,
inventory был implicit, secrets через `chmod 600` + sed/envsubst, multi-node
через SSH loop. Ansible решает всё это нативно:
- `community.docker.docker_compose_v2` — idempotent compose state
- `ansible.builtin.template` + Jinja2 — рендер configs из шаблонов
- `community.general.ufw` — declarative firewall
- `ansible.builtin.user` + `ansible.posix.sysctl` — host bootstrap
- `inventory/hosts.yml` — multi-node когда понадобится (master/worker)
- `ansible-vault` — secrets без bash trickery

**Required collections** (`ansible-galaxy collection install -r requirements.yml`):
- `community.docker` (≥ 4.0)
- `community.general` (≥ 9.0)
- `ansible.posix` (≥ 1.5)

**Точки опт-аута:** некоторые операции остаются Python entry-points:
- `agmind doctor` (runtime diagnostics — Python с прямым доступом к sysfs)
- `agmind status` (runtime backend selection)
- Compute abstraction (Vulkan/ROCm/CPU backends) — Python library

## 1.5. Workflow при работе над миграцией

1. **Никогда не удалять GB10/CUDA код напрямую.** Только `git mv` в `legacy/gb10/` + deprecation-комментарий в начало файла:
   ```python
   # DEPRECATED: GB10/CUDA only. См. ADR-0001. Не использовать в новом коде.
   ```
2. **Сначала аудит, потом изменения.** Перед правкой модуля прогнать `scripts/audit_forbidden.py`.
3. **ADR для каждого нетривиального решения.** Формат — раздел 5.2. Без ADR на новую архитектурную директорию — reject.
4. **Тесты идут вместе с кодом.** Для compute-слоя обязательны contract tests, параметризованные по бэкендам.
5. **Бенчмарки фиксируются.** Hot path → `pytest-benchmark`, before/after в PR description.
6. **Один PR = одна фаза GSD-плана.** Не сваливать всё в один коммит.

---

# Часть 2. GSD-планинг

GSD = Getting Shit Done. Принципы:

- **Capture everything**, потом сортируй. На старте — inventory всего, что заражено GB10/CUDA/aarch64.
- **Next Action в формате physical-verb-first.** Не «починить Docker», а «удалить FROM nvcr.io из docker/Dockerfile.gpu, заменить на ubuntu:24.04@sha256:..., обновить apt-get install список». Если шаг не помещается в одну глагольную фразу — он не Next Action, он Project.
- **Один Next Action — один контекст.** Не смешивать «прочитать код» и «написать код» в одной таске.
- **2-minute rule:** если действие занимает <2 минут — делать сразу, не записывать.
- **Weekly review:** в конце каждой фазы — сверка inventory, обновление плана, чистка done.
- **Definition of Done строгое:** acceptance criteria выписаны до старта, проверяются автоматически где возможно.

## 2.1. Бэклог фаз (top-level Projects)

| Фаза | Project | Outcome (DoD) |
|---|---|---|
| **A** | Inventory & Plan | `docs/MIGRATION_PLAN.md` с разбивкой на PR-ы, утверждён пользователем |
| **B** | Legacy quarantine | Весь CUDA/aarch64 код в `legacy/gb10/`, основное дерево компилируется без GPU |
| **C** | Compute abstraction | `agmind/compute/` реализован, CPU backend проходит contract tests |
| **D** | Backend implementations | Vulkan + ROCm backends реализованы и проходят contract tests на Strix Halo |
| **E** | Call-sites migration | Аудитор: 0 находок вне `legacy/`. Все hot path идут через `agmind.compute` |
| **F** | Docker & CI | 3 backend-Dockerfile'а зелёные, CI matrix зелёная, self-hosted runner настроен |
| **G** | Benchmarks & docs | `docs/BENCHMARKS.md` с цифрами, README обновлён, ADR закрыты |

**Правило:** не начинать фазу N+1 пока DoD фазы N не выполнен. Исключение —
параллельные фазы без зависимостей (например, D можно начать пока идёт E
для не-compute модулей).

## 2.2. Фаза A — Inventory & Plan (READ-ONLY)

**Outcome:** `docs/MIGRATION_PLAN.md` с разбивкой работы на 4-7 PR-ов.
**Жёстко: НИ ОДНОГО изменения кода. Только чтение и анализ.**

### Next Actions (выполнять по порядку)

- [ ] **A1. Запустить аудитор и собрать baseline.**
  ```
  python3 scripts/audit_forbidden.py --json baseline-audit.json
  ```
  Скрипт описан в Части 4 — если его нет, СНАЧАЛА создай его (Часть 4).
- [ ] **A2. Сгруппировать findings:** по правилу, по топ-10 файлов с
      наибольшим числом находок, по «горячести» (пересечение с hot path).
- [ ] **A3. Построить dependency graph** заражённых модулей:
      `python3 -c "import ast" + grep` или `pydeps`. Кто на ком держится,
      в каком порядке резать.
- [ ] **A4. Определить hot path:** какие модули критичны для прод-нагрузки
      (entrypoints, inference loop, embedding pipeline, fine-tune). Сверка
      с пользователем если непонятно.
- [ ] **A5. Написать `docs/MIGRATION_PLAN.md`** со следующими секциями:
      - Inventory (таблица: модуль → категория заражения → размер)
      - Hot path (список модулей с обоснованием)
      - Dependency graph (mermaid или текст)
      - Phasing (4-7 PR-ов с явным scope каждого)
      - Acceptance criteria по фазам (тесты, бенчмарки, аудит=N)
      - Risks (что может сломаться, как митигировать)
      - Rollback strategy для каждой фазы
- [ ] **A6. Показать план пользователю в чате**, получить апрув, ТОЛЬКО ПОТОМ переходить в B.

**DoD фазы A:** план в репо, апрув получен, ни одного коммита в исходный код.

## 2.3. Фаза B — Legacy quarantine

**Outcome:** весь GB10/CUDA/aarch64 код перенесён в `legacy/gb10/`, основное дерево импортируется без ошибок (даже если функционально ломается).

### Next Actions

- [ ] **B1.** Создать `legacy/gb10/` и `legacy/gb10/README.md` (описание: что лежит, почему, как откатить).
- [ ] **B2.** `git mv` (НЕ копирование!) перенести в `legacy/gb10/`:
      - CUDA-специфичные модули из inventory
      - GB10-Dockerfile'ы и compose-файлы
      - aarch64-only скрипты
      - TensorRT/NCCL/NIM конфиги
- [ ] **B3.** В начало каждого перенесённого файла добавить:
      ```
      # DEPRECATED: GB10/CUDA only. См. ADR-0001.
      # Не использовать в новом коде. Поддерживается до 2027-Q1 для отката.
      ```
- [ ] **B4.** Обновить импортёры из основного дерева:
      - Если основной модуль импортит legacy → пометить `FIXME(migration)`
      - Если возможно — заменить на NotImplementedError-стаб
      - Если hot path — добавить в плановый список Фазы E
- [ ] **B5.** Прогон аудитора после каждого крупного move. Цель: findings в основном дереве монотонно уменьшаются, в `legacy/` игнорируются (папка в `EXCLUDED_DIRS` аудитора).
- [ ] **B6.** Коммитить по одному логическому move за раз. **Один PR = один move.**

**DoD фазы B:**
- `git ls-files | xargs grep -l "import torch.cuda\|nvcr.io\|aarch64"` показывает только `legacy/` и `docs/`.
- `python3 -c "import agmind"` не падает с ImportError (может падать с NotImplementedError при вызове hot path — это нормально на этой фазе).
- Все изменения в B — серия отдельных PR-ов, каждый ревьюится.

## 2.4. Фаза C — Compute abstraction skeleton

**Outcome:** `agmind/compute/` реализован, CPU backend работает, contract tests зелёные на CPU.

### Next Actions

- [ ] **C1.** Создать `agmind/compute/base.py` с ABC `Backend` (см. ADR-0002 шаблон в разделе 5.3).
- [ ] **C2.** `agmind/compute/detect.py` — рантайм-детект через subprocess (`vulkaninfo --summary`, `rocminfo`, `lspci -nn`).
- [ ] **C3.** `agmind/compute/config.py` — чтение env vars:
      - `AGMIND_BACKEND` (vulkan|rocm|cpu|auto, default: auto)
      - `AGMIND_DEVICE_ID` (default: 0)
      - `AGMIND_BACKEND_PROFILE` (tg|pp|mixed, влияет на auto-select)
- [ ] **C4.** `agmind/compute/backends/cpu.py` — реализация на llama-cpp-python (CPU) + onnxruntime (CPU). Lazy imports внутри методов.
- [ ] **C5.** `agmind/compute/backends/npu_stub.py` — `available() → False`, всё остальное `NotImplementedError`.
- [ ] **C6.** `agmind/compute/__init__.py` — `get_backend()` функция с auto-select логикой.
- [ ] **C7.** `tests/compute/test_contract.py` — параметризованные тесты, маркеры `backend_cpu`, `backend_vulkan`, `backend_rocm`, `backend_any`.
- [ ] **C8.** `tests/compute/test_detect.py` — мок-тесты для detect.py.

**DoD фазы C:**
- `pytest tests/compute -m backend_cpu` зелёный.
- `pytest tests/compute -m backend_any` зелёный.
- `AGMIND_BACKEND=cpu python -c "from agmind.compute import get_backend; print(get_backend().device_info())"` работает.

## 2.5. Фаза D — Backend implementations

**Outcome:** Vulkan и ROCm бэкенды реализованы, contract tests зелёные на реальном Strix Halo.

### Next Actions

- [ ] **D1.** `agmind/compute/backends/vulkan.py`:
      - llama-cpp-python с `GGML_VULKAN=ON` (lazy import)
      - Принудительно `AMD_VULKAN_ICD=RADV` в env
      - `device_info()` через `vulkaninfo --json` парсинг
- [ ] **D2.** `agmind/compute/backends/rocm.py`:
      - torch+ROCm для тренировки/embeddings
      - llama-cpp-python с `GGML_HIP=ON` для inference
      - `device_info()` через `rocminfo` парсинг
      - Проверка `HSA_OVERRIDE_GFX_VERSION` в env, warning если не выставлено на gfx1151
- [ ] **D3.** Обновить auto-select в `get_backend()`:
      ```
      profile=tg, gfx1151 → vulkan
      profile=pp, gfx1151 → rocm
      profile=mixed → vulkan (default)
      no GPU → cpu
      ```
- [ ] **D4.** Прогон `pytest -m "backend_vulkan or backend_rocm"` на Strix Halo (локально или self-hosted runner).
- [ ] **D5.** Зафиксировать baseline-бенчмарки в `docs/BENCHMARKS.md`.

**DoD фазы D:**
- Все три бэкенда (cpu, vulkan, rocm) проходят contract tests.
- `device_info()` корректно отдаёт metadata на Strix Halo.
- `docs/BENCHMARKS.md` имеет baseline-числа.

## 2.6. Фаза E — Call-sites migration

**Outcome:** все hot path call-sites переведены на `agmind.compute`. Аудитор: 0 находок вне `legacy/`.

### Next Actions

- [ ] **E1.** Список call-sites из Фазы A (hot path).
- [ ] **E2.** Для каждого модуля: заменить прямые вызовы (torch.cuda, .cuda(), CUDA-либы) на `agmind.compute.get_backend()`.
- [ ] **E3.** Тесты на модуль до и после.
- [ ] **E4.** Один PR на модуль (или на логически связанную группу). Никаких сборных PR-ов «refactor everything».
- [ ] **E5.** После каждого PR — прогон аудитора. Findings монотонно стремятся к нулю.

**DoD фазы E:**
- `python3 scripts/audit_forbidden.py --fail` exit 0.
- `pytest` зелёный полностью (не только compute).
- Hot path функционально эквивалентен старому (smoke-тесты прошли).

## 2.7. Фаза F — Docker & CI

**Outcome:** 3 backend-Dockerfile'а собираются, CI зелёный, self-hosted runner на Strix Halo гоняет nightly бенчи.

### Next Actions

- [ ] **F1.** Подставить реальные digest'ы в Dockerfile'ы:
      ```
      docker buildx imagetools inspect ubuntu:24.04
      docker buildx imagetools inspect rocm/dev-ubuntu-24.04:7.0-complete
      ```
- [ ] **F2.** Локально прогнать `docker build` для всех трёх Dockerfile'ов.
- [ ] **F3.** Если есть `docker-compose.yml` — переписать без `nvcr.io`, добавить варианты под бэкенды.
- [ ] **F4.** Прогнать CI workflow (см. раздел 5.4) — все 3 docker-build job'а должны быть зелёные.
- [ ] **F5.** Настроить self-hosted runner на Strix Halo (label `strix-halo`).
- [ ] **F6.** Включить `workflow_dispatch` для `test-strix-halo`, прогнать вручную, убедиться что зелёно.

**DoD фазы F:**
- Все Docker-сборки в CI зелёные.
- Self-hosted runner подключён и проходит ручной запуск.

## 2.8. Фаза G — Benchmarks & docs

**Outcome:** numeric proof что миграция не сломала производительность (или сломала контролируемо), документация обновлена.

### Next Actions

- [ ] **G1.** `benchmarks/` suite на pytest-benchmark или asv:
      - LLM tg на 7B/30B/70B
      - LLM pp на длинных контекстах (4k, 8k, 32k)
      - Embeddings throughput
      - Memory footprint
- [ ] **G2.** Запуск на Strix Halo для всех бэкендов, обновить `docs/BENCHMARKS.md`.
- [ ] **G3.** Сравнение с старыми GB10-числами (если есть), сохранёнными до миграции.
- [ ] **G4.** Обновить корневой README с актуальными инструкциями по запуску.
- [ ] **G5.** Закрыть все открытые ADR, обновить дату ревью в этой спеке.

**DoD фазы G:**
- `docs/BENCHMARKS.md` имеет числа на всех релевантных бэкендах.
- README отражает текущее состояние.
- Этот файл (раздел 1.2) актуален на текущую дату.

## 2.9. Daily routine на время миграции

В начале каждой сессии Claude Code:
1. Читает этот файл (`AGMIND_MIGRATION_SPEC.md`) целиком.
2. Открывает `docs/MIGRATION_PLAN.md` если он уже существует.
3. Прогоняет `python3 scripts/audit_forbidden.py` чтобы знать текущее состояние findings.
4. Спрашивает пользователя: «какая фаза сейчас, какой Next Action берём».
5. Выполняет ровно один Next Action, потом останавливается на ревью.

В конце сессии:
1. Обновляет статус Next Actions в плане (галочки).
2. Если что-то новое всплыло — добавляет в backlog в `docs/MIGRATION_PLAN.md`.
3. Коммитит с понятным сообщением: `phase-B: move tensorrt wrapper to legacy/`.

---

# Часть 3. Запретные паттерны (быстрая шпаргалка)

Если Claude Code видит в diff что-то из этого списка — **СТОП**, спросить пользователя:

- слово `cuda` вне `legacy/` или комментариев
- `aarch64`, `arm64` вне `legacy/`, CI multi-arch матрицы, или комментариев
- `nvcr.io`, `nvidia/`, `tensorrt`, `triton-inference-server` вне `legacy/`
- `Jetson`, `Orin`, `Grace`, `Blackwell`, `Hopper` вне `legacy/` или ADR с обоснованием
- хардкод путей `/usr/local/cuda*`, `/opt/nvidia/*`
- хардкод архитектуры в setup.py / pyproject.toml без x86_64 ветки
- `-march=native` в любом shippable артефакте

---

# Часть 4. Аудитор — `scripts/audit_forbidden.py`

Если этого файла ещё нет в репо — создать ровно с этим содержимым:

```python
#!/usr/bin/env python3
"""
audit_forbidden.py — поиск платформо-зависимых хардкодов и упоминаний
старого стека (GB10 / CUDA / aarch64) во всём репозитории.

Запуск:
    python scripts/audit_forbidden.py              # отчёт по всему репо
    python scripts/audit_forbidden.py path/to/dir  # по подкаталогу
    python scripts/audit_forbidden.py --fail       # exit 1 при находках (для CI)
    python scripts/audit_forbidden.py --json out.json

Категории = раздел 3 AGMIND_MIGRATION_SPEC.md.
Папка legacy/ исключена из аудита (там этому коду место).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "legacy", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages",
}

TEXT_SUFFIXES = {
    ".py", ".pyx", ".pyi", ".toml", ".cfg", ".ini", ".yaml", ".yml",
    ".json", ".md", ".rst", ".txt", ".sh", ".bash", ".zsh",
    ".dockerfile", ".cmake", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp",
    ".cu", ".cuh", ".rs", ".go", ".js", ".ts", ".tsx", ".jsx",
    "",
}

BARE_NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt", ".gitignore", ".env"}

RULES: list[tuple[str, str, re.Pattern]] = [
    (
        "cuda_runtime",
        "Прямые упоминания CUDA runtime / API",
        re.compile(
            r"\b(cudaMalloc|cudaMemcpy|cudaFree|cudaStream|cublas\w*|"
            r"cudnn\w*|nvinfer\d*|nvrtc|nccl|cuFFT|curand)\b"
        ),
    ),
    (
        "cuda_python",
        "CUDA в Python-импортах и атрибутах",
        re.compile(
            r"(?<![A-Za-z_])("
            r"import\s+(?:pycuda|cupy|tensorrt|onnxruntime_gpu)|"
            r"from\s+(?:pycuda|cupy|tensorrt)\b|"
            r"torch\.cuda\.|"
            r"\.cuda\(\)|"
            r"\.to\(['\"]cuda(?::\d+)?['\"]\)|"
            r"device\s*=\s*['\"]cuda(?::\d+)?['\"]"
            r")"
        ),
    ),
    (
        "cuda_paths",
        "Хардкод путей CUDA / NVIDIA",
        re.compile(
            r"(/usr/local/cuda[\w\-./]*|/opt/nvidia[\w\-./]*|"
            r"nvcr\.io/[\w\-./:]+|nvidia/cuda:[\w\-.]+)"
        ),
    ),
    (
        "arm_aarch64",
        "Упоминания ARM/aarch64 архитектуры",
        re.compile(
            r"\b(aarch64|arm64|armv[78]|--platform=linux/arm64|"
            r"platform_machine\s*==\s*['\"]aarch64['\"])\b",
            re.IGNORECASE,
        ),
    ),
    (
        "nvidia_hw",
        "Имена NVIDIA-железа и продуктов",
        re.compile(
            r"\b(GB10|GB200|Grace|Blackwell|Hopper|H100|H200|A100|"
            r"Jetson|Orin|DGX|Xavier|Tegra|TensorRT[\-_]LLM|Triton[\-_]Inference)\b"
        ),
    ),
    (
        "cuda_arch_flags",
        "CUDA build flags (CMake/setup)",
        re.compile(
            r"(CUDA_ARCHITECTURES|CMAKE_CUDA_\w+|nvcc\b|--gpu-architecture|"
            r"compute_\d{2}|sm_\d{2})"
        ),
    ),
    (
        "native_march",
        "-march=native в shippable артефактах",
        re.compile(r"-march=native"),
    ),
]


@dataclass
class Finding:
    rule: str
    description: str
    file: str
    line: int
    snippet: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0

    @property
    def by_rule(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.rule, []).append(f)
        return out


def is_text_file(p: Path) -> bool:
    if p.name in BARE_NAMES:
        return True
    return p.suffix.lower() in TEXT_SUFFIXES


def iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in p.parts):
            continue
        if not is_text_file(p):
            continue
        yield p


def scan_file(p: Path, report: Report) -> None:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    report.scanned_files += 1
    for i, line in enumerate(text.splitlines(), start=1):
        if "# audit: allow" in line or "// audit: allow" in line:
            continue
        for rule_id, desc, pat in RULES:
            if pat.search(line):
                report.findings.append(
                    Finding(rule=rule_id, description=desc, file=str(p),
                            line=i, snippet=line.strip()[:200])
                )


def print_report(report: Report) -> None:
    grouped = report.by_rule
    print(f"\n=== AGmind audit ===")
    print(f"Файлов проверено: {report.scanned_files}")
    print(f"Находок:          {len(report.findings)}")
    if not report.findings:
        print("✅ Запрещённых паттернов не найдено.")
        return
    print()
    for rule_id, items in sorted(grouped.items()):
        desc = items[0].description
        print(f"[{rule_id}] {desc} — {len(items)} находок")
        for f in items[:50]:
            print(f"  {f.file}:{f.line}: {f.snippet}")
        if len(items) > 50:
            print(f"  ... и ещё {len(items) - 50}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=".", type=Path)
    ap.add_argument("--fail", action="store_true",
                    help="exit 1 если есть находки (для CI)")
    ap.add_argument("--json", type=Path, default=None,
                    help="дополнительно записать JSON-отчёт")
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.exists():
        print(f"Путь не существует: {root}", file=sys.stderr)
        return 2

    report = Report()
    for p in iter_files(root):
        scan_file(p, report)
    print_report(report)

    if args.json:
        payload = {"scanned_files": report.scanned_files,
                   "findings": [asdict(f) for f in report.findings]}
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nJSON отчёт: {args.json}")

    if args.fail and report.findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

# Часть 5. Шаблоны и заготовки

## 5.1. CLAUDE.md в корне репо

Создать `CLAUDE.md` в корне с одной строкой:
```
См. AGMIND_MIGRATION_SPEC.md — единственный источник правды.
```
Это гарантирует что Claude Code прочитает спеку в каждой сессии (CLAUDE.md он подхватывает автоматически).

## 5.2. Шаблон ADR

`docs/adr/0000-template.md`:
```markdown
# ADR-NNNN: <короткий заголовок решения>

- **Status:** proposed | accepted | deprecated | superseded by ADR-XXXX
- **Date:** YYYY-MM-DD
- **Authors:** @username
- **Related:** ADR-XXXX, issue #YYY

## Контекст
Что заставило это решение принимать. Какие силы давят, какие ограничения.

## Рассмотренные варианты
### A: ...
- Плюсы / Минусы / Цена

### B: ...

### C: ничего не делать

## Решение
Выбран вариант X, потому что …

## Последствия
### Положительные
### Отрицательные / технический долг
### Что нужно сделать
- [ ] ...

## Бенчмарки (если применимо)
| Сценарий | До | После | Δ |

## Откат
Что делать если решение не сработало.
```

## 5.3. Шаблон Backend ABC

`agmind/compute/base.py`:
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class DeviceInfo:
    backend: str
    device_id: int
    name: str
    total_memory_bytes: int
    capabilities: dict[str, Any]

class Backend(ABC):
    name: str  # "cpu" | "vulkan" | "rocm" | "npu"

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Можно ли использовать этот бэкенд на текущей машине."""

    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def load_llm(self, model_path: str, **kwargs) -> "LLMHandle": ...

    @abstractmethod
    def embed(self, texts: list[str], model: str) -> "np.ndarray": ...

    # ... добавлять методы по мере фактической нужды,
    # НЕ закладывать спекулятивный API
```

## 5.4. CI workflow

`.github/workflows/ci.yml`:
```yaml
name: ci
on:
  pull_request: { branches: [main, develop] }
  push: { branches: [main, develop] }
permissions: { contents: read }

jobs:
  audit:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python3 scripts/audit_forbidden.py --fail --json audit-report.json
      - if: always()
        uses: actions/upload-artifact@v4
        with: { name: audit-report, path: audit-report.json }

  lint:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy agmind/

  test-cpu:
    runs-on: ubuntu-24.04
    needs: [audit, lint]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -e ".[cpu,dev]"
      - run: pytest -q --cov=agmind

  docker-build:
    runs-on: ubuntu-24.04
    needs: [audit]
    strategy:
      fail-fast: false
      matrix:
        backend: [cpu, vulkan, rocm]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.base
          platforms: linux/amd64
          tags: agmind-base:ci
          load: true
          cache-from: type=gha,scope=base
          cache-to: type=gha,scope=base,mode=max
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.${{ matrix.backend }}
          platforms: linux/amd64
          build-args: BASE_IMAGE=agmind-base:ci
          tags: agmind-${{ matrix.backend }}:ci
          cache-from: type=gha,scope=${{ matrix.backend }}
          cache-to: type=gha,scope=${{ matrix.backend }},mode=max

  test-strix-halo:
    runs-on: [self-hosted, linux, x64, strix-halo]
    if: github.event_name == 'workflow_dispatch'
    needs: [audit, lint]
    strategy:
      fail-fast: false
      matrix: { backend: [vulkan, rocm] }
    steps:
      - uses: actions/checkout@v4
      - env: { AGMIND_BACKEND: "${{ matrix.backend }}" }
        run: |
          docker run --rm --device /dev/dri \
            $( [ "${{ matrix.backend }}" = "rocm" ] && echo "--device /dev/kfd --group-add video --group-add render" ) \
            -e AGMIND_BACKEND \
            agmind-${{ matrix.backend }}:ci \
            pytest -q -m "backend_${{ matrix.backend }} or backend_any"
```

## 5.5. pre-commit конфиг

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: local
    hooks:
      - id: agmind-audit-forbidden
        name: AGmind — запрет на non-x86 / CUDA хардкоды
        entry: python3 scripts/audit_forbidden.py --fail
        language: system
        pass_filenames: false
        always_run: true
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
        args: [--maxkb=1024]
```

## 5.6. Dockerfile.base (skeleton)

```dockerfile
# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 ubuntu:24.04@sha256:REPLACE_WITH_DIGEST AS base
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git build-essential \
        python3 python3-pip python3-venv python3-dev \
        pkg-config cmake ninja-build \
        libopenblas-dev libomp-dev \
    && rm -rf /var/lib/apt/lists/*
ENV CFLAGS="-O2 -march=x86-64-v3 -mtune=generic" \
    CXXFLAGS="-O2 -march=x86-64-v3 -mtune=generic"
WORKDIR /opt/agmind
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip setuptools wheel
ENV PATH="/opt/venv/bin:${PATH}"
```

## 5.7. Dockerfile.vulkan (основной)

```dockerfile
# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=agmind-base:latest
FROM ${BASE_IMAGE}

# Vulkan runtime + Mesa userspace (RADV) + GL stack для loader
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        mesa-vulkan-drivers libvulkan1 vulkan-tools \
        glslc shaderc spirv-tools glslang-tools libvulkan-dev \
        spirv-headers \
        libglvnd0 libgl1 libegl1 libegl-mesa0 libglx0 \
    && rm -rf /var/lib/apt/lists/*

# Mesa 26+ через kisak fresh PPA для critical MoE fixes
RUN add-apt-repository -y ppa:kisak/kisak-mesa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        mesa-vulkan-drivers libvulkan1 \
    && rm -rf /var/lib/apt/lists/*

# КРИТИЧЕСКИ ВАЖНО: вычистить любые AMDVLK ICD/implicit layer файлы.
# AMDVLK discontinued 2025-09-15, имеет 2 GiB cap который ломает LLM.
RUN rm -f \
        /etc/vulkan/icd.d/amd_icd64.json \
        /etc/vulkan/icd.d/amd_icd32.json \
        /etc/vulkan/implicit_layer.d/amd_icd64.json \
        /etc/vulkan/implicit_layer.d/amd_icd32.json

# Принудительно RADV для всего, что запускается из контейнера
ENV AMD_VULKAN_ICD=RADV \
    VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
    VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
    GGML_VK_VISIBLE_DEVICES=0 \
    CMAKE_ARGS="-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DCMAKE_BUILD_TYPE=Release" \
    FORCE_CMAKE=1

# CPU-only PyTorch (Vulkan backend через llama-cpp-python, не torch)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-binary llama-cpp-python "llama-cpp-python>=0.3.23" \
    && pip install onnxruntime numpy scipy

COPY . /opt/agmind
RUN pip install -e ".[vulkan]"
ENV AGMIND_BACKEND=vulkan

# Запуск: docker run --device /dev/dri \
#   --group-add video --group-add render \
#   --security-opt seccomp=unconfined ...
ENTRYPOINT ["python3", "-m", "agmind"]
```

## 5.8. Dockerfile.rocm

```dockerfile
# syntax=docker/dockerfile:1.7
# ROCm 7.2 runtime образ (не :7.0-complete — крашится на gfx1151,
# не :7.2-complete — 20-30 GB, для prod runtime ≤10 GB).
FROM --platform=linux/amd64 rocm/dev-ubuntu-24.04:7.2-runtime@sha256:REPLACE_WITH_DIGEST

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTORCH_ROCM_ARCH=gfx1151 \
    PYTORCH_ALLOC_CONF=expandable_segments:True \
    TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 \
    ROCBLAS_USE_HIPBLASLT=1 \
    HSA_ENABLE_SDMA=0 \
    HIP_PLATFORM=amd \
    MIOPEN_LOG_LEVEL=3 \
    HIP_VISIBLE_DEVICES=0 \
    ROCR_VISIBLE_DEVICES=0
# ВАЖНО: НЕ устанавливать HSA_OVERRIDE_GFX_VERSION=11.5.1 с AMD nightly
# wheels — native gfx1151 kernels уже есть; override может вызывать
# subtle bugs на attention/conv.

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev \
        build-essential cmake ninja-build pkg-config git \
        libnuma1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip wheel setuptools
ENV PATH="/opt/venv/bin:${PATH}"

# PyTorch ROCm: ОБЯЗАТЕЛЬНО AMD nightly index для gfx1151
# (stock pytorch.org wheels дают 'HIP error: invalid device function').
RUN pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre \
        torch torchaudio torchvision

# llama-cpp-python с GGML_HIP (gfx1151 specific build)
ENV CMAKE_ARGS="-DGGML_HIP=ON \
                -DAMDGPU_TARGETS=gfx1151 \
                -DGPU_TARGETS=gfx1151 \
                -DGGML_HIP_NO_VMM=ON \
                -DGGML_HIP_ROCWMMA_FATTN=ON \
                -DGGML_HIP_MMQ_MFMA=ON \
                -DGGML_NATIVE=OFF \
                -DCMAKE_BUILD_TYPE=Release" \
    FORCE_CMAKE=1 \
    HIPCXX=/opt/rocm/llvm/bin/clang \
    HIP_PATH=/opt/rocm
RUN pip install --no-binary llama-cpp-python "llama-cpp-python>=0.3.23"

# onnxruntime CPU only — onnxruntime-rocm 1.22.x не gfx1151-ready
RUN pip install onnxruntime numpy scipy

WORKDIR /opt/agmind
COPY . /opt/agmind
RUN pip install -e ".[rocm]"
ENV AGMIND_BACKEND=rocm

# Запуск: docker run --device /dev/kfd --device /dev/dri \
#   --group-add video --group-add render \
#   --security-opt seccomp=unconfined --cap-add=SYS_PTRACE \
#   --ipc=host --shm-size=16G ...
# (rootless Docker НЕ работает с ROCm на cgroups v2 — ROCm/#2860)
ENTRYPOINT ["python3", "-m", "agmind"]
```

## 5.9. Dockerfile.cpu

```dockerfile
# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=agmind-base:latest
FROM ${BASE_IMAGE}
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision torchaudio \
    && pip install onnxruntime "llama-cpp-python" numpy scipy
COPY . /opt/agmind
RUN pip install -e ".[cpu]"
ENV AGMIND_BACKEND=cpu
ENTRYPOINT ["python3", "-m", "agmind"]
```

## 5.10. pyproject.toml extras (фрагмент)

```toml
[project.optional-dependencies]
cpu = [
  "torch",
  "onnxruntime",
  "llama-cpp-python",
  "numpy",
  "scipy",
]
vulkan = [
  "llama-cpp-python",  # build with CMAKE_ARGS=-DGGML_VULKAN=ON
  "numpy",
  "scipy",
]
rocm = [
  "torch",  # install from rocm index-url
  "onnxruntime",
  "llama-cpp-python",  # build with CMAKE_ARGS=-DGGML_HIP=ON
  "numpy",
  "scipy",
]
dev = [
  "pytest",
  "pytest-cov",
  "pytest-benchmark",
  "ruff",
  "mypy",
  "pre-commit",
]
```

---

# Часть 6. Полезные ссылки (на момент написания, май 2026)

- llama.cpp Strix Halo benchmarks: https://github.com/visorcraft/strix-halo-llm-perf
- Strix Halo guide (Vulkan vs ROCm, гайд по флагам): https://github.com/hogeheer499-commits/strix-halo-guide
- Ryzen AI SW Linux issue (XDNA на STX-H заблокирован): https://github.com/amd/RyzenAI-SW/issues/366
- Phoronix ROCm 7.0 на Strix Halo: https://www.phoronix.com/review/amd-rocm-7-strix-halo
- AMD ROCm docs: https://rocm.docs.amd.com/
- Mesa RADV: https://docs.mesa3d.org/drivers/radv.html

---

**Конец спеки.** При любом конфликте между этим файлом и поведением Claude Code — этот файл выигрывает. Изменения спеки — только через PR с обоснованием.
