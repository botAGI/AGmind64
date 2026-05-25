# AGmind — Hardware setup (Strix Halo, x86_64)

> Этот документ — **черновик**. Финализируется после фазы D (когда
> известны окончательные бенчмарки). Часть рекомендаций ниже выходит за
> пределы Dockerfile (хост-side настройки) — пользователь применяет их
> на BIOS / kernel / systemd до запуска agmind.

## Поддерживаемое железо

**Primary:** AMD Strix Halo (Ryzen AI Max+ 385 / 390 / 395)
- CPU: Zen 5, 16C/32T
- iGPU: Radeon 8060S (gfx1151, RDNA 3.5, 40 CU)
- RAM: 32 / 64 / 128 GB LPDDR5X unified
- Bandwidth: ~215 GB/s effective (256 GB/s theoretical)
- PCI IDs: `1002:1586` (primary), `1002:150e` (variant)

**Secondary** (x86_64 generic): любой Zen 4 / Zen 5 / Ice Lake /
Sapphire Rapids с AVX-512 — CPU-only inference, без AMD GPU.

## Минимальные системные требования

### Ubuntu 24.04

| Component | Min | Recommended |
|-----------|-----|-------------|
| Kernel | 6.17.0-19 HWE | 6.18.4+ mainline |
| Mesa (для Vulkan) | 25.2.8 | 26.0+ через `ppa:kisak/kisak-mesa` |
| linux-firmware | 20260110+ | 20260110+ or vendor-pinned newer build |
| ROCm (если используется HIP backend) | 7.2.0 | 7.2.3 stable |
| amdgpu-dkms | matches ROCm | use the package that matches the selected ROCm release |

```bash
# kernel HWE на Ubuntu 24.04:
sudo apt install --install-recommends linux-generic-hwe-24.04

# Mesa 26+ через kisak PPA:
sudo add-apt-repository ppa:kisak/kisak-mesa
sudo apt upgrade

# AMD ROCm 7.2 (опционально, только для HIP backend):
wget https://repo.radeon.com/amdgpu-install/...
sudo apt install ./amdgpu-install_*.deb
sudo amdgpu-install --usecase=rocm,hiplibsdk --no-dkms

# User в render+video группы:
sudo usermod -aG video,render $USER
newgrp render  # или ре-логин
```

## BIOS settings (Strix Halo)

**На Linux:** ставить **минимум** для UMA Frame Buffer (обычно 512 MB).
Linux управляет GTT через `ttm.pages_limit`, который намного гибче.

Vendor BIOS menu:
- **Framework Desktop:** `BIOS > Advanced > AMD CBS > NBIO > GFX Configuration > UMA Frame Buffer Size`
- **GMKtec EVO-X2:** `Advanced > GFX Configuration > iGPU Configuration > UMA Mode = UMA_SPECIFIED` (требует BIOS 1.04+)
- **HP ZBook Ultra G1a:** `Advanced BIOS > Graphics Options > UMA Frame Buffer Size`
- **Beelink GTR9 Pro:** AMI BIOS GTRP110+, стандартное AMD CBS меню

Также включить:
- **Above 4G Decoding:** Enabled
- **Resizable BAR:** Enabled
- **IOMMU:** default (kernel cmdline override ниже)

## Kernel cmdline (GRUB)

```bash
# /etc/default/grub.d/99-strixhalo-llm.cfg
GRUB_CMDLINE_LINUX_DEFAULT="amd_iommu=off ttm.pages_limit=31457280 ttm.page_pool_size=31457280"
```

Расшифровка для 128 GB системы:
- `amd_iommu=off` — +6% memory bandwidth
- `ttm.pages_limit=31457280` = 120 GiB pool для GPU (120 × 1024³ ÷ 4096)
- `ttm.page_pool_size` тот же

**Scale для других объёмов RAM:**
- 64 GB system → `ttm.pages_limit=15728640` (60 GiB pool)
- 32 GB system → `ttm.pages_limit=7340032` (28 GiB pool)
- 16 GB system → `ttm.pages_limit=3145728` (12 GiB pool, S-tier only)

После правки:
```bash
sudo update-grub
sudo reboot
```

## sysctl

```bash
# /etc/sysctl.d/99-strixhalo-llm.conf
vm.swappiness=10
vm.overcommit_memory=1
vm.max_map_count=1048576
```

```bash
sudo sysctl --system
```

## Disable zswap, enable zram

zswap competes с iGPU за memory bandwidth — отключаем. Маленький zram
оставляем как safety net.

```bash
# Disable zswap
echo N | sudo tee /sys/module/zswap/parameters/enabled

# kernel cmdline: добавить `zswap.enabled=0`
```

```bash
# /etc/systemd/zram-generator.conf.d/zram.conf
[zram0]
zram-size = ram / 8
compression-algorithm = zstd
swap-priority = 100
```

## Vulkan driver — обязательно RADV, НЕ AMDVLK

AMDVLK официально discontinued AMD 2025-09-15 + имеет hard cap 2 GiB
на VkDeviceMemory allocation (ломает LLM ≥30B). RADV — единственный
production-ready Vulkan driver на gfx1151.

```bash
# Если AMDVLK был установлен — снести:
sudo apt remove --purge amdvlk 2>/dev/null
sudo rm -f \
  /etc/vulkan/icd.d/amd_icd64.json \
  /etc/vulkan/icd.d/amd_icd32.json \
  /etc/vulkan/implicit_layer.d/amd_icd64.json \
  /etc/vulkan/implicit_layer.d/amd_icd32.json

# Проверка:
vulkaninfo --summary | grep "driverName"
# Должно быть только: driverName = radv
```

В нашем `docker/Dockerfile.vulkan` это делается на образ-этапе.
Хост-side проверка — preflight.

## Tier-based model selection

```
RAM / Effective GPU pool → Recommended LLM tier
─────────────────────────────────────────────────
16 GB / 8 GB → S: 7B Q4 (~4 GB)
32 GB / 16 GB → M: 13-14B Q4 (~8 GB)
64 GB / 32 GB → L: 30-32B Q4 (~18 GB)
128 GB / 96+ GB → XL: 70B Q4 (~42 GB) или XXL: 120B MoE MXFP4 (~63 GB)
```

agmind при старте детектит `mem_info_gtt_total` через
`/sys/class/drm/cardN/device/` и автоматически рекомендует tier.

## Diagnostic commands

```bash
# Кратко: устройства, версии, память
agmind doctor

# Manually:
cat /sys/class/drm/card0/device/mem_info_vram_total   # BIOS UMA size
cat /sys/class/drm/card0/device/mem_info_gtt_total    # эффективный GPU pool
cat /sys/module/ttm/parameters/pages_limit            # current TTM limit
rocm-smi --showmeminfo all
dmesg | grep -E "amdgpu.*(VRAM|GTT)"
```

**Не использовать:**
- `amd-smi` (broken на gfx1151, ROCm/issues/6035) — все метрики N/A
- `rocm-smi` underflow на ROCm 7.1 + kernel 6.14 — обновить ядро

## Suspend / hibernation

**Важно:** Strix Halo + iGPU + загруженная LLM модель — suspend
ненадёжен (driver bug ROCm/issues/5665, 5724, 5590).

**Workaround:**
```bash
# systemd-suspend.d hook
# /etc/systemd/system/agmind-pre-suspend.service
[Unit]
Description=Unload agmind models before suspend
Before=sleep.target
StopWhenUnneeded=yes

[Service]
Type=oneshot
ExecStart=/usr/local/bin/agmind unload-all
RemainAfterExit=yes

[Install]
WantedBy=sleep.target
```

```bash
sudo systemctl enable agmind-pre-suspend.service
```

## Docker daemon

```bash
# /etc/docker/daemon.json (опционально для производительности)
{
  "default-runtime": "runc",
  "default-shm-size": "16G",
  "storage-driver": "overlay2",
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" }
}
```

После правки:
```bash
sudo systemctl restart docker
```

## docker run boilerplate (для agmind контейнеров)

```bash
# Vulkan backend (LLM, embed, rerank через llama-server):
docker run -d \
  --device=/dev/dri \
  --group-add video \
  --group-add render \
  --security-opt seccomp=unconfined \
  --shm-size=16G \
  --restart=unless-stopped \
  -e AMD_VULKAN_ICD=RADV \
  -e VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
  -v /var/lib/agmind/models:/models:ro \
  agmind/vulkan:dev

# ROCm backend (для PyTorch / batch / GDN-моделей):
docker run -d \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --group-add render \
  --security-opt seccomp=unconfined \
  --cap-add=SYS_PTRACE \
  --ipc=host \
  --shm-size=16G \
  --restart=unless-stopped \
  agmind/rocm:dev
```

**Note:** `--privileged` НЕ нужен. Rootless Docker НЕ поддерживается
для ROCm на cgroups v2 в 2026.

## Hot validation (что проверить после установки)

```bash
# 1. Kernel
uname -r  # >= 6.17 (HWE) или 6.18.4+ mainline

# 2. amdgpu kernel module
lsmod | grep amdgpu

# 3. Devices
ls /dev/kfd /dev/dri/

# 4. User groups
groups | grep -E "render|video"

# 5. Vulkan
vulkaninfo --summary | grep -E "driverName|deviceName|apiVersion"
# Expected:
#   driverName = radv
#   deviceName = AMD Radeon Graphics (RADV GFX1151) или (RADV STRIX_HALO)
#   apiVersion = 1.4.xxx

# 6. ROCm (если нужен HIP backend)
rocminfo | grep -E "gfx1151|HSA"

# 7. Mesa version
glxinfo | grep "OpenGL version"  # >= Mesa 25.2.8

# 8. Effective GPU pool
cat /sys/class/drm/card0/device/mem_info_gtt_total

# 9. agmind health
agmind doctor   # после установки агента
```

## Сравнение с GB10 (legacy reference)

| | GB10 (legacy) | Strix Halo (current) |
|---|---------------|----------------------|
| ISA | aarch64 | x86_64 |
| Compute | NVIDIA CUDA / Blackwell | AMD ROCm / Vulkan / RDNA 3.5 |
| Memory | 128 GB unified LPDDR5X (273 GB/s) | 128 GB unified LPDDR5X (256 GB/s, ~215 measured) |
| Driver | NVIDIA 580.x pinned | amdgpu-dkms |
| Limit | sm_121 specifics, FlashInfer FP8 broken, dcgm broken | gfx1151 quirks, AMDVLK broken, amd-smi broken |
| tg 70B Q4 | ~5 t/s | ~5-12 t/s (паритет) |
| pp 70B Q4 | ~75-105 t/s | ~25-35 t/s (2.5-3× медленнее) |

## Поддержка / troubleshooting

См. `docs/TROUBLESHOOTING.md` (будет создан после фазы E).
