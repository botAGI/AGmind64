# AGmind

English | [Русская версия](README.ru.md)

Private LLM/RAG platform for AMD Strix Halo and generic x86_64 hosts. The
primary lane is Docker Compose on Ubuntu, with optional Proxmox VM provisioning
and Kubernetes targets kept behind explicit deploy-target contracts.

## What AGmind Is

AGmind installs and operates a local AI stack: llama.cpp services for LLM,
embedding, and reranking; RAG/storage services; observability; and deployment
governance checks. It is tuned for AMD Ryzen AI Max+ "Strix Halo" systems with
Radeon 8060S/gfx1151, while keeping a CPU fallback for ordinary x86_64 Linux.

Core commands live behind the `agmind` CLI:

```bash
agmind install
agmind doctor
agmind status
agmind render topology --profile core,rag,observability --json
agmind deploy --profile core,rag,observability --domain lab.example.com --no-prompt
agmind cluster inspect --timeout 10
```

## Current Readiness Snapshot

Last local readiness pass: 2026-05-25.

- Default fresh deploy target: `ubuntu-compose`.
- Deployment target contracts: `ubuntu-compose`, `proxmox-vm-compose`, and
  `k3s` are registered and validated.
- Version governance checks pass for constraints, components, deploy targets,
  and tool candidates.
- `scripts/version_check.py` writes 31 component entries. Current manual-review
  items are major candidates for RagFlow and MySQL, expected holds for selected
  pinned services, and several registry probes that returned no remote version.
- Deploy-facing mutable image tags and unbounded Ansible package upgrade state
  have been removed from the current deploy/docs surfaces.
- Compose runtime secrets are required at config time; `agmind install` writes
  `/opt/agmind/.env` with mode `0600` and preserves generated values on rerun.
- The `full` profile is intentionally blocked by deploy conflict checks until
  the alternative edge proxies are split; use `core,observability` first, then
  add `rag` after models and secrets are ready.
- Local cluster status sees this node as `beelinknode-GTR-Pro` on
  `192.168.1.151`; mDNS peer discovery currently returns no AGmind peers.
- A LAN neighbor was visible at `192.168.1.58`, but ping and TCP probes on
  `22`, `41423`, `8080`, `8081`, `8082`, and `8006` did not respond. Treat the
  second node as connected to the LAN but not yet advertising AGmind.

## Quick Start

```bash
git clone https://github.com/botAGI/AGmind64 agmind
cd agmind
uv venv
uv pip install -e ".[dev]"
agmind install
```

Non-interactive Strix Halo install:

```bash
agmind install --no-tui \
  --domain lab.example.com \
  --cf-token-file token.txt \
  --model-id qwen36-a3b-q4km \
  --ctx-size 16384 \
  --kv-cache q8_0
```

Model catalog:

```bash
agmind install --list-models
```

## Fresh Deploy Test Plan

Run these before touching `/opt/agmind` on a clean host:

Start with `core,observability` or `core,rag,observability`. Do not use
`--profile full` for the first host test: it selects Caddy, Nginx, and Traefik
together, and deploy now stops that 80/443 host-port collision before Docker.

```bash
agmind doctor --json
agmind cluster inspect --timeout 10
agmind render topology --profile core,rag,observability --json
cat > /tmp/agmind-compose-check.env <<'EOF'
POSTGRES_PASSWORD=check-postgres-password
GRAFANA_PASSWORD=check-grafana-password
MYSQL_ROOT_PASSWORD=check-mysql-root-password
MINIO_ROOT_USER=check-minio
MINIO_ROOT_PASSWORD=check-minio-password
REDIS_PASSWORD=check-redis-password
EOF
agmind render compose \
  --profile core,rag,observability \
  --domain lab.example.com \
  --output /tmp/agmind-fresh-deploy-check.yml
docker compose \
  --env-file /tmp/agmind-compose-check.env \
  -f /tmp/agmind-fresh-deploy-check.yml \
  config --quiet
agmind deploy --profile core,rag,observability \
  --install-dir /tmp/agmind-fresh-deploy-check \
  --domain lab.example.com \
  --no-prompt
```

Repository checks for a fresh deploy branch:

```bash
python scripts/constraints_check.py
python scripts/component_check.py
python scripts/deploy_target_check.py
python scripts/tool_candidate_check.py
python scripts/version_check.py \
  --json /tmp/agmind-version-report.json \
  --output /tmp/agmind-version-report.md
python scripts/audit_forbidden.py --fail
python scripts/governance_check.py
```

Focused test modules:

```bash
pytest -q tests/test_cluster_detect.py tests/test_cluster_inspect.py tests/test_cluster_inventory.py
pytest -q tests/test_deploy_targets.py tests/test_deploy_conflicts.py tests/test_service_selection.py tests/test_deployment_topology.py
```

`agmind doctor` can exit with code `1` when it finds warnings. Code `2` means a
blocking failure.

## Two-Node Cluster Detection

AGmind discovers peers with the mDNS service `_agmind._tcp.local.`. A device on
the same LAN will not appear until it advertises AGmind or runs a compatible
service record.

On the second node:

```bash
cd ~/agmind
uv venv
uv pip install -e ".[dev]"
agmind cluster advertise --duration 600
```

On the first node:

```bash
agmind cluster detect --timeout 10
agmind cluster status --timeout 10
agmind cluster inspect --timeout 10
```

If discovery is empty:

- Confirm both nodes are on the same subnet/VLAN.
- Start or install `avahi-daemon` on Linux hosts.
- Allow UDP 5353/mDNS through the firewall.
- Confirm the Python environment includes `zeroconf`.
- Check reachability with `ip neigh show`, `ping <node-ip>`, and targeted TCP
  probes for SSH or the service port you expect.

## Enable Proxmox

AGmind has two Proxmox paths: the Compose runtime can scrape an existing
Proxmox VE cluster through `proxmox-exporter`, and the experimental
`proxmox-vm-compose` target can provision Ubuntu VM shells before Ansible and
Compose take over.

Enable the Proxmox exporter for an existing Compose host:

```bash
sudo install -d -m 0750 /etc/agmind/proxmox-exporter
sudo cp templates/observability/proxmox-exporter/pve.yml.example \
  /etc/agmind/proxmox-exporter/pve.yml
sudoedit /etc/agmind/proxmox-exporter/pve.yml
python -m agmind.deploy.proxmox_exporter \
  --config /etc/agmind/proxmox-exporter/pve.yml
agmind render compose \
  --profile core,observability,proxmox \
  --domain lab.example.com \
  --output /tmp/agmind-proxmox.yml
docker compose \
  --env-file /opt/agmind/.env \
  -f /tmp/agmind-proxmox.yml \
  config --quiet
```

Equivalent Ansible variables:

```yaml
agmind_proxmox_exporter_existing_config: false
agmind_proxmox_exporter_verify_ssl: true
agmind_proxmox_exporter_user: "prometheus@pve"
agmind_proxmox_exporter_token_name: "agmind"
agmind_proxmox_exporter_token_value: "REDACTED"
```

Provision Proxmox VM shells with OpenTofu:

```bash
cd infra/proxmox/vm-compose
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
tofu init
tofu plan
tofu apply
tofu output -json > /tmp/agmind-proxmox-output.json
python ../../../scripts/proxmox_inventory.py \
  --input /tmp/agmind-proxmox-output.json \
  --output ../../../ansible/inventory/proxmox.generated.yml
```

Keep `terraform.tfvars`, state files, and plan files local; this module ignores
them by design.

## Version And Pinning Policy

- Do not use mutable floating image tags for runtime or deploy examples.
- Service image pins live in `templates/services/*.yaml`.
- Python dependency planes live in `constraints/*.txt`.
- Intentional version holds live in `templates/version_holds.yaml`.
- Use `scripts/version_check.py` to review patch, minor, and major candidates.
- Major candidates require manual review before pin changes.

Current manual-review items from the local report:

- RagFlow has a major candidate from `v0.25.5` to `v1.0`.
- MySQL has a major candidate from `8.0.46-oraclelinux9` to `9.7.0`.
- Selected services are intentionally held, including Elasticsearch, llama.cpp,
  Dify API, Dify plugin daemon, PostgreSQL, and Redis.

## Day-2 Operations

```bash
agmind doctor
agmind status
agmind cluster inspect --timeout 10
agmind status --tui
agmind logs llama-llm -f
agmind shell traefik --cmd "/bin/sh"
agmind backup --output ~/agmind-backup.tar.gz
agmind restore ~/agmind-backup.tar.gz
agmind migrate status
agmind migrate up
make audit
```

## Architecture Map

```text
agmind/                Python package and CLI
agmind/compute/        Runtime backend detection and selection
agmind/cluster/        mDNS discovery, inventory, and target inspection
agmind/deploy/         Dry-run, apply, rollback, targets, Proxmox helpers
agmind/services/       Service descriptors, topology, Compose/Kubernetes render
templates/services/    Pinned service descriptors
templates/deploy-targets/  ubuntu-compose, proxmox-vm-compose, k3s
constraints/           Python dependency planes
ansible/               Host bootstrap and service configuration
infra/proxmox/         OpenTofu Proxmox VM skeleton
docker/                Backend Dockerfiles
docs/                  Operations notes, benchmarks, plans, ADRs
```

## Documentation

- [`docs/HARDWARE.md`](docs/HARDWARE.md) - Strix Halo host setup.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) - benchmark methodology and results.
- [`docs/CLUSTER.md`](docs/CLUSTER.md) - cluster and inventory notes.
- [`infra/proxmox/vm-compose/README.md`](infra/proxmox/vm-compose/README.md) -
  Proxmox VM provisioning target.
- [`docs/adr/`](docs/adr/) - architecture decision records.

## License

Apache-2.0. See [LICENSE](LICENSE).
