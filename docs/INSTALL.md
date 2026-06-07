# AGmind Install Guide

Детальный setup AGmind на AMD Strix Halo (gfx1151).

> No internet on the target host? See
> [`docs/installation/offline-install.md`](installation/offline-install.md) for
> the air-gap path (`docker save`/`load` + `AGMIND_OFFLINE=1`).

## Recommended clean TUI install

Для новой ручной установки сначала докажи рендер и зависимости без мутаций,
потом запускай TUI:

```bash
# Main one-command path — make creates the .venv, installs the CLI into it, runs the wizard:
make setup

# Optional non-mutating proofs first (after `make bootstrap` creates the .venv):
.venv/bin/agmind verify install --domain lab.example.com

# Focused descriptor proof for one or more explicit services:
.venv/bin/agmind render compose --service n8n --service dozzle --domain lab.example.com \
  --output /tmp/agmind-focused.yml
```

`agmind setup` ведет оператора через wizard, bootstrap, запись runtime `.env`
и `version.env`, точную проверку `docker compose config --quiet` для выбранного
стека, pull образов, загрузку моделей, deploy, health checks и rollback-aware
failure handling. Значения credentials не печатаются; путь к `/opt/agmind/.env`
показывается в финальном summary.

Если Docker ставится вручную до TUI, используй официальные пакеты Docker Engine
`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` и
`docker-compose-plugin`. `docker.io` из distro repo считается конфликтующим
пакетом для production bootstrap; роль Docker удаляет его перед установкой
официального Engine, если Compose plugin отсутствует или старее `2.24.0`.

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
│    │ Traefik (reverse proxy/TLS)  │   │
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

### 3.1 List the curated catalog

```bash
agmind models list              # local *.gguf files + legacy registry tiers
agmind install --list-models    # the ids the install wizard actually offers
```

`agmind install --list-models` is the source of truth for what `--model-id` accepts. As of this
build: `qwen36-a3b-q4km` (default, ★Strix-verified), `qwen36-a3b-q4_0`, `qwen36-a3b-dyn`,
`llama2-7b-q4km`/`llama2-7b-q4_0` (CI/smoke baselines), `bge-m3-q8`, `bge-reranker-v2-m3-q8`.

### 3.2 Pull

```bash
# Primary LLM (curated id from `agmind install --list-models`):
agmind models pull qwen36-a3b-q4km

# Embed + rerank (всегда нужны):
agmind models pull bge-m3-q8
agmind models pull bge-reranker-v2-m3-q8

# Custom model from a Hugging Face repo (outside the curated catalog):
agmind models pull --repo <hf-org/repo> --file <model.gguf>
```

There is no curated VLM id in the install catalog yet; pull one with `--repo/--file` if needed.

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

Inference is HTTP-only (OpenAI-compatible); there is no `agmind chat`/`embed`/`rerank` CLI.
Probe the llama-server ports directly with `curl` (with the LLM skipped at install time, only
embed/rerank answer):

```bash
# Chat → llama-llm (:8080)
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"hello, what is your name?"}]}'

# Embed → llama-embed (:8081)
curl http://localhost:8081/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":["test text"]}'

# Rerank → llama-rerank (:8082)
curl http://localhost:8082/v1/rerank \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-reranker","query":"пример запроса","documents":["первый документ","второй документ"]}'
```

## Phase 5: Access

- Dify console: http://localhost:3000 или http://`agmind-dify.local`
- LLM API (OpenAI compat): http://localhost:8080/v1
- Embed: http://localhost:8081/v1/embeddings
- Rerank: http://localhost:8082/v1/rerank
- Open WebUI (если `ui` profile): http://`agmind-chat.local`
- Grafana (если `observability`): http://localhost:3002

Credentials в `/opt/agmind/.env` (chmod 600). Версии выбранных
runtime-сервисов и digest записываются в `/opt/agmind/version.env` (chmod 644).

## Profile-specific notes

### `rag` profile

После первого старта зайди в Dify console (http://localhost:3000):
1. Создай admin user
2. Settings → Model Providers → add OpenAI-compatible (URL: `http://llama-llm:8080/v1`)
3. Knowledge → New → settings: embed = `bge-m3` через `http://llama-embed:8081/v1`

### `ragflow` profile

RAGFlow на CPU в 10-50× медленнее, чем на дискретном GPU-ускорителе. Use для
cyrillic-heavy scans если docling недостаточен.

### `observability` profile

Grafana login: admin / password из `/opt/agmind/.env::GRAFANA_PASSWORD`.
Дашборды auto-provisioned (Prometheus, Loki datasources).

### `security` profile

Authelia runs as an edge forward-auth portal in front of the infra consoles (portainer /
grafana / n8n). The default policy is **`one_factor`** (username + password SSO) — the 2FA
step-up was removed 2026-06-07 because the file-notifier OTC enrollment was too clunky without
SMTP. To restore 2FA, set `policy: two_factor` in
`templates/authelia/configuration.yml` (and configure an SMTP notifier) before deploy.

Host-level UFW (LAN-only firewall) + fail2ban (sshd jail) ship in the Ansible `security` role
and are applied only on the full Ansible path
(`sudo ansible-playbook ansible/install.yml -t security` with `security` in `agmind_profiles`).
The default `make setup` docker deploy brings up the Authelia container but does **not** yet run
the host-firewall role — wiring UFW/fail2ban into the installer is roadmap.

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
# Config/state backup. Use the sudo prompt for root-owned /opt and /var/lib paths.
agmind backup --ask-sudo-password --output ~/agmind-backup.tar.gz
agmind restore --ask-sudo-password ~/agmind-backup.tar.gz
```

The CLI backup includes rendered Compose, runtime `.env`, runtime
`version.env`, setup/schema state, service descriptor snapshots, and deploy
snapshots. It intentionally excludes GGUF models and Docker volume data;
protect `/var/lib/agmind` service data with host/storage snapshots before
destructive maintenance.

Optional root-owned smoke without touching `/opt/agmind`:

```bash
agmind ops smoke backup-root-owned --dry-run
agmind ops smoke backup-root-owned
```

The smoke creates a temporary root-owned tree under
`/tmp/agmind-root-owned-smoke`, runs `create_backup(..., sudo_password=...)`,
restores into a second `/tmp` target, and cleans up by default. The checkout
script `python3 scripts/proof/root_owned_backup_smoke.py` is equivalent.

## Uninstall

```bash
agmind deploy down --volumes   # DESTRUCTIVE
sudo rm -rf /var/lib/agmind /etc/agmind /opt/agmind
```
