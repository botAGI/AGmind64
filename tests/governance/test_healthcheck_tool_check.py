"""A6 gate tests — healthcheck must not invoke a tool absent from the image."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "checks" / "healthcheck_tool_check.py"
_spec = importlib.util.spec_from_file_location("healthcheck_tool_check", _GATE)
assert _spec and _spec.loader
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def test_extract_cmd_curl() -> None:
    assert hc._extract_external_tools(["CMD", "curl", "-f", "http://x/health"]) == ["curl"]


def test_extract_cmd_bash_devtcp_is_not_external() -> None:
    # qdrant's fixed healthcheck — bash builtin /dev/tcp, no external binary.
    assert (
        hc._extract_external_tools(["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/6333"]) == []
    )


def test_extract_cmd_shell_curl() -> None:
    assert hc._extract_external_tools(["CMD-SHELL", "curl -f http://x || exit 1"]) == ["curl"]


def test_command_position_ignores_subcommand() -> None:
    # `mysqladmin ping` / `redis-cli ... ping` — `ping` is a SUBCOMMAND, not the binary.
    assert hc._extract_external_tools(["CMD", "mysqladmin", "ping"]) == []
    assert hc._extract_external_tools(["CMD-SHELL", "redis-cli -a x ping"]) == []


def test_absolute_path_not_flagged() -> None:
    assert hc._extract_external_tools(["CMD", "/usr/bin/healthcheck"]) == []


def test_qdrant_curl_is_a_recorded_regression() -> None:
    # The historical qdrant-curl bug: image is recorded as lacking curl so a re-introduced
    # curl healthcheck on qdrant is caught at authoring.
    assert "curl" in hc._KNOWN_MISSING_TOOLS["qdrant/qdrant:v1.18.0"]


def test_current_catalog_has_no_absent_tool_healthcheck() -> None:
    """The shipped catalog must not invoke a proven-absent tool in any healthcheck."""
    errors, _unknowns, count = hc.check_healthcheck_tools()
    assert errors == [], "A6 violations in catalog:\n" + "\n".join(e["message"] for e in errors)
    assert count >= 1, "expected at least one external-tool healthcheck (llama curl) in the catalog"


def test_known_missing_image_with_that_tool_is_flagged() -> None:
    """Unit-level: the deny-map + extractor flag a curl healthcheck on a curl-less image."""
    img = "qdrant/qdrant:v1.18.0"
    tools = hc._extract_external_tools(["CMD", "curl", "-f", "http://localhost:6333/healthz"])
    flagged = [t for t in tools if t in hc._KNOWN_MISSING_TOOLS.get(img, frozenset())]
    assert flagged == ["curl"]
