"""Phase 09-03 (M8): the local mypy pre-commit hook must propagate mypy's exit code.

`mypy ... | head -20` (no `pipefail`) returns head's exit status (0), so the hook passes
even when mypy fails — a hollow local gate. Assert the hook either avoids the pipe or sets
`pipefail`."""

from __future__ import annotations

import pathlib

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_CONFIG = pathlib.Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"


def _mypy_entries() -> list[str]:
    cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    return [
        hook["entry"]
        for repo in cfg.get("repos", [])
        for hook in repo.get("hooks", [])
        if hook.get("id") == "mypy"
    ]


def test_mypy_precommit_hook_exists() -> None:
    assert _mypy_entries(), "mypy pre-commit hook not found"


def test_mypy_precommit_hook_does_not_mask_exit_code() -> None:
    for entry in _mypy_entries():
        assert "mypy" in entry
        if "|" in entry:
            assert "pipefail" in entry, (
                "piped mypy hook must `set -o pipefail` or drop the pipe — "
                f"otherwise the pipeline's exit code is the tail's, not mypy's: {entry!r}"
            )
