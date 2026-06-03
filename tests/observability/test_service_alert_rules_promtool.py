"""Deep validation of the service alert rules via promtool (image-guarded).

promtool validates PromQL semantics, not just YAML — it catches a malformed expr
that yaml.safe_load happily accepts. Guarded on the pinned prometheus image being
present locally (self-hosted CI has it); skipped otherwise. promtool canNOT verify
the metric series exist against our exact exporter versions — that needs a one-time
check on the live GPU host (documented in the research).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULES_DIR = _REPO_ROOT / "templates" / "observability" / "prometheus" / "rules"
_PROM_IMAGE = "prom/prometheus:v3.5.3"


def _promtool_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", _PROM_IMAGE],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


@pytest.mark.skipif(not _promtool_available(), reason=f"{_PROM_IMAGE} not present locally")
def test_promtool_accepts_all_rule_files() -> None:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0",
            "-v",
            f"{_RULES_DIR}:/rules:ro",
            "--entrypoint",
            "promtool",
            _PROM_IMAGE,
            "check",
            "rules",
            "/rules/services.yml",
            "/rules/system.yml",
            "/rules/llama.yml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SUCCESS" in (result.stdout + result.stderr)
