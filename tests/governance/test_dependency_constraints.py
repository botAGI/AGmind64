"""Tests for Python/backend dependency constraint planes."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _constraints_check_module():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    return importlib.import_module("constraints_check")


def test_constraints_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "constraints_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "dependency constraints OK" in result.stdout


def test_constraints_check_script_json_output() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "constraints_check.py"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["plane_count"] == 5
    assert payload["package_rule_count"] == 44
    assert payload["error_count"] == 0
    assert payload["issues"] == []


def test_required_constraint_planes_exist_and_parse() -> None:
    constraints_check = _constraints_check_module()

    planes = constraints_check.load_constraint_planes(REPO_ROOT / "constraints")

    assert set(planes) == {"core", "dev", "cpu", "vulkan", "rocm-gfx1151"}
    assert "typer" in planes["core"]
    assert "pytest" in planes["dev"]
    assert "torch" in planes["cpu"]
    assert "llama-cpp-python" in planes["vulkan"]
    assert "torch" in planes["rocm-gfx1151"]


def test_pyproject_and_dockerfile_dependencies_are_covered() -> None:
    constraints_check = _constraints_check_module()

    errors = constraints_check.validate_constraints(REPO_ROOT)

    assert errors == []


def test_backend_dockerfiles_use_matching_constraints() -> None:
    expected = {
        "Dockerfile.cpu": "cpu",
        "Dockerfile.vulkan": "vulkan",
        "Dockerfile.rocm": "rocm-gfx1151",
    }

    for dockerfile, plane in expected.items():
        text = (REPO_ROOT / "docker" / dockerfile).read_text(encoding="utf-8")
        assert "COPY constraints /opt/agmind/constraints" in text
        assert f"-c /opt/agmind/constraints/{plane}.txt" in text


def test_backend_dockerfiles_install_core_before_editable_no_deps() -> None:
    expected = {
        "Dockerfile.cpu": ("cpu", "cpu"),
        "Dockerfile.vulkan": ("vulkan", "vulkan"),
        "Dockerfile.rocm": ("rocm-gfx1151", "rocm"),
    }

    for dockerfile, (plane, extra) in expected.items():
        text = (REPO_ROOT / "docker" / dockerfile).read_text(encoding="utf-8")
        normalized = " ".join(text.replace("\\\n", " ").split())

        assert (
            f"pip install -c /opt/agmind/constraints/{plane}.txt "
            "-r /opt/agmind/constraints/core.txt"
        ) in normalized
        editable_install = (
            "pip install --no-build-isolation --no-deps "
            f'-c /opt/agmind/constraints/{plane}.txt -e ".[{extra}]"'
        )
        assert editable_install in normalized


def test_dockerignore_excludes_local_build_artifacts() -> None:
    dockerignore = REPO_ROOT / ".dockerignore"
    patterns = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {
        ".git/",
        ".venv/",
        "*.egg-info/",
        ".planning/",
        "docs/superpowers/",
        "coverage.xml",
    }.issubset(patterns)
