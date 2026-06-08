"""Task H.6 (1): `agmind render catalog` produces a schema-valid catalog.

Tests:
- The catalog schema file exists at templates/schemas/catalog.json.
- `agmind render catalog --version 0.0.0-test` emits valid JSON.
- The emitted catalog validates against templates/schemas/catalog.json.
- All 42 service descriptors appear in the catalog's `services` map.
- Each service entry carries a `digest` field as `sha256:<64hex>`.
- The backends block contains all four expected keys.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_SCHEMA_PATH = _REPO_ROOT / "templates" / "schemas" / "catalog.json"
_SERVICES_DIR = _REPO_ROOT / "templates" / "services"


def _load_schema() -> dict:
    return json.loads(_CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))


def test_catalog_schema_exists() -> None:
    """templates/schemas/catalog.json must exist and be parseable JSON."""
    assert _CATALOG_SCHEMA_PATH.exists(), f"missing catalog schema: {_CATALOG_SCHEMA_PATH}"
    schema = _load_schema()
    assert schema.get("schema_version") is None  # catalog schema does not embed schema_version
    assert "services" in schema.get("properties", {})
    assert "backends" in schema.get("properties", {})


def test_render_catalog_validates(tmp_path: Path) -> None:
    """agmind render catalog --version 0.0.0-test -o <tmp> produces schema-valid JSON."""
    out = tmp_path / "catalog-0.0.0-test.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agmind",
            "render",
            "catalog",
            "--version",
            "0.0.0-test",
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"render catalog failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert out.exists(), "output file not created"

    catalog = json.loads(out.read_text(encoding="utf-8"))
    schema = _load_schema()

    # Validate against the JSON Schema.
    jsonschema.validate(catalog, schema)


def test_render_catalog_covers_all_services(tmp_path: Path) -> None:
    """Rendered catalog services map must include all service descriptors."""
    out = tmp_path / "catalog.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agmind",
            "render",
            "catalog",
            "--version",
            "0.0.0-test",
            "--output",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))

    # Count actual descriptor files — EXCLUDING build-services (compose `build:`), which are
    # built on-host from shipped source, carry no registry digest, and are intentionally absent
    # from the pull-by-digest release catalog (mirror digest_check's build-exemption).
    import yaml

    expected_names = set()
    for path in _SERVICES_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("build") is not None:
            continue
        expected_names.add(path.stem)
    actual_names = set(catalog["services"].keys())
    assert expected_names == actual_names, (
        f"catalog services mismatch\n"
        f"  missing: {sorted(expected_names - actual_names)}\n"
        f"  extra: {sorted(actual_names - expected_names)}"
    )


def test_render_catalog_digest_format(tmp_path: Path) -> None:
    """Each catalog service entry must have digest as 'sha256:<64hex>'."""
    out = tmp_path / "catalog.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agmind",
            "render",
            "catalog",
            "--version",
            "0.0.0-test",
            "--output",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    import re

    digest_re = re.compile(r"^sha256:[a-f0-9]{64}$")
    bad = [
        (name, entry["digest"])
        for name, entry in catalog["services"].items()
        if not digest_re.match(entry.get("digest", ""))
    ]
    assert not bad, f"services with invalid digest format: {bad[:5]}"


def test_render_catalog_backends_present(tmp_path: Path) -> None:
    """Rendered catalog must have all four backend entries."""
    out = tmp_path / "catalog.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agmind",
            "render",
            "catalog",
            "--version",
            "0.0.0-test",
            "--output",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    assert set(catalog["backends"].keys()) == {"base", "cpu", "vulkan", "rocm"}
    for name, entry in catalog["backends"].items():
        assert "image" in entry, f"backend {name} missing 'image'"
        assert "digest" in entry, f"backend {name} missing 'digest'"
        assert "ref" in entry, f"backend {name} missing 'ref'"


def test_render_catalog_with_backend_digests_dir(tmp_path: Path) -> None:
    """When --backend-digests-dir is given, backend entries use those digests."""
    # Create fake digest files.
    digests_dir = tmp_path / "backends"
    digests_dir.mkdir()
    fake_digest_hex = "a" * 64
    for backend in ("base", "cpu", "vulkan", "rocm"):
        (digests_dir / f"{backend}.digest").write_text(
            f"sha256:{fake_digest_hex}", encoding="utf-8"
        )

    out = tmp_path / "catalog.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agmind",
            "render",
            "catalog",
            "--version",
            "0.0.0-test",
            "--backend-digests-dir",
            str(digests_dir),
            "--output",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    for name, entry in catalog["backends"].items():
        assert entry["digest"] == f"sha256:{fake_digest_hex}", f"backend {name} digest mismatch"
        assert entry["ref"].endswith(f"@sha256:{fake_digest_hex}")
