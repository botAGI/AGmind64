# AGmind

**Your own private AI stack — LLM, RAG, observability and SSO — on one box, one command.**

English | [Русская версия](README.ru.md)

[![CI](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)

AGmind turns an AMD Ryzen AI Max+ "Strix Halo" host (Radeon 8060S / gfx1151) — or
any x86_64 Linux box — into a fully self-hosted AI platform. No cloud, no API
keys leaving your network: a local OpenAI-compatible LLM, embeddings and
reranking, a RAG app, a vector store, edge auth, and a monitoring suite, all
wired together and deployed by a single `make setup`.

## What you get

- **Local inference** — llama.cpp serving LLM, embeddings and reranking on
  Vulkan/ROCm (with a CPU fallback), behind OpenAI-compatible HTTP endpoints.
- **RAG out of the box** — Dify app, Qdrant vector store, document parsing
  (Docling); optional RAGFlow lane.
- **Observability** — Prometheus, Grafana, Loki, Alloy, cAdvisor and exporters
  on an opt-in profile.
- **Secure edge** — Traefik reverse proxy + Authelia forward-auth SSO; secrets
  written to a `0600` `.env` and never printed.
- **One operator CLI** — `agmind` for install, day-2 ops, backup/restore,
  diagnostics, and a built-in TUI.
- **Reproducible** — every service image is pinned by digest; governance checks
  block mutable tags and a hardware-mismatch audit forbids NVIDIA/CUDA paths.

> **Status:** pre-1.0. Single-node Docker Compose on Ubuntu is the supported
> lane; multi-node cluster discovery is experimental.

## Quick start

```bash
git clone https://github.com/botAGI/AGmind64.git agmind
cd agmind
make setup
```

`make setup` creates a local `.venv`, installs the `agmind` CLI into it, repairs
or installs Docker if needed, then launches the TUI install wizard. **This
checkout is the bootstrap entry point** — there is no global `agmind` binary
until the install writes one, so before the first install always go through
`make setup` (or `.venv/bin/agmind …`).

Non-interactive install (Strix Halo defaults):

```bash
make install ARGS="--no-tui --domain lab.example.com \
  --model-id qwen36-a3b-q4km --ctx-size 16384 --kv-cache q8_0"
```

List the model catalog the wizard offers: `agmind install --list-models`.

## Accessing the stack

| What | Where |
|------|-------|
| LLM (OpenAI-compatible) | `http://<host>:8080/v1` |
| Embeddings | `http://<host>:8081/v1` |
| Reranking | `http://<host>:8082/v1` |
| Dify, Grafana, … | `agmind endpoints` (URL + state) |
| Credentials | `sudo agmind creds show` (root-only; stored in `/opt/agmind/.env`, `0600`, never printed) |

There is no `agmind chat` CLI — inference is HTTP-only; point any
OpenAI-compatible client at the ports above.

## Profiles

Select components at install time (default is `core,rag`):

| Profile | Includes |
|---------|----------|
| `core` | Traefik, llama LLM/embed/rerank, Qdrant (minimum for inference) |
| `rag` | + Dify (api/worker/web/plugin-daemon/sandbox), Postgres, Redis, Docling |
| `ragflow` | RAGFlow + MySQL + Elasticsearch + MinIO (opt-in fallback) |
| `ui` | Open WebUI chat frontend |
| `observability` | Prometheus, Grafana, Loki, Alloy, cAdvisor, Portainer, exporters |
| `security` | Authelia SSO (one-factor forward-auth) + Redis session store |
| `automation` | n8n workflow automation |
| `tracing` | Arize Phoenix LLM tracing for Dify |

Fresh installs should stage the rollout: start with `core,observability`, verify
models and secrets, then add `rag` and the rest.

## Day-2 cheatsheet

```bash
agmind doctor              # preflight + live diagnostics
agmind status              # backend + device info ( --tui for live dashboard )
agmind endpoints           # published services: URL + state
agmind open grafana        # print a service URL (SSH-pipeable)
agmind creds show          # logins + passwords (root-only)
agmind config validate     # check the live deployment config
agmind logs llama-llm -f   # stream service logs
agmind backup  --output ~/agmind-backup.tar.gz
agmind restore ~/agmind-backup.tar.gz
agmind uninstall           # tear the stack down
```

`agmind backup` archives rendered Compose, the runtime `.env`/`version.env`,
setup state and snapshots — not model files or volume data; snapshot those
separately. See [`docs/DR.md`](docs/DR.md).

## Architecture

The `agmind` Python package owns the CLI, backend detection, mDNS cluster
discovery, install/deploy planning, and the rendering of pinned service
descriptors (`templates/services/*.yaml`) into Docker Compose / Kubernetes.
Ansible handles host bootstrap; OpenTofu provides an optional Proxmox VM target.
Full responsibility map: [`docs/CODEBASE.md`](docs/CODEBASE.md).

## Documentation

**Getting started**
- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — fastest path to a running stack.
- [`docs/INSTALL.md`](docs/INSTALL.md) — detailed install reference.
- [`docs/HARDWARE.md`](docs/HARDWARE.md) — Strix Halo host setup.
- [`docs/SETUP_ROCM_STRIX_HALO.md`](docs/SETUP_ROCM_STRIX_HALO.md) — ROCm/Vulkan drivers.
- [`docs/SETUP_CLOUDFLARE_DOMAIN.md`](docs/SETUP_CLOUDFLARE_DOMAIN.md) — public domain + TLS.
- [`docs/installation/offline-install.md`](docs/installation/offline-install.md) — air-gapped install.

**Operations**
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — fixes (also `agmind troubleshoot`).
- [`docs/DR.md`](docs/DR.md) — disaster recovery (RPO/RTO + drills).
- [`docs/operations/incident-response.md`](docs/operations/incident-response.md) — incident runbook.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — benchmark methodology and results.

**Reference**
- [`docs/CODEBASE.md`](docs/CODEBASE.md) — codebase responsibility map.
- [`docs/CLUSTER.md`](docs/CLUSTER.md) — multi-node discovery and inventory.
- [`docs/docling-presets.md`](docs/docling-presets.md) — document-parsing presets.
- [`docs/adr/`](docs/adr/) — architecture decision records.
- [`infra/proxmox/vm-compose/README.md`](infra/proxmox/vm-compose/README.md) — Proxmox VM target.

## Contributing & security

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup,
test/lint commands and the branch workflow. Report vulnerabilities via
[SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
