# Contributing to AGmind

Thanks for your interest. This document covers the dev environment, the quality
gates that CI enforces, and the branch/commit conventions.

## Dev setup

AGmind targets **Python 3.12+**. Use [`uv`](https://github.com/astral-sh/uv) if
you have it (CI does); plain `venv` works too.

```bash
git clone https://github.com/botAGI/AGmind64.git agmind
cd agmind

# uv (preferred — mirrors CI dependency resolution)
uv venv --python python3 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# or plain venv
python3 -m venv .venv
.venv/bin/python -m pip install -U pip setuptools wheel
.venv/bin/python -m pip install -e ".[dev]"
```

Install the git hooks once:

```bash
make pre-commit-install
```

> **Pitfall:** a stale local `.venv` can mask undeclared dependencies and pin an
> older `typer`/`click`. If something is green locally but red in CI, re-verify
> in a **fresh** `uv` venv before trusting "green".

## Quality gates

Run these before opening a PR — they mirror CI. Each is also a `make` target.

```bash
make format          # ruff format + ruff check --fix
make lint            # ruff check . + mypy agmind/
make test            # pytest with coverage  (test-fast skips slow)
make audit           # forbid legacy/NVIDIA/CUDA patterns in the main tree
make pre-commit-run  # run all hooks against all files
make schema-validate # validate templates/services/*.yaml against the schema
```

Direct equivalents the lead uses:

```bash
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/python -m mypy agmind/
.venv/bin/pytest -q
```

`make pre-commit-run` also runs the governance/topology/landmine checks under
`scripts/checks/` plus `gitleaks`, `shellcheck`, `hadolint`, and `ansible-lint`.

### TDD is required

For any behaviour change, write a failing test first, then the minimal patch to
make it pass. New services, CLI flags, or descriptors need accompanying tests.

### Hardware audit

This project is AMD Strix Halo (Vulkan/ROCm). **Never** introduce proprietary
GPU-vendor, datacenter-accelerator, or alternative inference-server references —
even in comments. `make audit` (the `audit_forbidden.py` gate) lists the exact
banned tokens and fails the build on them.

### Service descriptors

When adding or editing a `templates/services/*.yaml`, follow the
service-descriptor checklist (permissions, mounts, env/secrets, co-deploy) and
bump the `service_count` assertions in the contract/governance tests — one new
descriptor ripples through several gates.

## Commit conventions

Commits use [Conventional Commits](https://www.conventionalcommits.org)
(enforced by the `conventional-pre-commit` commit-msg hook):

```
fix(security): rank warning severity so audit doesn't crash
feat(cluster): add mDNS peer inspection
docs(readme): rewrite front page
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.

## Language policy

- **Code, identifiers, and commit messages: English.** Symbol names, log
  strings, and Conventional-Commit subjects/bodies are always English so the
  history and API read consistently for every contributor.
- **Code comments and docstrings: Russian is fine** — it is an established
  project convention. Keep them accurate: an out-of-date comment is worse than
  none, so update the comment whenever you change the code it describes.
- **User-facing docs ship EN + RU.** `README.md` (English) and `README.ru.md`
  (Russian) are meaning-mirrors: the prose may diverge to read naturally in each
  language, but their code blocks must stay byte-identical and their heading
  topology must match. `scripts/checks/docs_mirror_check.py` (wired into
  pre-commit) gates this — edit one file and its mirror in the same change.

## Branch & release flow

- **`develop`** is the working branch — open PRs and push fix-on-top commits here.
- **`main`** is the protected/default branch and source of releases.
- Promotion is a fast-forward of `develop` onto `main`, gated on a **green CI
  conclusion** (not just ancestry). Never amend + force-push a shared branch;
  always fix on top.

PRs should be focused, pass every gate above, and keep docs in sync. If you edit
a doc that ships EN + RU code blocks, keep the code blocks byte-identical.

## License

By contributing you agree your contributions are licensed under Apache-2.0.
