"""Guard: every test file must carry a backend marker.

The only CI test lane runs `pytest -m "backend_any or backend_cpu"`
(`.github/workflows/ci.yml`). A test file with no backend marker collects to
zero tests under that filter, so it passes locally (unfiltered) yet never gates
CI. This guard fails if any `tests/**/test_*.py` that defines tests references no
backend marker at all.

Exclusion: ``tests/integration/`` is intentionally exempt. Those files carry
``pytestmark = pytest.mark.integration`` and are excluded from the default CI
lane via ``addopts = -m "not integration"`` rather than backend markers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_BACKEND_MARKERS = ("backend_any", "backend_cpu", "backend_vulkan", "backend_rocm")

# Test sub-packages that are intentionally not backend-marked.
# They use their own opt-in marker (e.g. ``pytest.mark.integration``) and are
# excluded from the CI lane by other means (addopts / explicit -m flags).
_EXCLUDED_PACKAGES = ("tests/integration",)


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
        rel = str(test_file.relative_to(repo))
        # Skip intentionally-excluded integration packages.
        if any(rel.startswith(pkg) for pkg in _EXCLUDED_PACKAGES):
            continue
        text = test_file.read_text(encoding="utf-8")
        if not re.search(r"^\s*(async\s+)?def test_", text, re.MULTILINE):
            continue  # no test functions (helpers/fixtures only)
        # Match a real `pytest.mark.<backend>` marker, not the bare word appearing in a
        # docstring/comment (review POLISH marker-coverage-substring-match).
        if not re.search(rf"pytest\.mark\.(?:{'|'.join(_BACKEND_MARKERS)})", text):
            offenders.append(rel)

    assert not offenders, (
        "test files with no backend marker are silently skipped by the CI lane "
        f'(-m "backend_any or backend_cpu"): {offenders}'
    )
