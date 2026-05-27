"""Guard: every test file must carry a backend marker.

The only CI test lane runs `pytest -m "backend_any or backend_cpu"`
(`.github/workflows/ci.yml`). A test file with no backend marker collects to
zero tests under that filter, so it passes locally (unfiltered) yet never gates
CI. This guard fails if any `tests/**/test_*.py` that defines tests references no
backend marker at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_BACKEND_MARKERS = ("backend_any", "backend_cpu", "backend_vulkan", "backend_rocm")


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while not (path / "pyproject.toml").exists():
        if path == path.parent:
            raise RuntimeError("repo root with pyproject.toml not found")
        path = path.parent
    return path


def test_every_test_file_has_a_backend_marker() -> None:
    repo = _repo_root()
    offenders: list[str] = []
    for test_file in sorted((repo / "tests").rglob("test_*.py")):
        text = test_file.read_text(encoding="utf-8")
        if not re.search(r"^\s*(async\s+)?def test_", text, re.MULTILINE):
            continue  # no test functions (helpers/fixtures only)
        if not any(marker in text for marker in _BACKEND_MARKERS):
            offenders.append(str(test_file.relative_to(repo)))

    assert not offenders, (
        "test files with no backend marker are silently skipped by the CI lane "
        f'(-m "backend_any or backend_cpu"): {offenders}'
    )
