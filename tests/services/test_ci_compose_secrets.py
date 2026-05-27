"""Guard: the CI compose-validate env must cover every required descriptor secret.

`docker compose config` in the `compose-validate` job interpolates every
`${VAR:?...}` reference, so any required secret introduced by a service
descriptor must also be provided by the hand-maintained env in
`.github/workflows/ci.yml`. Without this guard the two drift apart silently and
only fail once that profile is rendered in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while not (path / "pyproject.toml").exists():
        if path == path.parent:
            raise RuntimeError("repo root with pyproject.toml not found")
        path = path.parent
    return path


def test_ci_compose_validate_env_covers_required_secrets() -> None:
    repo = _repo_root()

    required: set[str] = set()
    for descriptor in (repo / "templates" / "services").glob("*.yaml"):
        required |= set(re.findall(r"\$\{([A-Z0-9_]+):\?", descriptor.read_text(encoding="utf-8")))

    ci_text = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    provided = set(re.findall(r"^\s*([A-Z0-9_]+)=", ci_text, re.MULTILINE))

    missing = required - provided
    assert not missing, (
        "compose-validate env in .github/workflows/ci.yml is missing required "
        f"descriptor secrets: {sorted(missing)}"
    )
