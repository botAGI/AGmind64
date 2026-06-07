"""CLI tests for the `agmind doctor` overhaul (--live / --fix / --bundle / --json).

Behavioural assertions via --json (never help text — CI wraps rich/typer help).
Backward compat: bare `agmind doctor` keeps today's preflight output.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agmind.cli import core_cmd
from agmind.config.validation import ConfigFinding, ConfigValidationReport
from agmind.diagnostics.doctor import CheckResult, DoctorReport

pytestmark = pytest.mark.backend_any

runner = CliRunner()


def _app() -> typer.Typer:
    app = typer.Typer()
    core_cmd.register(app)
    return app


def _ok_preflight() -> DoctorReport:
    return DoctorReport(checks=[CheckResult("kernel", "ok", "fine")])


def test_bare_doctor_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compat: no flags → preflight only, exit 0, no validate_config call."""
    called = {"validate": 0}

    def _no_validate(*a, **k):  # type: ignore[no-untyped-def]
        called["validate"] += 1
        raise AssertionError("validate_config must not run without --live")

    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)
    monkeypatch.setattr("agmind.config.validation.validate_config", _no_validate)

    result = runner.invoke(_app(), ["doctor"])
    assert result.exit_code == 0
    assert called["validate"] == 0


def test_doctor_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)
    result = runner.invoke(_app(), ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["summary"]["ok"] == 1
    assert any(c["name"] == "kernel" for c in payload["checks"])


def test_doctor_live_merges_findings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)

    report = ConfigValidationReport(
        findings=(
            ConfigFinding(
                id="env-file-mode",
                severity="error",
                message="bad mode",
                fixable=True,
                fix_cmd="sudo chmod 600 /opt/agmind/.env",
            ),
        )
    )
    monkeypatch.setattr(core_cmd, "_validate_config", lambda *a, **k: report)

    # tmp_path (not a literal /opt/agmind) — _validate_config is mocked, but keep the test hermetic
    # so it can never read a real host install dir (the lesson public-checks taught on config-validate).
    result = runner.invoke(_app(), ["doctor", "--live", "--json", "--install-dir", str(tmp_path)])
    payload = json.loads(result.stdout)
    names = [c["name"] for c in payload["checks"]]
    assert "env-file-mode" in names
    assert "kernel" in names
    # an error live finding → exit 1
    assert result.exit_code == 1


def test_doctor_live_ok_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)
    monkeypatch.setattr(
        core_cmd,
        "_validate_config",
        lambda *a, **k: ConfigValidationReport(findings=()),
    )
    result = runner.invoke(_app(), ["doctor", "--live", "--json"])
    assert result.exit_code == 0


def test_doctor_fix_runs_only_perm_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """--fix implies --live, applies perm fixes, re-evaluates → exit 0 after fix."""
    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)

    bad = ConfigValidationReport(
        findings=(
            ConfigFinding(
                id="env-file-mode",
                severity="error",
                message="bad mode",
                fixable=True,
                fix_cmd="sudo chmod 600 /opt/agmind/.env",
            ),
            ConfigFinding(
                id="drift-digest-mismatch",
                severity="error",
                message="drift",
                fixable=True,
                fix_cmd="agmind deploy --apply",
            ),
        )
    )
    # second validate (re-eval) returns only the unfixable drift
    after = ConfigValidationReport(
        findings=(
            ConfigFinding(
                id="drift-digest-mismatch",
                severity="error",
                message="drift",
                fixable=True,
                fix_cmd="agmind deploy --apply",
            ),
        )
    )
    seq = iter([bad, after])
    monkeypatch.setattr(core_cmd, "_validate_config", lambda *a, **k: next(seq))

    invoked: list[list[str]] = []

    class _CP:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **k):  # type: ignore[no-untyped-def]
        invoked.append(list(cmd))
        return _CP()

    monkeypatch.setattr("agmind.diagnostics.live.subprocess.run", _fake_run)

    result = runner.invoke(_app(), ["doctor", "--fix", "--json"])
    # exactly one perm-class subprocess (chmod); deploy never invoked
    assert len(invoked) == 1
    assert "chmod" in invoked[0]
    for c in invoked:
        assert "deploy" not in " ".join(c)
    # drift remains after fix → exit 1; output mentions manual deploy fix
    assert result.exit_code == 1
    assert "agmind deploy --apply" in result.stdout


def test_doctor_bundle_creates_tar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_cmd, "_run_preflight", _ok_preflight)
    install = tmp_path / "opt-agmind"
    install.mkdir()
    (install / ".env").write_text("SECRET=abc\n", encoding="utf-8")

    out = tmp_path / "support.tar.gz"

    def _fake_bundle(output_path, **k):  # type: ignore[no-untyped-def]
        with tarfile.open(output_path, "w:gz") as tar:
            import io

            data = b"{}"
            info = tarfile.TarInfo("agmind-bundle.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        from agmind.diagnostics.live import BundleResult

        return BundleResult(output_path=Path(output_path), bytes_written=10, issues=[])

    monkeypatch.setattr("agmind.diagnostics.live.create_support_bundle", _fake_bundle)

    result = runner.invoke(
        _app(),
        ["doctor", "--bundle", str(out), "--install-dir", str(install)],
    )
    assert result.exit_code == 0
    assert out.exists()
