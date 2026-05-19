# Setup: ROCm 7.2.3 на Strix Halo (gfx1151)

> Closes `DEF-ROCM-VERSION-GFX1151`. Все URLs verified через `curl -I` (HTTP 200) на 2026-05-19.

## Контекст

AMD Strix Halo (Ryzen AI Max+ 395, gfx1151) — APU с unified memory. Ubuntu 24.04 stock ROCm 5.7 **НЕ поддерживает** gfx1151. Минимальная версия с native gfx1151 support — **ROCm 7.1.1** (релиз 26 ноября 2025). Latest stable на дату написания — **ROCm 7.2.3** (30 апреля 2026).

Vulkan (RADV) — primary inference path в AGmindx86. ROCm — secondary, для embed_batch/PP workloads (ADR-0002, ADR-0004).

## Pre-checks

```bash
# 1. Kernel version (минимум для gfx1151 на HWE: 6.17.0-19.19~24.04.2)
uname -r
# наш стенд (verified): 6.17.0-29-generic ✓

# 2. linux-firmware version — критично!
# Firmware 20251125 ломает ROCm на gfx1151 (Hardware Corner, Framework community).
# Нужно ≥20260110.
dpkg -l linux-firmware | tail -1

# 3. GPU реально gfx1151
lspci -d 1002:1586 | head -1
# expected: ... Device 1586 (rev c1)
```

## Установка ROCm 7.2.3 (verified sequence)

```bash
# Prerequisites
sudo apt update
sudo apt install -y wget curl

# 1. Скачать amdgpu-install package
# URL verified HTTP 200 на 2026-05-19, last-modified 2026-04-30 21:24:48 GMT, 16900 bytes
cd /tmp
wget https://repo.radeon.com/amdgpu-install/7.2.3/ubuntu/noble/amdgpu-install_7.2.3.70203-1_all.deb

# 2. Установить wrapper + добавить ROCm apt repo
sudo apt install -y ./amdgpu-install_7.2.3.70203-1_all.deb
sudo apt update

# 3. Headers для DKMS (kernel 6.17 HWE на Ubuntu 24.04)
sudo apt install -y "linux-headers-$(uname -r)" "linux-modules-extra-$(uname -r)"

# 4. ROCm + amdgpu kernel module
sudo apt install -y python3-setuptools python3-wheel
sudo usermod -aG render,video "$USER"
sudo apt install -y amdgpu-dkms rocm

# 5. Обновить linux-firmware (если ≤ 20251125 — ОБЯЗАТЕЛЬНО)
sudo apt install -y linux-firmware
dpkg -l linux-firmware  # убедиться что версия > 20260110

# 6. Reboot — нужен для load amdgpu kernel module
sudo reboot
```

## Verify (после reboot)

```bash
# Expected output (real Strix Halo, ROCm 7.2.3):
rocminfo | grep -E "gfx1151|Agent|ROCk"
#   ROCk module version 6.16.13 is loaded
#   Agent 2: gfx1151 (GPU)
#     Name: gfx1151
#     ISAs: amdgcn-amd-amdhsa--gfx1151, amdgcn-amd-amdhsa--gfx11-generic
#     Compute Unit: 40
#     Max Clock 2900 MHz

# rocm-smi работает (VRAM 0% expected — APU не имеет dedicated VRAM)
rocm-smi

# amd-smi — НЕ ИСПОЛЬЗОВАТЬ
# Сломан на gfx1151 (ROCm/ROCm#6035, открыт 15 Mar 2026, статус: triage без фикса).
# Все метрики возвращают N/A кроме EDGE temp.
# AGmindx86 использует scripts/amdgpu_textfile.sh вместо amd-smi (R13).
```

## Verify в AGmind

```bash
cd ~/AGmindx86
.venv/bin/agmind status
# Should show: rocm в available backends
.venv/bin/pytest tests/compute/test_contract.py -v -k rocm
# test_rocm_backend_available_if_rocminfo_gfx1151 — passes
```

## Известные проблемы / workarounds

| Issue | Status | Workaround |
|---|---|---|
| **ROCm/ROCm#6035** amd-smi N/A на gfx1151 | Open, triage | Используем `scripts/amdgpu_textfile.sh` (R13) — читает sysfs напрямую |
| **ROCm/ROCm#5665** GPU hang на ROCm 7.1 + AI + video encoding | Закрыт в 7.2.3 | Используем 7.2.3 |
| **firmware 20251125** регрессия | Известна | `apt install linux-firmware` ≥ 20260110 |
| **HSA_OVERRIDE_GFX_VERSION=11.5.1** | НЕ нужен на 7.2.x | В AMD docs не требуется; community workaround для старых версий (ollama#14855) |
| **pytorch#171687** decode memcpy-bound | Open | Не наш case (мы через llama.cpp, не PyTorch) |
| **rocm7-nightlies cap 64GB** | Bug nightly | Если нужно >64GB — использовать stable 7.2.3, не nightly |

## llama-cpp-python с HIP build (gfx1151)

Точные `CMAKE_ARGS` для нашего железа **не подтверждены** в официальной AMD doc. Community проекты (kyuz0 toolboxes, hogeheer499 guide) используют containerized pre-builds, не дают чистый pip workflow.

Текущий `docker/Dockerfile.rocm` использует:
```
CMAKE_ARGS=-DGGML_HIP=ON \
           -DAMDGPU_TARGETS=gfx1151 \
           -DGPU_TARGETS=gfx1151 \
           -DGGML_HIP_NO_VMM=ON \
           -DGGML_HIP_ROCWMMA_FATTN=ON \
           -DGGML_HIP_MMQ_MFMA=ON
```

Это R-recon R3 тема — флаги взяты из spec'а до Phase H', потребуют verify на реальном железе после установки ROCm 7.2.3.

## Альтернатива: ROCm nightly для bleeding-edge

Нужно только если 7.2.3 не работает. URL verified HTTP 200:
```bash
pip install --index-url https://rocm.nightlies.amd.com/v2-staging/gfx1151/ --pre \
    torch torchaudio torchvision
```

⚠️ Nightly cap memory allocation 64GB (см. kyuz0). Для models >64GB — stable 7.2.3.

## Sources (все verified HTTP 200 на 2026-05-19)

- [repo.radeon.com/amdgpu-install/](https://repo.radeon.com/amdgpu-install/) — index of releases
- [ROCm 7.2.3 noble package](https://repo.radeon.com/amdgpu-install/7.2.3/ubuntu/noble/amdgpu-install_7.2.3.70203-1_all.deb)
- [AMD Strix Halo system optimization](https://rocm.docs.amd.com/en/latest/how-to/system-optimization/strixhalo.html)
- [ROCm/ROCm#6035 (amd-smi N/A)](https://github.com/ROCm/ROCm/issues/6035)
- [tinycomputers.io ROCm 7.0→7.2 upgrade on gfx1151](https://tinycomputers.io/posts/upgrading-rocm-7.0-to-7.2-on-amd-strix-halo-gfx1151.html)
- [Framework community Linux+ROCm Jan 2026](https://community.frame.work/t/linux-rocm-january-2026-stable-configurations-update/79876)
- [kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes)
- [Hardware Corner: Strix Halo ROCm firmware fix](https://www.hardware-corner.net/strix-halo-rocm-firmware-fix/)

## Что AGmindx86 спецификации зафиксировано

- `ansible/group_vars/all.yml::agmind_rocm_min` — теперь **7.2.0** (минимум для gfx1151 + stable)
- `ansible/group_vars/all.yml::agmind_firmware_min` — **20260110**
- `ansible/group_vars/all.yml::agmind_kernel_min` — **6.18.4** (или HWE 6.17.0-19+)
