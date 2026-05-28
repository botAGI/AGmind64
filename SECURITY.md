# Security Policy

AGmind installs and operates a self-hosted LLM/RAG stack (llama.cpp services,
Dify/RAGFlow, vector stores, reverse proxy, monitoring) on AMD Strix Halo and
generic x86_64 hosts with a single command. The installer performs privileged
host bootstrap via Ansible (apt, groups, sysctl, Docker, firewall), generates
runtime secrets, and brings up many containers — a bug here has a large blast
radius. Reports are taken seriously.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security problems.**

Use a private channel:

1. **GitHub Security Advisories** (preferred) —
   [Report a vulnerability](https://github.com/botAGI/AGmind64/security/advisories/new)
   from the repo's Security tab. This opens a private, coordinated-disclosure
   thread.
2. **Email** — if GHSA is unavailable, email the maintainers (address in the
   org profile / README). Include:
   - affected version / commit SHA
   - reproduction steps
   - assessed impact (RCE / privilege escalation / secret exposure / DoS)
   - a suggested fix, if you have one

We aim to acknowledge within **72 hours** and ship a fix within **14 days** for
critical / high severity. If those windows slip, please ping again.

## Scope

In scope (please report):
- `agmind/` — the CLI, install orchestrator/steps, ops (backup/restore/exec),
  deploy (render/apply/rollback/snapshot/gc), service rendering, and cluster
  inspection.
- `templates/services/*.yaml` and the rendered Docker Compose / Kubernetes
  output.
- `ansible/` — privileged host bootstrap roles.
- `.github/workflows/*.yml` — the CI/CD pipeline.
- Credentials handling (secret generation, `0600` file modes, sudo password
  passing), firewall / TLS / reverse-proxy setup.
- Supply chain — pinned image tags, pinned GitHub Actions, downloaded model
  artifacts and their integrity checks.

Out of scope:
- Vulnerabilities in upstream images (Dify, RAGFlow, llama.cpp, Postgres,
  Redis, …) — report those upstream. We will bump pins once a fixed release
  exists, subject to `templates/version_holds.yaml`.
- Issues that require an already-compromised host or physical access.
- Missing hardening that is the operator's documented responsibility (e.g.
  exposing services to the public internet without the documented reverse
  proxy / auth).

## Supported Versions

AGmind is pre-1.0 and ships from `main`. Only the latest `main` is supported;
fixes land there and are not back-ported.
