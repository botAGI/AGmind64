"""Phase 09-04 (M8): the healthcheck-coverage gate (A7).

Every catalog service must ship a Docker `health:` probe or be explicitly classified exempt,
so the deploy runner's "running == ready" shortcut can never apply to an un-considered
service."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.backend_any

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "checks" / "healthcheck_coverage_check.py"
)


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location("healthcheck_coverage_check", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_catalog_has_full_healthcheck_coverage() -> None:
    """The real catalog must pass: every service has a probe or a classified exemption."""
    gate = _load_gate()
    errors = gate.check_coverage()  # type: ignore[attr-defined]
    assert errors == [], "uncovered/stale services:\n" + "\n".join(errors)


def test_unclassified_no_health_service_fails() -> None:
    gate = _load_gate()
    fake = {"brand-new-svc": SimpleNamespace(health=None)}
    errors = gate.check_coverage(fake)  # type: ignore[attr-defined]
    assert any("brand-new-svc" in e and "not classified" in e for e in errors)


def test_service_with_health_passes() -> None:
    gate = _load_gate()
    fake = {"probed-svc": SimpleNamespace(health=SimpleNamespace(test=["CMD", "true"]))}
    errors = gate.check_coverage(fake)  # type: ignore[attr-defined]
    assert errors == []


def test_stale_exemption_with_health_fails() -> None:
    """An exempt service that gains a probe must be flagged so the list cannot rot."""
    gate = _load_gate()
    # ssrf-proxy is exempt; give the fake one a health block → stale exemption.
    fake = {"ssrf-proxy": SimpleNamespace(health=SimpleNamespace(test=["CMD", "true"]))}
    errors = gate.check_coverage(fake)  # type: ignore[attr-defined]
    assert any("ssrf-proxy" in e and "stale" in e for e in errors)
