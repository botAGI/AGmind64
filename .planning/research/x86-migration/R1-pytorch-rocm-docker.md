---
recon: R1 — PyTorch ROCm + onnxruntime-rocm + Docker AMD container runtime
date: 2026-05-19
status: completed (replacement after watchdog stall)
source_agent: general-purpose (~14 sources)
related: R3, R4, R5, R10
---

# R1: PyTorch ROCm + onnxruntime-rocm + Docker AMD

## TL;DR

1. **Stock PyPI / pytorch.org wheels НЕ работают на gfx1151** — `HIP error:
   invalid device function`. Единственный production source:
   `https://rocm.nightlies.amd.com/v2/gfx1151/` (AMD nightly) или
   `repo.radeon.com/rocm/manylinux/rocm-rel-7.2/` (stable).
2. **onnxruntime-rocm 1.22.x на gfx1151 НЕ production-ready** — нет
   gfx1151-specific kernels, silent CPU fallback risk. Embedding workload
   через PyTorch+ROCm или CPU fallback (Zen5 16C даёт ~120-200 docs/sec
   на bge-m3-small).
3. **Docker AMD container runtime** = стандартный docker, без spec
   toolkit. Канонические флаги ниже.
4. **HSA_OVERRIDE_GFX_VERSION=11.5.1** — с AMD nightly gfx1151 wheels
   **НЕ нужен** (native kernels). С stock wheels — нужен.
5. **`PYTORCH_HIP_ALLOC_CONF=backend:malloc` КРАШИТ** — не ставить.
6. **`TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`** — критично, +19x
   speedup на attention (44ms → 2.3ms). Undocumented но работает.
7. **Rootless Docker/Podman + ROCm НЕ работает** на cgroups v2 в 2026 —
   rootful only.

## PyTorch ROCm install path для gfx1151

```bash
# AMD nightly (recommended, master path для gfx1151):
pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre \
    torch torchaudio torchvision

# Stable через repo.radeon.com (ROCm 7.2):
pip install \
  https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/triton-3.5.1%2Brocm7.2.0.gita272dfa8-cp312-cp312-linux_x86_64.whl \
  https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/torch-2.9.1%2Brocm7.2.0.lw.git7e1940d4-cp312-cp312-linux_x86_64.whl \
  https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/torchvision-0.24.0%2Brocm7.2.0.gitb919bd0c-cp312-cp312-linux_x86_64.whl

# Fallback (TheRock community, требует numpy<2):
pip install --extra-index-url https://github.com/scottt/rocm-TheRock/releases/v6.5.0rc-pytorch \
    torch  # torch 2.7.0a0+gitbfd8155, ROCm 6.5
```

**Note:** спека Part 5.8 указывает `--index-url https://download.pytorch.org/whl/rocm6.3` — это **wrong для gfx1151**. Нужно обновить на AMD nightly.

## Critical env vars

| Var | Value | When |
|-----|-------|------|
| `PYTORCH_ROCM_ARCH` | `gfx1151` | always |
| `PYTORCH_ALLOC_CONF` | `expandable_segments:True` | always (fragmentation workaround) |
| `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL` | `1` | always (+19x SDPA) |
| `HIP_PLATFORM` | `amd` | защита от ambiguous |
| `HSA_OVERRIDE_GFX_VERSION` | `11.5.1` | **только** если NOT using AMD nightly |
| `MIOPEN_LOG_LEVEL` | `3` | заглушить шум |
| `ROCR_VISIBLE_DEVICES` / `HIP_VISIBLE_DEVICES` | `0` | если несколько GPU |
| **`PYTORCH_HIP_ALLOC_CONF`** | **НЕ СТАВИТЬ** | крашит |

## Known PyTorch bugs на gfx1151

ROCm #6034 — 5 критических bf16 багов:

| # | Trigger | Symptom | Workaround |
|---|---------|---------|------------|
| 1 | TOTAL_BATCH=2^13/2^14 + DEVICE_BATCH=16 | NaN в 15 шагов | use ≥2^15 |
| 2 | HEAD_DIM=32 в attention | NaN crash | HEAD_DIM=64 (+1.11% perf) |
| 3 | DEPTH ≥ 12 слоёв | timeout/NaN ~23 шаг | ограничить DEPTH=10 |
| 4 | ASPECT_RATIO=128 | timeout | ≤64 |
| 5 | LR ≥ 0.20 | NaN/crash | cap ≤0.15 |

**LLM decode bottleneck:** pytorch #171687 — 92-95% времени в
`hipMemcpyWithStream` (KV cache rematerializes в host UMA каждый шаг).
Workaround в мае 2026 **отсутствует** — ждать фикса.

**bitsandbytes 4-bit/8-bit:** missing `libbitsandbytes_rocm72.so` → не
работает на gfx1151. Quantization через GGUF/Q-формат вместо bnb.

**Flash Attention v2:** native underperforms, нужен AOTriton
(`TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`).

**hipBLAS отдельные GEMM dims:** `HIPBLAS_STATUS_NOT_SUPPORTED` на
конкретных размерах (m=1280 n=8192 k=5120 etc).

**rocBLAS standalone:** ~8.9% efficiency. С hipBLASLt — ~64.4%.
**Всегда предпочитать hipBLASLt** (`ROCBLAS_USE_HIPBLASLT=1`).

## onnxruntime-rocm status

PyPI `onnxruntime-rocm` 1.22.2.post1 (2025-09-11) — community maintained,
не AMD official. **Скомпилирован под mainline (gfx9xx, gfx1100), на
gfx1151 fall back на CPU молча.** TheRock issue #2945 явно:
"missing onnxruntime-rocm wheel for TheRock 7.11" → face detection
падает на CPU с `libcublasLt.so.12 missing`.

**Для embedding workload (bge-m3, sentence-transformers):**
- Через PyTorch + sentence-transformers напрямую (NO ONNX) ✅
- Через llama.cpp embed mode (R5) ✅ **primary**
- onnxruntime CPU (Zen5 16C, ~120-200 docs/sec) ✅ fallback
- onnxruntime-rocm на gfx1151 ❌

## Docker для ROCm на gfx1151

**Спец-toolkit не нужен** (нет AMD-аналога nvidia-container-toolkit
который работает в production). Прямой проброс kernel devices:

```bash
docker run -it \
    --device=/dev/kfd \
    --device=/dev/dri \
    --group-add video \
    --group-add render \
    --security-opt seccomp=unconfined \
    --cap-add=SYS_PTRACE \
    --ipc=host \
    --shm-size=16G \
    -v $HOME:$HOME \
    rocm/dev-ubuntu-24.04:7.2-complete
```

**Обязательные:** `/dev/kfd` + `/dev/dri`, `--group-add video --group-add render`.

**Сильно рекомендуемые в 2026:**
- `--security-opt seccomp=unconfined` — **да, всё ещё нужен** для HSA ops
- `--shm-size=16G` — иначе DataLoader bus error
- `--ipc=host` — для PyTorch DataLoader / NCCL-style
- `--cap-add=SYS_PTRACE` — для debuggers / tracing

**Опциональные:**
- `--privileged` — НЕ рекомендуется в production
- `--network=host` — для inter-container low latency

## cgroups v2 + Ubuntu 24.04

- По умолчанию cgroups v2.
- `render` group замещает `video` для `/dev/dri/renderD*` в v2.
- Host user в обеих: `sudo usermod -aG video,render $USER && newgrp render`.
- Контейнер: `--group-add video --group-add render` оба.

**Rootless Docker/Podman + ROCm:** НЕ работает на cgroups v2 в 2026
(ROCm #2860). Device cgroups в user namespace через eBPF не работают.
Workaround `chmod 666 /dev/kfd /dev/dri/renderD*` — security-неприемлемо.

**Рекомендация: rootful Docker для production на gfx1151.**

## Image sizes

| Image | Size | Use |
|-------|------|-----|
| `rocm/dev-ubuntu-24.04:7.2-complete` | 20-30 GB | development, build, debug |
| `rocm/dev-ubuntu-24.04:7.2-runtime` | ≤10 GB | production |

**Use `:7.2-runtime` for production** (size matters).

## Update в AGMIND_MIGRATION_SPEC.md (proposed)

Part 1.3 «Запреты»:
```
| Build flags | -march=native в shippable; HSA_OVERRIDE_GFX_VERSION с
gfx1151 native wheels; PYTORCH_HIP_ALLOC_CONF=backend:malloc (crashes) |
```

Part 5.8 (Dockerfile.rocm):
- Заменить `--index-url https://download.pytorch.org/whl/rocm6.3` на
  `--index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre`
- Base image: `rocm/dev-ubuntu-24.04:7.2-runtime` (НЕ `:7.0-complete`)
- Добавить env vars: `PYTORCH_ALLOC_CONF=expandable_segments:True`,
  `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`,
  `ROCBLAS_USE_HIPBLASLT=1`, `MIOPEN_LOG_LEVEL=3`
- Удалить `HSA_OVERRIDE_GFX_VERSION=11.5.1` из ENV если using AMD
  nightly (или сделать conditional)

## Host preflight (Python скрипт для agmind/install/)

Checks:
1. `/dev/kfd`, `/dev/dri` exist
2. amdgpu kernel module loaded
3. Host user in render + video groups
4. `rocminfo` detects gfx1151
5. Docker installed
6. cgroups v2 (recommended)
7. `/dev/kfd` group в render/video/kfd
8. kernel ≥ 6.18.4 (Strix Halo)

## Sources

- TheRock pytorch wheels discussion (https://github.com/ROCm/TheRock/discussions/655)
- TinyComputers ROCm 7.0→7.2 on gfx1151
- Framework Community ROCm Jan 2026 stable
- pytorch #171687 (hipMemcpyWithStream LLM decode)
- ROCm #6034 (5 critical bf16 bugs)
- ROCm #5853 (segfault nightly torch)
- TheRock #2945 (bitsandbytes + precision)
- AMD nightlies index gfx1151 (https://rocm.nightlies.amd.com/v2/gfx1151/)
- AMD ROCm Docker docs
- onnxruntime-rocm PyPI 1.22.2.post1
- onnxruntime MIGraphX EP docs
- PyTorch ROCm compatibility matrix
- ROCm #2860 (rootless Podman)
- llm-tracker Strix Halo
