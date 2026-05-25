#!/usr/bin/env python3
"""Validate AGmind dependency constraint planes."""

from __future__ import annotations

import json
import re
import shlex
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS_DIR = REPO_ROOT / "constraints"

REQUIRED_PLANES = ("core", "dev", "cpu", "vulkan", "rocm-gfx1151")
EXTRA_TO_PLANE = {
    "dev": "dev",
    "cpu": "cpu",
    "vulkan": "vulkan",
    "rocm": "rocm-gfx1151",
}
DOCKERFILE_TO_PLANE = {
    "Dockerfile.cpu": "cpu",
    "Dockerfile.vulkan": "vulkan",
    "Dockerfile.rocm": "rocm-gfx1151",
}

_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")
_SPEC_RE = re.compile(r"(===|==|~=|!=|<=|>=|<|>)")
_PIP_INSTALL_RE = re.compile(r"\bpip\s+install\b")
_SKIP_OPTION_WITH_VALUE = {
    "-c",
    "--constraint",
    "-r",
    "--requirement",
    "-i",
    "--index-url",
    "--extra-index-url",
    "-f",
    "--find-links",
    "--no-binary",
    "--only-binary",
}
_SKIP_FLAGS = {
    "--pre",
    "--upgrade",
    "--no-cache-dir",
    "--force-reinstall",
    "--no-build-isolation",
}


@dataclass(frozen=True)
class ConstraintPin:
    """One package constraint from a plane file."""

    name: str
    specifier: str
    file: str


def normalize_name(name: str) -> str:
    """Normalize Python package names enough for local constraint checks."""
    return name.strip().lower().replace("_", "-")


def package_name_from_req(req: str) -> str:
    """Extract package name from a PEP 508-ish requirement string."""
    match = _REQ_NAME_RE.match(req.strip())
    return normalize_name(match.group(1)) if match else ""


def parse_constraint_file(path: Path) -> dict[str, ConstraintPin]:
    """Parse package constraints from one constraints/*.txt file."""
    pins: dict[str, ConstraintPin] = {}
    if not path.exists():
        return pins

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-c ", "--constraint ")):
            continue
        name = package_name_from_req(line)
        specifier = line[len(name) :].strip() if name else ""
        pins[name] = ConstraintPin(
            name=name,
            specifier=specifier,
            file=str(path.relative_to(REPO_ROOT)),
        )
    return pins


def load_constraint_planes(root: Path = CONSTRAINTS_DIR) -> dict[str, dict[str, ConstraintPin]]:
    """Load all required constraint planes."""
    return {
        plane: parse_constraint_file(root / f"{plane}.txt")
        for plane in REQUIRED_PLANES
        if (root / f"{plane}.txt").exists()
    }


def _constraint_includes(path: Path) -> set[str]:
    includes: set[str] = set()
    if not path.exists():
        return includes
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line.startswith("-c "):
            includes.add(line.split(maxsplit=1)[1])
        elif line.startswith("--constraint "):
            includes.add(line.split(maxsplit=1)[1])
    return includes


def _pyproject_dependency_groups(pyproject: Path) -> dict[str, set[str]]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    groups: dict[str, set[str]] = {
        "core": {
            package_name_from_req(req)
            for req in project.get("dependencies", []) or []
            if package_name_from_req(req)
        }
    }
    optional = project.get("optional-dependencies", {}) or {}
    for extra, reqs in optional.items():
        groups[str(extra)] = {
            package_name_from_req(req) for req in reqs or [] if package_name_from_req(req)
        }
    return groups


def _logical_dockerfile_lines(path: Path) -> list[str]:
    logical: list[str] = []
    current = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        current += stripped
        logical.append(current)
        current = ""
    if current:
        logical.append(current)
    return logical


def scan_dockerfile_pip_packages(path: Path) -> set[str]:
    """Return package names installed by `pip install` commands in a Dockerfile."""
    packages: set[str] = set()
    if not path.exists():
        return packages

    for line in _logical_dockerfile_lines(path):
        if not _PIP_INSTALL_RE.search(line):
            continue
        tokens = shlex.split(line.replace("&&", " && "))
        install_starts: list[int] = []
        for index, token in enumerate(tokens[:-1]):
            if token == "pip" or token.endswith("/pip"):
                if tokens[index + 1] == "install":
                    install_starts.append(index + 2)
            elif (
                token == "python3"
                and index + 3 < len(tokens)
                and tokens[index + 1] == "-m"
                and tokens[index + 2] == "pip"
                and tokens[index + 3] == "install"
            ):
                install_starts.append(index + 4)

        for start in install_starts:
            index = start
            while index < len(tokens) and tokens[index] != "&&":
                token = tokens[index]
                if token in {"RUN", "install"}:
                    index += 1
                    continue
                if token in _SKIP_OPTION_WITH_VALUE:
                    index += 2
                    continue
                if token in _SKIP_FLAGS or token.startswith("--"):
                    index += 1
                    continue
                if token == "-e":
                    index += 2
                    continue
                if token.startswith(".") or token.startswith("/"):
                    index += 1
                    continue
                name = package_name_from_req(token)
                if name:
                    packages.add(name)
                index += 1
    return packages


def validate_constraints(repo_root: Path = REPO_ROOT) -> list[str]:
    """Validate constraint coverage across pyproject and backend Dockerfiles."""
    errors: list[str] = []
    constraints_dir = repo_root / "constraints"
    planes = load_constraint_planes(constraints_dir)

    for plane in REQUIRED_PLANES:
        path = constraints_dir / f"{plane}.txt"
        if not path.exists():
            errors.append(f"missing constraint plane: {path.relative_to(repo_root)}")
            continue
        if plane != "core" and "core.txt" not in _constraint_includes(path):
            errors.append(f"{path.relative_to(repo_root)} must include -c core.txt")

    for plane, pins in planes.items():
        for pin in pins.values():
            if not _SPEC_RE.search(pin.specifier):
                errors.append(f"{pin.file}: {pin.name} must include a version specifier")

    groups = _pyproject_dependency_groups(repo_root / "pyproject.toml")
    for package in sorted(groups.get("core", set())):
        if package not in planes.get("core", {}):
            errors.append(f"core dependency missing from constraints/core.txt: {package}")

    for extra, plane in EXTRA_TO_PLANE.items():
        for package in sorted(groups.get(extra, set())):
            if package not in planes.get(plane, {}):
                errors.append(f"{extra} dependency missing from constraints/{plane}.txt: {package}")

    docker_dir = repo_root / "docker"
    for dockerfile, plane in DOCKERFILE_TO_PLANE.items():
        for package in sorted(scan_dockerfile_pip_packages(docker_dir / dockerfile)):
            if package not in planes.get(plane, {}):
                errors.append(
                    f"{dockerfile} pip package missing from constraints/{plane}.txt: {package}"
                )

    return errors


def _issue(message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "kind": "constraint_validation",
        "message": message,
    }


def _payload(*, planes: dict[str, dict[str, ConstraintPin]], errors: list[str]) -> dict[str, Any]:
    return {
        "ok": not errors,
        "plane_count": len(planes),
        "package_rule_count": sum(len(pins) for pins in planes.values()),
        "error_count": len(errors),
        "issues": [_issue(error) for error in errors],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    errors = validate_constraints(REPO_ROOT)
    planes = load_constraint_planes(CONSTRAINTS_DIR)
    payload = _payload(planes=planes, errors=errors)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    if errors:
        print("Dependency constraint validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    package_count = payload["package_rule_count"]
    print(f"dependency constraints OK: {len(planes)} planes, {package_count} package rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
