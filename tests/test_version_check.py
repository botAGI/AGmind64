"""Phase P: tests for scripts/version_check.py (regex + compare + report)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "version_check.py"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True, text=True, check=False,
    )


# ---- semver compare ----


def test_compare_up_to_date() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    assert vc._compare("v1.2.3", "1.2.3") == "up_to_date"


def test_compare_patch() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    assert vc._compare("1.2.3", "1.2.5") == "patch"


def test_compare_minor() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    assert vc._compare("1.2.3", "1.3.0") == "minor"


def test_compare_major() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    assert vc._compare("1.2.3", "2.0.0") == "major"


# ---- compose pin scanner ----


def test_scan_compose_finds_known_pins() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    pins = vc.scan_compose_pins(REPO_ROOT / "templates" / "services")
    images = {p[0] for p in pins}
    assert "infiniflow/ragflow" in images
    assert "langgenius/dify-api" in images
    assert "ghcr.io/ggml-org/llama.cpp" in images


def test_scan_extracts_correct_tag() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    pins = vc.scan_compose_pins(REPO_ROOT / "templates" / "services")
    by_image = {p[0]: p[1] for p in pins}
    assert by_image["infiniflow/ragflow"] == "v0.25.5"


# ---- holds parser ----


def test_holds_loaded() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    holds = vc.load_holds()
    assert "ghcr.io/ggml-org/llama.cpp" in holds


# ---- end-to-end (offline mode) ----


def test_offline_smoke_runs_no_crash() -> None:
    """`scripts/version_check.py --offline` должен бежать без network."""
    p = _run("--offline")
    # Output goes to stdout — markdown
    assert p.returncode == 0
    assert "Upstream Version Check" in p.stdout
    assert "ragflow" in p.stdout.lower()


def test_offline_json_output(tmp_path: Path) -> None:
    json_out = tmp_path / "out.json"
    p = _run("--offline", "--json", str(json_out))
    assert p.returncode == 0
    data = json.loads(json_out.read_text())
    assert isinstance(data, list)
    assert any(d["image"] == "infiniflow/ragflow" for d in data)


def test_holds_skip_probe() -> None:
    """Образы из version_holds.yaml выходят как 'hold' даже в online mode."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    reports = vc.build_reports(probe_fn=lambda _img: "v999.999.999")
    holds_in_report = [r for r in reports if r.status == "hold"]
    images = {r.image for r in holds_in_report}
    # ghcr.io/ggml-org/llama.cpp is held per templates/version_holds.yaml
    assert "ghcr.io/ggml-org/llama.cpp" in images


def test_markdown_table_format() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc
    reports = vc.build_reports(probe_fn=lambda _img: None)
    md = vc.render_markdown(reports)
    assert "| Component |" in md
    assert "### Legend" in md
    assert "### How to bump" in md
