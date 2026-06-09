"""Phase 2.2 (A8): the zero-egress telemetry gate.

Every service that CAN disable phone-home/telemetry declaratively (via an env key) must
carry the verified key=value from Phase 2.1; UI-only opt-out services (no env knob) are
listed exempt with a reason. The gate fails on a missing/wrong key AND on a stale entry
(an exempt service that gained the env knob, or a required/exempt service that left the
catalog) so neither list can rot — mirroring the A7 healthcheck-coverage discipline.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "checks" / "egress_telemetry_check.py"


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location("egress_telemetry_check", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_catalog_passes_egress_gate() -> None:
    """The real catalog must satisfy every required egress key (no missing/wrong values)."""
    gate = _load_gate()
    issues = gate.check_egress()  # type: ignore[attr-defined]
    assert issues == [], "egress violations:\n" + "\n".join(i["message"] for i in issues)


def test_missing_required_key_is_flagged() -> None:
    gate = _load_gate()
    # qdrant requires QDRANT__TELEMETRY_DISABLED=true; a descriptor without it must fail.
    fake = {"qdrant": SimpleNamespace(env={})}
    issues = gate.check_egress(fake)  # type: ignore[attr-defined]
    assert any(
        i["service"] == "qdrant" and "QDRANT__TELEMETRY_DISABLED" in i["message"] for i in issues
    )


def test_wrong_value_is_flagged() -> None:
    gate = _load_gate()
    fake = {"qdrant": SimpleNamespace(env={"QDRANT__TELEMETRY_DISABLED": "false"})}
    issues = gate.check_egress(fake)  # type: ignore[attr-defined]
    assert any(i["service"] == "qdrant" and "false" in i["message"] for i in issues)


def test_correct_value_passes() -> None:
    gate = _load_gate()
    fake = {"qdrant": SimpleNamespace(env={"QDRANT__TELEMETRY_DISABLED": "true"})}
    issues = gate.check_egress(fake)  # type: ignore[attr-defined]
    assert issues == []


def test_stale_exempt_service_that_gained_the_env_knob_is_flagged() -> None:
    """An exempt (UI-only) service that picks up a required key must be moved out of exempt."""
    gate = _load_gate()
    # portainer is UI-only exempt; pretend it now has a required key → stale exemption.
    exempt_name = next(iter(gate._EGRESS_EXEMPT))  # type: ignore[attr-defined]
    # Cross-reference a real required key onto the exempt service.
    some_required_key = next(iter(next(iter(gate._REQUIRED_EGRESS_ENV.values()))))  # type: ignore[attr-defined]
    fake = {exempt_name: SimpleNamespace(env={some_required_key: "true"})}
    issues = gate.check_egress(fake)  # type: ignore[attr-defined]
    assert any(exempt_name in i["message"] and "stale" in i["message"].lower() for i in issues)


def test_stale_required_service_not_in_catalog_is_flagged() -> None:
    """A required-env service that left the catalog is a stale rule (full-catalog only)."""
    gate = _load_gate()
    # Synthetic full catalog missing every required service → every rule is stale.
    fake = {"unrelated-svc": SimpleNamespace(env={})}
    issues = gate.check_egress(fake, full_catalog=True)  # type: ignore[attr-defined]
    assert any("not in the catalog" in i["message"] for i in issues)


def test_exempt_services_have_nonempty_reasons() -> None:
    gate = _load_gate()
    assert gate._EGRESS_EXEMPT, "exempt map must not be empty"  # type: ignore[attr-defined]
    assert all(reason.strip() for reason in gate._EGRESS_EXEMPT.values())  # type: ignore[attr-defined]


def test_required_and_exempt_sets_are_disjoint() -> None:
    gate = _load_gate()
    overlap = set(gate._REQUIRED_EGRESS_ENV) & set(gate._EGRESS_EXEMPT)  # type: ignore[attr-defined]
    assert overlap == set(), f"a service cannot be both required-env and UI-only exempt: {overlap}"


def test_cli_text_output_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "egress" in result.stdout.lower()


def test_cli_json_output_shape() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["error_count"] == 0
    assert payload["required_count"] > 0
    assert payload["exempt_count"] > 0


def test_registered_in_governance_aggregator() -> None:
    from agmind.governance import DEFAULT_CHECKS, run_governance_checks

    assert "egress" in DEFAULT_CHECKS, "egress must be in DEFAULT_CHECKS so it runs aggregate"
    report = run_governance_checks(checks=("egress",), structured=True)
    assert report.ok, "aggregate egress check FAILED on the current catalog"


def test_ci_runs_a8_egress_gate() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/checks/egress_telemetry_check.py" in workflow, (
        "A8 egress gate must be wired into ci.yml beside A6/A7"
    )


def test_pre_commit_runs_a8_egress_gate() -> None:
    import yaml

    config = yaml.safe_load((_REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [h for repo in config["repos"] if repo["repo"] == "local" for h in repo["hooks"]]
    ids = {h["id"] for h in hooks}
    assert "agmind-egress-telemetry-check" in ids, "A8 egress gate must be a pre-commit hook"
