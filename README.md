# AGmind

English | [Русская версия](README.ru.md)

[![CI](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml/badge.svg)](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml)

Private LLM/RAG platform for AMD Strix Halo and generic x86_64 hosts. The
primary lane is Docker Compose on Ubuntu, with optional Proxmox VM provisioning
and Kubernetes targets kept behind explicit deploy-target contracts.

## What AGmind Is

AGmind installs and operates a local AI stack: llama.cpp services for LLM,
embedding, and reranking; RAG/storage services; optional n8n workflow
automation; observability; and deployment governance checks. It is tuned for AMD
Ryzen AI Max+ "Strix Halo" systems with Radeon 8060S/gfx1151, while keeping a
CPU fallback for ordinary x86_64 Linux.

Core commands live behind the `agmind` CLI:

```bash
agmind setup
agmind verify install --domain lab.example.com
agmind doctor
agmind status
agmind render topology --profile core,rag,observability --json
agmind cluster inspect --timeout 10
```

## Current Readiness Snapshot

Last local readiness pass: 2026-05-26.

- Default fresh deploy target: `ubuntu-compose`.
- Deployment target contracts: `ubuntu-compose`, `proxmox-vm-compose`, and
  `k3s` are registered and validated.
- The setup wizard walks service selection by installer-facing departments:
  foundation, RAG/agents/automation, data, model runtime, monitoring, and
  security.
- `agmind setup` is the primary one-command TUI flow: config wizard, bootstrap,
  runtime `.env`, exact Docker Compose config validation before real image
  pulls, model pulls, Compose deploy, health checks, rollback-aware failure
  handling, and final credential path hint.
- `agmind verify install` is the non-destructive fresh-install gate: it expands
  setup service choices, writes temporary runtime env files, runs deploy
  dry-runs, validates Docker Compose config and image pull dry-runs for key
  stacks, installs required Ansible collections into the ignored local cache,
  and syntax-checks the bootstrap playbook.
- Out-of-box monitoring is the `observability` profile: Prometheus, Grafana,
  Loki, Alloy, Alertmanager, and node exporter are part of the default service
  selection.
- n8n is an accepted opt-in `automation` profile with a pinned image, persistent
  `/var/lib/agmind/n8n`, disabled diagnostics, and Prometheus metrics enabled.
- Version governance checks pass for constraints, components, deploy targets,
  and tool candidates.
- `scripts/checks/version_check.py` writes 32 component entries. Current manual-review
  items are major candidates for RagFlow and MySQL, expected holds for selected
  pinned services, and several registry probes that returned no remote version.
- Deploy-facing mutable image tags and unbounded Ansible package upgrade state
  have been removed from the current deploy/docs surfaces.
- Compose runtime secrets are required at config time; `agmind install` writes
  `/opt/agmind/.env` with mode `0600` and preserves generated values on rerun.
  Final setup/install summaries point operators to this file without printing
  credential values.
- `agmind install` also writes `/opt/agmind/version.env` with mode `0644`.
  It records AGmind plus selected runtime service image tags and digests for
  operator drift review, backups, and rollback notes.
- A repository-visible example lives at `templates/runtime/version.env.example`
  and tracks the pinned Uptime Kuma, Homarr, Watchtower, Dozzle, and Netdata
  descriptors.
- Compose and Kubernetes rendering support repeated `--service/-s` flags for
  focused runtime proofs, for example `agmind render compose --service n8n
  --service dozzle`. Explicit service renders fail fast if hard `depends_on`
  services are missing.
- Compose rendering uses health-aware dependency gates: services wait for
  healthy Postgres/Redis/MySQL/MinIO/etc. when the dependency descriptor has a
  healthcheck, reducing fresh-deploy startup races.
- The `full` profile renders again after the alternative edge proxy cleanup, but
  fresh installs should still prefer a staged rollout: start with
  `core,observability`, verify models and secrets, then add `rag`/other profiles.
- Local cluster status sees this node as `beelinknode-GTR-Pro` on
  `192.168.1.151`; `agmind cluster inspect` now reports both AGmind mDNS peers
  and raw LAN neighbor candidates from the local neighbor table.
- Current LAN probes show no AGmind mDNS peers. Neighbor candidates include
  `192.168.1.58` and `192.168.1.78`; neither has TCP `41423` open. Treat the
  second node as physically visible on the LAN but not yet advertising AGmind.

## Quick Start

```bash
git clone https://github.com/botAGI/AGmind64 agmind
cd agmind
uv venv
uv pip install -e ".[dev]"

# Optional proof when Docker Compose 2.24+ is already available.
agmind verify install --domain lab.example.com

# One-command TUI install; bootstrap installs/repairs Docker Engine if needed.
agmind setup
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

Start with `core,observability` or `core,rag,observability`.

```bash
agmind doctor --json
agmind cluster inspect --timeout 10
agmind verify install --domain lab.example.com
agmind setup
```

Useful focused variants:

```bash
agmind verify install --domain lab.example.com --scenario explicit-dify-ragflow-milvus
agmind verify install --domain lab.example.com --skip-ansible
agmind verify install --domain lab.example.com --json
```

Repository checks for a fresh deploy branch:

```bash
python scripts/checks/constraints_check.py
python scripts/checks/component_check.py
python scripts/checks/deploy_target_check.py
python scripts/checks/tool_candidate_check.py
python scripts/checks/version_check.py \
  --json /tmp/agmind-version-report.json \
  --output /tmp/agmind-version-report.md
python scripts/checks/audit_forbidden.py --fail
python scripts/checks/governance_check.py
```

Focused test modules:

```bash
pytest -q tests/cluster/test_cluster_detect.py tests/cluster/test_cluster_inspect.py tests/cluster/test_cluster_inventory.py
pytest -q tests/deploy/test_deploy_targets.py tests/components/test_deploy_conflicts.py tests/services/test_service_selection.py tests/services/test_deployment_topology.py
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
- Check `agmind cluster inspect --timeout 10`: `LAN neighbors` means the other
  device is visible at L2/ARP even if AGmind mDNS is not advertising yet.
- On the other node, run `agmind cluster advertise --duration 600` and make sure
  TCP `41423` is reachable from this node.
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
python ../../../scripts/ops/proxmox_inventory.py \
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
- Use `scripts/checks/version_check.py` to review patch, minor, and major candidates.
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
agmind backup --ask-sudo-password --output ~/agmind-backup.tar.gz
agmind restore --ask-sudo-password ~/agmind-backup.tar.gz
agmind ops smoke backup-root-owned --dry-run
agmind migrate status
agmind migrate up
make audit
```

`agmind backup` covers rendered Compose, runtime `.env`, runtime `version.env`,
setup state, descriptor snapshots, and deploy snapshots. It does not archive
model files or Docker volume data; keep those on separate storage snapshots.

## Architecture Map

```text
agmind/                Python package and CLI
agmind/core/           Shared logging, env, and secret helpers
agmind/compute/        Runtime backend detection and selection
agmind/cluster/        mDNS discovery, inventory, and target inspection
agmind/deploy/         Dry-run, apply, rollback, targets, Proxmox helpers
agmind/install/        Fresh install planning, steps, and verification
agmind/ops/            Backup, restore, logs, shell, and smoke helpers
agmind/services/       Service descriptors, topology, Compose/Kubernetes render
scripts/checks/        CI, pre-commit, and governance checks
templates/services/    Pinned service descriptors
templates/deploy-targets/  ubuntu-compose, proxmox-vm-compose, k3s
constraints/           Python dependency planes
ansible/               Host bootstrap and service configuration
infra/proxmox/         OpenTofu Proxmox VM skeleton
docker/                Backend Dockerfiles
tests/                 Domain-mirrored test layout
docs/                  Operations notes, benchmarks, codebase map, ADRs
```

## Documentation

- [`docs/HARDWARE.md`](docs/HARDWARE.md) - Strix Halo host setup.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) - benchmark methodology and results.
- [`docs/CLUSTER.md`](docs/CLUSTER.md) - cluster and inventory notes.
- [`docs/CODEBASE.md`](docs/CODEBASE.md) - codebase responsibility map.
- [`infra/proxmox/vm-compose/README.md`](infra/proxmox/vm-compose/README.md) -
  Proxmox VM provisioning target.
- [`docs/adr/`](docs/adr/) - architecture decision records.

## License

Apache-2.0. See [LICENSE](LICENSE).
