# Agent Tooling And Plugins

Last updated: 2026-05-25.

This is the current answer to: "what plugins/tools does Codex need to run this
project well?"

## Required For Day-To-Day Project Stewardship

### 1. GitHub connector

Status: available in this environment for `botAGI/AGmind64` (`develop` is the
default branch). The old `botAGI/AGmindx86` locator is stale and should not be
used for connector calls.

Needed for:

- read PR/run metadata
- inspect Actions logs
- cancel stale self-hosted runs
- rerun failed jobs
- manage issues/labels when planning moves to GitHub issues

Why it matters: AGmind's truth is not only local tests; the self-hosted runner
is part of the product evidence.

### 2. Local shell with Docker, Git, gh, uv

Status: available locally.

Needed for:

- local parity checks
- Docker backend image builds
- Strix runtime smoke
- repo cleanup and patching
- compose rendering validation

### 3. Web/doc lookup

Status: available through the browsing tool.

Use only when facts are current/unstable:

- upstream image tags
- GitHub Actions behavior
- OpenAI/OpenAI API docs
- ROCm/Vulkan/llama.cpp/Dify/RAGFlow current compatibility

For technical changes, prefer primary sources: official docs, release notes,
source repositories, issues, or specs.

## Useful Next Plugins / Connectors

These are not mandatory today, but would make project stewardship cleaner.

| Tool | Why |
|------|-----|
| GitHub plugin/connector | Already effectively required: PRs, issues, Actions runs, reruns, comments, labels. Keep it enabled. |
| Superpowers plugin | Already enabled: use for planning, TDD, verification, worktree discipline, and long-running context handoff. |
| Codex Security plugin | Useful before public deploy/security work, Authelia, secret handling, reverse proxy changes, and plugin marketplace work. Not needed for every small patch. |
| Hugging Face plugin or `hf` CLI | Useful for future model metadata refreshes, downloads, cache behavior, and local evals. Optional unless actively changing `templates/models.yaml`. |
| OpenAI Developers / OpenAI docs MCP | Useful only if AGmind grows an OpenAI API integration, agent, MCP server, or ChatGPT app surface. Not needed for core homelab deploy work. |
| Notion or Mem | Mem is installed in this Codex environment, but external memory remains optional. Do not make it the source of truth unless the user explicitly wants planning moved out of `.planning/`. |
| GitHub Actions runner monitor | First product-side layer exists as `agmind ci status` for queue/recent-run and runner online/busy visibility. Still missing cancel/rerun and self-hosted service health actions. |
| Cloudflare plugin/MCP | Optional for Cloudflare Tunnel/DNS/Zero Trust if that becomes an official deploy target. Do not require it for local/Proxmox/k3s paths. |
| Multi-agent tools | Available through deferred tooling, but only use when the user explicitly asks for parallel agents or delegation. Normal project stewardship should stay in the main thread to keep `.planning/` coherent. |
| CircleCI plugin | Enabled, but not useful for this repository unless CI moves from GitHub Actions to CircleCI. Do not route current self-hosted runner work through it. |

## Project-Level Plugins AGmind May Need

These are AGmind product plugins, not Codex plugins:

1. Backend package template for `agmind.backends`.
2. Thin Dify tool plugin for RAGFlow/Docling sidecars.
3. Observability exporter/dashboard bundle.
4. Service bundle marketplace command: `agmind plugin list/install`.
5. Optional Authelia 2FA setup bundle once security UX is prioritized.

## Current Decision

No extra plugin is required before the next engineering task. The current
environment has enough to continue:

- shell
- GitHub connector / `gh`
- Docker
- `uv`
- Mem plugin installed for optional external recall
- browser for current upstream facts

The current operational plugin gap is now narrower: `agmind ci status` gives
queue/recent-run and self-hosted runner online/busy visibility through `gh`.
The remaining gap is actionability: stale-run cancel/rerun and self-hosted
service health should be easier to trigger from the agent loop.

The current repo hygiene gap found during the 2026-05-24 tooling review was
stale `AGmindx86` live metadata. Runtime metadata and schema ids should point
at `botAGI/AGmind64`; historical session files may still mention `AGmindx86`
as old context.

Current plugin decision after research: keep project memory canonical in
`.planning/`, use GitHub + Superpowers + local shell as the default working
stack, and use Mem only as optional external recall when explicitly helpful.
Avoid making external PKM or SaaS tools mandatory for contributors.
