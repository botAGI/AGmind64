# Session 2026-05-23 — Codebase map + Claude artifact cleanup

## Context

User asked to:

- rebuild/update the codebase map,
- clean Claude artifacts,
- decide what plugins/tools Codex needs to continue stewarding the project.

## Actions

- Removed active Claude project files:
  - `.claude/agents/deploy-verifier.md`
  - `.claude/commands/{shellcheck,stack-status,verify}.md`
  - `.claude/settings*.json`
  - `.claude/scheduled_tasks.lock`
  - `CLAUDE.md`
- Added `.claude/` and `CLAUDE.md` to `.gitignore`.
- Rebuilt `.planning/codebase/` around current post-M5/post-CI state:
  - `INDEX.md`
  - `ARCHITECTURE.md`
  - `DEPENDENCIES.md`
  - `EXTENSION_POINTS.md`
  - `INVARIANTS.md`
  - `PITFALLS.md`
  - `AGENT_TOOLING.md`
- Updated `STATE.md`, `ROADMAP.md`, and `BACKLOG.md` to stop describing the
  stale 101-file cloud-artifact layer as current work.
- Pushed cleanup commit `2a3c21f`; GitHub Actions run `26333098114` completed
  successfully.
- Observed that Strix smoke could start before the Docker backend matrix
  finished, which risks testing stale local `agmind-*:ci` images on the
  self-hosted runner. Added an explicit `docker-build` dependency for
  `test-strix-halo`.

## Current tool decision

No new plugin is mandatory before the next engineering task. Required tooling
is already available:

- GitHub connector / `gh`
- local shell
- Docker
- `uv` / `uvx`
- web lookup for current upstream facts

Best next operational improvement: prevent Dependabot/release-drafter from
occupying the only self-hosted Strix Halo runner ahead of required develop CI.

## Verification

Ran:

```bash
.venv/bin/pre-commit run --all-files --show-diff-on-failure
python3 scripts/audit_forbidden.py --fail --json /tmp/agmind-audit-cleanup.json
$HOME/.local/bin/uvx check-jsonschema --schemafile templates/schemas/service.json templates/services/*.yaml
find . -maxdepth 4 \( -path './.claude*' -o -name 'CLAUDE.md' \) -print
git status --short --branch
```

Result: pre-commit passed, forbidden audit found 0 findings, service schema
validation passed, and no live `.claude/` or `CLAUDE.md` path remained in the
worktree.
