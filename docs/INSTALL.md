# AGmind Install Guide

Детальный setup AGmind на AMD Strix Halo (gfx1151).

## Architecture overview

```
┌─ Operator / Control Node ─────────────┐
│                                       │
│  ansible-playbook install.yml         │  ← bootstrap + apply config
│   │                                   │
│   └─→ SSH (или local)                 │
│                                       │
└──┬────────────────────────────────────┘
   │
   ▼
┌─ AGmind Master Node ──────────────────┐
│                                       │
│  Host setup:                          │
│    - apt: vulkan-tools, docker-ce     │
│    - GRUB: ttm.pages_limit, IOMMU off │
│    - sysctl: swappiness/overcommit    │
│    - groups: render, video            │
│    - AMDVLK purge                     │
│                                       │
│  Python agmind/ (venv в /opt/agmind):│
│    - CLI: agmind doctor/status/...    │
│    - compute layer (HTTP к llama-server)│
│                                       │
│  Docker stack (docker compose):       │
│    ┌──────────────────────────────┐   │
│    │ llama-llm (Vulkan RADV)      │   │
│    │ llama-embed (bge-m3)         │   │
│    │ llama-rerank (bge-reranker)  │   │
│    │ qdrant                       │   │
│    │ Dify (api/web/worker/plugin) │   │
│    │ postgres / redis             │   │
│    │ docling-serve-cpu            │   │
│    │ nginx (reverse proxy)        │   │
│    │ [observability: P/G/L/A]     │   │
│    └──────────────────────────────┘   │
│                                       │
└───────────────────────────────────────┘
```

## Phase 1: Host preparation

### 1.1 BIOS settings (Strix Halo)

Войдите в BIOS (обычно F2 при загрузке) и установите:

| Setting | Path | Value |
|---------|------|-------|
| **UMA Frame Buffer** | Advanced → AMD CBS → NBIO → GFX Configuration | **Auto** или **512 MB** (минимум — Linux сам управляет через GTT) |
| **Above 4G Decoding** | Advanced → PCI Configuration | **Enabled** |
| **Resizable BAR** | Advanced → PCI Configuration | **Enabled** |
| **IOMMU** | Advanced → AMD CBS → NBIO | **Default** (kernel cmdline override ниже) |
| **Secure Boot** | Boot → Secure Boot | **Disabled** (для unsigned amdgpu modules) |

### 1.2 Kernel & firmware

```bash
# Ubuntu 24.04 HWE kernel (≥ 6.17.0-19)
sudo apt install --install-recommends linux-generic-hwe-24.04

# Verify
uname -r  # должен быть 6.17.0-19+ или 6.18.x

# linux-firmware ≥ 20260110
apt list --installed linux-firmware
# Если старее — apt upgrade

# Optional: Mesa 26+ через kisak fresh PPA (recommended для MoE perf)
sudo add-apt-repository ppa:kisak/kisak-mesa
sudo apt upgrade
```

### 1.3 GRUB cmdline

```bash
# Edit /etc/default/grub:
sudo tee /etc/default/grub.d/99-strixhalo-llm.cfg <<'EOF'
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=off ttm.pages_limit=30788044 ttm.page_pool_size=30788044 zswap.enabled=0"
EOF

# 30788044 = ~118 GiB GTT pool on 128 GB system.
# Scale: для 64 GB system → 15394022, для 32 GB → 7697011.

sudo update-grub
sudo reboot
```

После reboot — verify:
```bash
cat /sys/class/drm/card*/device/mem_info_gtt_total
# Should be ~118 GiB (or matching cmdline)
```

### 1.4 sysctl

```bash
sudo tee /etc/sysctl.d/99-agmind.conf <<'EOF'
vm.swappiness=10
vm.overcommit_memory=1
vm.max_map_count=1048576
EOF
sudo sysctl --system
```

### 1.5 User groups

```bash
sudo usermod -aG video,render,docker $USER
newgrp render  # или re-login
```

### 1.6 AMDVLK cleanup

AMDVLK officially discontinued AMD 2025-09-15. У него hard 2 GiB cap на
VkDeviceMemory, ломает LLM ≥30B. **Use RADV only**.

```bash
sudo apt remove --purge amdvlk 2>/dev/null
sudo rm -f /etc/vulkan/icd.d/amd_icd64.json \
           /etc/vulkan/icd.d/amd_icd32.json \
           /etc/vulkan/implicit_layer.d/amd_icd64.json \
           /etc/vulkan/implicit_layer.d/amd_icd32.json

# Verify
vulkaninfo --summary | grep -i driverName
# Должно быть только: driverName = radv
```

## Phase 2: Ansible install

### 2.1 Install Ansible

```bash
sudo apt install -y ansible python3-pip git
ansible --version  # ≥ 2.16
```

### 2.2 Clone repo

```bash
git clone https://github.com/botAGI/AGmind64 /opt/agmind-repo
cd /opt/agmind-repo
```

### 2.3 Ansible collections

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

### 2.4 Bootstrap (один шаг)

```bash
# Полный install: bootstrap + docker + agmind venv + services
sudo ansible-playbook ansible/install.yml

# Или поэтапно:
sudo ansible-playbook ansible/install.yml -t bootstrap  # apt + groups + sysctl
sudo ansible-playbook ansible/install.yml -t strix-halo # GRUB + AMDVLK purge
sudo ansible-playbook ansible/install.yml -t agmind-core # docker + python
sudo ansible-playbook ansible/install.yml -t services    # render compose + up
```

### 2.5 Profile selection

Override через `-e agmind_profiles='["core","rag","observability"]'`:

```bash
sudo ansible-playbook ansible/install.yml \
  -e 'agmind_profiles=["core","rag","observability"]'
```

## Phase 3: Models

### 3.1 Verify tier

```bash
agmind models list
# Должна быть автодетекция:
#   [·] XL  gpt-oss-120b  62.8 GB
```

### 3.2 Download

```bash
# Primary LLM для auto-detected tier:
agmind models download

# Embed + rerank (всегда нужны):
agmind models download --embed
agmind models download --rerank

# VLM (для Docling picture description):
agmind models download --vlm
```

### 3.3 Restart inference services

```bash
agmind deploy restart llama-llm
agmind deploy restart llama-embed
agmind deploy restart llama-rerank
```

## Phase 4: Verify

### 4.1 Doctor

```bash
agmind doctor
# Все checks должны быть ✓ (4 ok / 0 warn / 0 fail)
```

### 4.2 Stack status

```bash
agmind deploy status
# Все services Up (health check passing)
```

### 4.3 Inference smoke test

```bash
# Chat
agmind chat
# Try: "hello, what's your name?"

# Embed
echo "test text" | agmind embed
# Should return JSON с embedding vector

# Rerank
agmind rerank "пример запроса" "первый документ" "второй документ"
```

## Phase 5: Access

- Dify console: http://localhost:3000 или http://`agmind-dify.local`
- LLM API (OpenAI compat): http://localhost:8080/v1
- Embed: http://localhost:8081/v1/embeddings
- Rerank: http://localhost:8082/v1/rerank
- Open WebUI (если `ui` profile): http://`agmind-chat.local`
- Grafana (если `observability`): http://localhost:3002

Credentials в `/opt/agmind/.env` (chmod 600).

## Profile-specific notes

### `rag` profile

После первого старта зайди в Dify console (http://localhost:3000):
1. Создай admin user
2. Settings → Model Providers → add OpenAI-compatible (URL: `http://llama-llm:8080/v1`)
3. Knowledge → New → settings: embed = `bge-m3` через `http://llama-embed:8081/v1`

### `ragflow` profile

RAGFlow на CPU 10-50× медленнее CUDA. Use для cyrillic-heavy scans если
docling недостаточен.

### `observability` profile

Grafana login: admin / password из `/opt/agmind/.env::GRAFANA_PASSWORD`.
Дашборды auto-provisioned (Prometheus, Loki datasources).

### `security` profile

Authelia config — отредактировать `/etc/agmind/authelia/configuration.yml`
после первого старта. Default — disabled (не блокирует доступ).

## Upgrade

```bash
cd /opt/agmind-repo
git pull
sudo ansible-playbook ansible/install.yml -t services
```

Idempotent — Ansible применит только diff. State preserved в
`/var/lib/agmind/`.

## Backup

```bash
# Docker volumes — bind-mounted, рейзу tar:
sudo tar -czf agmind-backup-$(date +%F).tar.gz \
    /var/lib/agmind/{postgres,qdrant,redis} \
    /opt/agmind/.env
```

## Uninstall

```bash
agmind deploy down --volumes   # DESTRUCTIVE
sudo rm -rf /var/lib/agmind /etc/agmind /opt/agmind
```
