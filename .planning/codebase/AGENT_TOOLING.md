# Agent Tooling And Plugins

Last updated: 2026-05-23.

This is the current answer to: "what plugins/tools does Codex need to run this
project well?"

## Required For Day-To-Day Project Stewardship

### 1. GitHub connector

Status: available in this environment.

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
| GitHub Actions runner manager | Queue visibility, busy runner reason, cancel/rerun without relying on `gh` polling. |
| GitHub Projects/issues connector | Turn `.planning/BACKLOG.md` live queue into tracked issues without manual duplication. |
| Release automation connector | Control release-drafter and Dependabot interactions so they do not block the self-hosted runner. |
| Docs/code-search connector | Faster cross-repo lookup for Dify/RAGFlow/llama.cpp/ROCm source evidence. |
| Long-running CI monitor | Watch a run, fetch failed logs, and summarize deltas automatically. |

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
- browser for current upstream facts

The one operational gap to fix in the repo is workflow ownership: Dependabot
and release-drafter should not compete with required develop CI for the only
Strix Halo runner.
