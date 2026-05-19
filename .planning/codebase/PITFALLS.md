# AGmind Pitfalls — known gotchas

24+ gotchas из recons (R1-R11) + practical lessons. Каждый — со
severity + workaround + source.

## P1 — Critical (production blockers)

### P1.1 AMDVLK ICD silently overrides RADV
- **Severity:** critical (LLM ≥30B не загружаются — 2 GiB cap)
- **Detection:** `ls /etc/vulkan/{icd.d,implicit_layer.d}/amd_icd*.json`
- **Fix:** `sudo rm -f` + `apt remove --purge amdvlk`
- **Source:** R2-vulkan-radv-vs-amdvlk.md
- **Detected by:** `agmind doctor::amdvlk-absent`

### P1.2 stock PyPI torch wheels не работают на gfx1151
- **Severity:** critical (`HIP error: invalid device function` на ROCm)
- **Detection:** `pip list | grep torch` → если из download.pytorch.org/whl/rocm6.x
- **Fix:** `pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre torch torchaudio torchvision`
- **Source:** R1-pytorch-rocm-docker.md

### P1.3 `PYTORCH_HIP_ALLOC_CONF=backend:malloc` крашит
- **Severity:** critical (PyTorch не стартует)
- **Detection:** env grep
- **Fix:** unset + use `PYTORCH_ALLOC_CONF=expandable_segments:True`
- **Source:** R1

### P1.4 ROCm 7.0.x крашится на gfx1151
- **Severity:** critical
- **Fix:** ROCm 7.2.x минимум (ROCm/issues/5534)
- **Source:** R3 + R1

### P1.5 Kernel < 6.17.0-19 HWE — ROCm видит только 15.5 GiB VRAM
- **Severity:** high (memory budget broken)
- **Detection:** `agmind doctor::kernel-version`
- **Fix:** `sudo apt install --install-recommends linux-generic-hwe-24.04`
- **Source:** ROCm/issues/5444, R10

### P1.6 GDN models на llama.cpp < b8765 → fallback CPU 11.87 t/s
- **Severity:** high (Qwen3.5/3.6 A3B family unusable)
- **Detection:** check tag in docker compose
- **Fix:** `image: ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049` или новее
- **Source:** llama.cpp#20354 (closed Mar 2026), R-llm-models

## P2 — High (performance / stability)

### P2.1 BIOS UMA > 2 GiB на Linux — sub-optimal
- **Severity:** medium (отнимает CPU-usable RAM, не даёт GPU)
- **Detection:** `agmind doctor::bios-uma`
- **Fix:** BIOS → UMA Frame Buffer = Auto / 512 MB (Linux управляет GTT)
- **Source:** R10-strix-halo-bios-uma.md

### P2.2 GTT pool sub-optimal без `ttm.pages_limit`
- **Severity:** high (GPU не видит >32 GiB модели)
- **Detection:** `cat /sys/class/drm/card*/device/mem_info_gtt_total` < 70% RAM
- **Fix:** GRUB cmdline `ttm.pages_limit=<94% RAM in 4KB pages>`
- **Source:** R10

### P2.3 HIP models >6 GB hang at runtime
- **Severity:** medium (большие модели не загружаются)
- **Fix:** `-dio` flag в llama-server либо `mmap=False`
- **Source:** R3

### P2.4 Vulkan DeviceLost при ubatch >2048 на long ctx
- **Severity:** medium (crash в зоне 65K-80K tokens)
- **Fix:** `--ubatch-size 256` / `-b 2048`
- **Source:** llama.cpp#20515, R3

### P2.5 Mesa 26.0+ vs Mesa 25.x — без kisak PPA = slower MoE
- **Severity:** low-medium (~9% pp perf на Strix Halo)
- **Fix:** `sudo add-apt-repository ppa:kisak/kisak-mesa && sudo apt upgrade`
- **Source:** R2, R3

### P2.6 `HSA_OVERRIDE_GFX_VERSION=11.5.1` с native gfx1151 wheels = subtle bugs
- **Severity:** medium (attention/conv corruption)
- **Fix:** НЕ ставить env var если используются AMD nightly wheels (native gfx1151)
- **Source:** R1

### P2.7 Vulkan mmproj degraded для specific images
- **Severity:** medium (VLM picture description corrupted)
- **Detection:** compare CUDA vs Vulkan output (нет CUDA в нашем стеке → manual check)
- **Fix:** fallback на CPU VLM либо HIP backend
- **Source:** llama.cpp#20081

### P2.8 Ollama vendored llama.cpp lags 56% от upstream
- **Severity:** high (production использовать Ollama nelzy)
- **Fix:** llama-server standalone (ghcr.io/ggml-org/llama.cpp:server-vulkan-bXXXX)
- **Source:** ollama#15601, R3

## P3 — Medium (tooling / build)

### P3.1 shaderc v2025.2 ломает Vulkan build
- **Severity:** high (compile fail)
- **Fix:** pin shaderc 2025.1
- **Source:** llama.cpp#15344, R3

### P3.2 rootless Docker/Podman не работает с ROCm на cgroups v2
- **Severity:** high (security workaround impossible)
- **Fix:** rootful Docker only
- **Source:** ROCm/#2860, R1

### P3.3 onnxruntime-rocm 1.22.x silent CPU fallback на gfx1151
- **Severity:** medium (perf не grow vs CPU)
- **Fix:** не использовать onnxruntime для GPU workloads на Strix Halo
- **Source:** R1

### P3.4 bitsandbytes 4/8-bit не собирается для gfx1151
- **Severity:** medium (нельзя quantize через bnb)
- **Fix:** use GGUF Q4/Q8 quants (через llama.cpp)
- **Source:** R1

### P3.5 jina-embeddings-v3 — no GGUF conversion
- **Severity:** low (use v4 if needed)
- **Source:** llama.cpp#9585, R5

### P3.6 MinIO Docker Hub frozen Sep 2025 (AGPL3 transition)
- **Severity:** medium (нет fresh patches на Docker Hub)
- **Fix:** `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772`
- **Source:** R12-versions-x86-may2026.md

### P3.7 Prometheus v2→v3 PromQL breaking
- **Severity:** low (если legacy queries)
- **Fix:** `holt_winters` → `double_exponential_smoothing`
- **Source:** R12

### P3.8 cAdvisor v0.55→v0.57 metric rename
- **Severity:** low
- **Fix:** dashboards review for `container_start_time_seconds` semantic change
- **Source:** R12

### P3.9 Loki 3.6→3.7 new tenancy schema
- **Severity:** low (single-tenant — ok)
- **Source:** R12

### P3.10 Dify upstream pins ОЧЕНЬ старые versions (postgres:15, redis:6)
- **Severity:** medium (override conflict potential)
- **Fix:** мы override до новых через services.yaml; verify compose не падает
- **Source:** R12

## P4 — Low (UX / niche)

### P4.1 MinerU нет official amd64 CPU Docker image
- **Severity:** low (M2 feature)
- **Fix:** self-build (Dockerfile есть в legacy) либо defer to M2
- **Source:** R12, R7

### P4.2 SGLang gfx1151 — никакого community port
- **Severity:** low (alternative engine planned для M3)
- **Source:** R4

### P4.3 MLC-LLM на gfx1151 — нет публичных бенчмарков
- **Severity:** low
- **Source:** R4

### P4.4 TEI ROCm — только MI200/MI300 (Instinct)
- **Severity:** low (M1 не использует TEI)
- **Fix:** llama-server pooling=cls для embed
- **Source:** R5

### P4.5 AGMIND_LLAMA_SERVER_URL — нет retry/backoff
- **Severity:** low (transient errors не retry'd)
- **Fix:** M2 — добавить urllib3-style retry
- **Source:** internal

### P4.6 typer не installed default — agmind app() exits with hint
- **Severity:** low (UX)
- **Detection:** `python -m agmind` без typer → SystemExit(2)
- **Fix:** `pip install typer rich` либо `pip install -e .[dev]`

### P4.7 pytest not installed на dev → 306 tests not runnable
- **Severity:** low (но critical для CI validation)
- **Fix:** `pip install pytest pytest-cov pytest-benchmark`

## Cross-cutting concerns

### CC.1 Audit script self-reference
- audit_forbidden.py RULES содержат запрещённые паттерны как regex strings
- Mitigation: `# audit: allow rule-self-reference` markers + per-file EXCLUDED_PATHS
- Файл сам в EXCLUDED_PATHS

### CC.2 Spec / docs / recons references к legacy
- AGMIND_MIGRATION_SPEC.md, README.md, CLAUDE.md упоминают GB10/CUDA/aarch64 как "откуда мигрируем"
- Mitigation: per-file EXCLUDED_PATHS (10 файлов)

### CC.3 ADR-0001/0002 в "proposed" status
- Не "accepted" — должны быть после migration shipped
- Fix in Phase I (git baseline)

### CC.4 migration_progress.json устарел
- Застрял на phase A — реальный state = G+ (cleanup + D1-D4 done)
- Fix in Phase I

### CC.5 BENCHMARKS.md = reference только
- Нет local runs (нет vulkaninfo/llama-cpp on dev)
- Fix in Phase H (hardware validation)
