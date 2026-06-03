# AGmind Troubleshooting

Cookbook для распространённых проблем. Каждая секция: симптом →
диагностика → fix.

## Quick reference

```bash
agmind doctor              # preflight diagnostics с fix hints
agmind status --json       # backend selection state
agmind deploy status       # docker compose ps
agmind deploy logs <svc>   # tail container logs
agmind deploy logs llama-llm -f --lines 200
```

## Section 1: Vulkan / GPU detection

### Symptom: `agmind status` → backend=cpu (no GPU)

```
Available: ['cpu']
Selected:  cpu / llama_cpp
Device:    AMD RYZEN AI MAX+ 395 w/ Radeon 8060S
```

#### Causes & fixes

**a) vulkaninfo не установлен**
```bash
sudo apt install vulkan-tools mesa-vulkan-drivers libvulkan1
vulkaninfo --summary  # должно показать RADV + GFX1151
```

**b) AMDVLK leaked**
```bash
ls /etc/vulkan/icd.d/amd_icd*.json   # эти файлы НЕ должны существовать
ls /etc/vulkan/implicit_layer.d/amd_icd*.json
# Если есть:
sudo rm -f /etc/vulkan/icd.d/amd_icd*.json \
           /etc/vulkan/implicit_layer.d/amd_icd*.json
sudo apt remove --purge amdvlk 2>/dev/null
```

**c) User не в render/video groups**
```bash
groups | grep -E "render|video"
# Если нет:
sudo usermod -aG video,render $USER
newgrp render
```

**d) /dev/dri permissions**
```bash
ls -la /dev/dri/  # render group
# Если group другая:
sudo chgrp render /dev/dri/renderD*
```

### Symptom: vulkaninfo показывает amdvlk вместо radv

```
driverName = AMD open source driver
```

#### Fix
```bash
sudo rm -f /etc/vulkan/icd.d/amd_icd*.json
export AMD_VULKAN_ICD=RADV
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json
vulkaninfo --summary
```

### Symptom: cooperative_matrix missing

```
agmind status:
  Capabilities:
    cooperative_matrix: False
```

#### Fix

Mesa < 25.2.8. Upgrade:
```bash
sudo add-apt-repository ppa:kisak/kisak-mesa
sudo apt upgrade
# Reboot recommended.
glxinfo | grep "OpenGL version"  # должно быть Mesa 26.0+
```

## Section 2: ROCm / HIP

### Symptom: rocminfo missing, ROCm backend unavailable

```bash
sudo apt install rocm-hip-sdk   # full ROCm 7.2
# Или см. AMD official:
# https://repo.radeon.com/amdgpu-install/
```

### Symptom: ROCm видит только 15.5 GiB VRAM

ROCm/issues/5444 — kernel < 6.17.0-19 HWE. Upgrade:

```bash
sudo apt install --install-recommends linux-generic-hwe-24.04
sudo reboot
uname -r  # должен быть ≥ 6.17.0-19
```

### Symptom: HSA error: invalid device function

Используете stock PyPI torch wheels. Они не работают на gfx1151.
Установите AMD nightly:

```bash
pip uninstall torch torchvision torchaudio
pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre \
    torch torchaudio torchvision
```

### Symptom: HIP model >6 GB hangs at load

```
llama-server: stuck on load_model
```

#### Fix

Add `-dio` flag (direct IO) либо `--no-mmap`:
```bash
# В compose service env:
LLAMA_CPP_ARGS: "--no-mmap"
```

Это уже default в `templates/services.yaml`.

## Section 3: GTT memory

### Symptom: GTT pool sub-optimal

```
agmind doctor:
  ⚠ gtt-pool   GTT pool only 62.5 GiB on 125 GiB RAM
```

#### Fix

GRUB cmdline (значение = 94% RAM в pages 4KB):

```bash
sudo tee /etc/default/grub.d/99-strixhalo-llm.cfg <<'EOF'
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=off ttm.pages_limit=30788044 ttm.page_pool_size=30788044 zswap.enabled=0"
EOF
sudo update-grub
sudo reboot

# Verify:
cat /sys/class/drm/card*/device/mem_info_gtt_total
# 30788044 × 4096 / 1024^3 = ~117.5 GiB
```

### Symptom: BIOS UMA > 2 GiB (sub-optimal на Linux)

```
agmind doctor:
  ⚠ bios-uma   BIOS UMA frame buffer = 32.0 GiB
```

#### Fix

Reboot в BIOS → AMD CBS → GFX Configuration → UMA Frame Buffer Size →
**Auto** или **512 MB** (минимум).

На Linux большой UMA не даёт benefits — Linux управляет через GTT.
Большой UMA только отбирает у CPU.

## Section 4: Docker / Compose

### Symptom: `docker compose up` fails — image pull error

```
Error response from daemon: manifest for X:Y not found
```

#### Causes

**a) Network unreachable / DNS issue**
```bash
docker pull alpine:3.21  # smoke
```

**b) Image pinned digest mismatched** (R12 digests verified 2026-05-19;
если registry убрал image — нужен update)

```bash
agmind audit  # должен показать 0; если digest pin broken — нужен новый
```

### Symptom: container restarts in loop

```bash
agmind deploy ps
# RESTARTING repeatedly
```

#### Diagnose
```bash
agmind deploy logs <service> --lines 200
```

Common causes:
- Postgres: invalid `POSTGRES_PASSWORD` (empty?)
- Redis: bind permission issue
- llama-llm: model file missing (`/models/X.gguf`)
- Dify: postgres not ready (depends_on не помогло — `pg_isready` нужен)

## Section 5: LLM inference

### Symptom: llama-llm fails on startup

```
agmind deploy logs llama-llm
# unable to open model: /models/X.gguf
```

#### Fix

Model not downloaded:
```bash
agmind models download
agmind deploy restart llama-llm
```

Или wrong filename — check inventory:
```bash
agmind models path llm   # absolute path
ls -la $(agmind models path llm)
```

### Symptom: Vulkan DeviceLost error mid-inference

```
vk::DeviceLostError at ctx ~80000 tokens
```

#### Fix

llama.cpp issue #20515. Pin smaller batch:

```yaml
# templates/services.yaml::llama-llm:
extra_args:
  - "-ub"
  - "2048"   # вместо 4096+ default
  - "-b"
  - "2048"
```

### Symptom: GDN model fallback to CPU (super slow)

```
Qwen3.6-35B-A3B running at 11.87 t/s instead of expected 60 t/s
```

llama.cpp < b8765 (GDN Vulkan shader не landed). Update image:

```yaml
# templates/services.yaml::llama-llm:
image: ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049
```

Apply:
```bash
agmind deploy pull
agmind deploy restart llama-llm
```

## Section 6: Network / mDNS

### Symptom: `agmind-dify.local` не резолвится

```bash
# Check avahi-daemon
systemctl status avahi-daemon
sudo systemctl enable --now avahi-daemon

# Verify mDNS resolution
avahi-resolve -n agmind-dify.local
```

### Symptom: Traefik 502 Bad Gateway

```bash
agmind deploy logs traefik
# upstream connect refused
```

#### Diagnose

```bash
# Is target service up?
agmind deploy status
docker compose -f /opt/agmind/docker-compose.yml ps dify-api

# Target service health from inside its container
docker compose -f /opt/agmind/docker-compose.yml exec dify-api \
  wget -qO- http://localhost:5001/health
```

## Section 7: Cluster

### Symptom: master не видит workers

```bash
agmind status --json
# available_backends only has 'cpu' on master, workers не появляются
```

#### Diagnose

**a) Cluster config missing**
```bash
ls -la /etc/agmind/cluster.yaml
# Если нет — Ansible cluster role не отработал
sudo ansible-playbook -i ansible/inventory/cluster.yml \
    ansible/install.yml -t services
```

**b) Worker llama-server unreachable**
```bash
curl http://agmind-worker-01.local:8080/health
# Connection refused → worker down или firewall
```

**c) mDNS не работает между LAN segments**
```bash
# На master:
avahi-resolve -n agmind-worker-01.local
# Empty → use static IP в cluster.yml вместо .local
```

### Symptom: Routing strategy not effective

`agmind status` показывает strategy round-robin но все requests идут на
один worker.

#### Diagnose

```bash
# Health probe всех peers:
python3 -c "
from agmind.cluster import load_cluster_config, probe_all
cfg = load_cluster_config()
healths = probe_all(cfg.peers)
for h in healths:
    print(f'{h.peer.name}: alive={h.is_alive} inflight={h.inflight}')
"
```

Если **только один alive** — router правильно выбирает только его.
Other workers dead → check их status.

## Section 8: Logs / observability

### Where to look

| Service | Location |
|---------|----------|
| llama-llm/embed/rerank | `agmind deploy logs llama-llm` |
| Dify API | `agmind deploy logs dify-api` |
| Traefik | `agmind deploy logs traefik` |
| Postgres | `agmind deploy logs postgres` |
| Grafana | http://localhost:3002 (Loki datasource) |
| Audit trail | `agmind audit --json` |
| Host syslog | `journalctl -u docker -u amdgpu --since today` |

### Loki queries

```logql
# All errors последний час
{job="docker"} |= "error" | line_format "{{.container}}: {{.message}}"

# llama-llm slow requests
{container="agmind-llama-llm"} |~ "took [0-9]+s"
```

## Section 9: Emergency rollback

Old AGmind Bash installer (DGX Spark / aarch64) больше не доступен — мы
удалили `legacy/` в cleanup. Если нужен legacy — restore from git:

```bash
git log --oneline | grep "legacy"
git checkout <commit-before-cleanup> -- legacy/
cd legacy/gb10
sudo bash install.sh  # requires DGX Spark hardware
```

## Section 10: Get help

1. **Doctor first:** `agmind doctor` — 9 checks с fix hints
2. **Logs:** `agmind deploy logs <service> --lines 500`
3. **Status:** `agmind status --json`
4. **Audit:** `agmind audit` (forbidden patterns)
5. **ADRs:** `docs/adr/0001-migration-to-x86-strix-halo.md`,
   `docs/adr/0002-compute-backend-abstraction.md`
6. **Incident runbook:** [`docs/operations/incident-response.md`](operations/incident-response.md)
   — severity matrix, decision trees, and recovery procedures.
