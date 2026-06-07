from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from agmind.cli import config_cmd

pytestmark = pytest.mark.backend_any


@pytest.fixture(autouse=True)
def _hermetic_secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_DEFAULT_SECRETS_DIR`` at a per-test tmp dir (host-state isolation).

    A ``tests/config/conftest.py`` cannot reach this ``tests/cli/`` module, so the
    same autouse fixture is declared here. Without it the A8 secret-file check
    falls back to the live host ``/var/lib/agmind/secrets`` — present on a dev box,
    absent on a clean CI runner — and emits a spurious ``secret-file-missing``
    error that breaks the happy-path exit-0 assertions.
    """
    secrets = tmp_path / "agmind-secrets"
    secrets.mkdir()
    monkeypatch.setattr("agmind.config.validation._DEFAULT_SECRETS_DIR", secrets)
    return secrets


def _good_install(tmp_path: Path, secrets_dir: Path) -> Path:
    install = tmp_path / "opt-agmind"
    install.mkdir()
    env = install / ".env"
    env.write_text("POSTGRES_PASSWORD=s3cret\n", encoding="utf-8")
    os.chmod(env, 0o600)
    compose = install / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "postgres": {
                        "image": "postgres:17",
                        "environment": ["POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?req}"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # Stage the postgres secret in the hermetic tmp dir. postgres has no reader_uid
    # requirement, so an owner-readable 0600 file owned by the test user satisfies
    # the A8 stat without any chown (keeps this green on a non-root CI runner).
    secret = secrets_dir / "postgres_password"
    secret.write_text("s3cret", encoding="utf-8")
    os.chmod(secret, 0o600)
    return install


def _app() -> typer.Typer:
    app = typer.Typer()
    config_cmd.register(app)
    return app


# --------------------------------------------------------------------------- #
# thin cmd_validate (no typer)
# --------------------------------------------------------------------------- #


def test_cmd_validate_json_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], _hermetic_secrets_dir: Path
) -> None:
    rc = config_cmd.cmd_validate(
        install_dir=_good_install(tmp_path, _hermetic_secrets_dir),
        as_json=True,
        check_drift=False,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["error_count"] == 0


def test_cmd_validate_json_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install = tmp_path / "broken"
    install.mkdir()  # no .env, no compose
    rc = config_cmd.cmd_validate(install_dir=install, as_json=True, check_drift=False)
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    ids = {f["id"] for f in payload["findings"]}
    assert "env-file-missing" in ids
    assert "compose-missing" in ids


def test_cmd_validate_human_failure_goes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = tmp_path / "broken2"
    install.mkdir()
    rc = config_cmd.cmd_validate(install_dir=install, as_json=False, check_drift=False)
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # failures must surface even with stdout redirected
    assert "FAILED" in captured.err


def test_cmd_validate_never_exits_two(tmp_path: Path) -> None:
    install = tmp_path / "x"
    install.mkdir()
    rc = config_cmd.cmd_validate(install_dir=install, check_drift=False)
    assert rc in (0, 1)


# --------------------------------------------------------------------------- #
# typer wiring via CliRunner — assert BEHAVIOUR via --json, never help text
# --------------------------------------------------------------------------- #


def test_cli_config_validate_json(tmp_path: Path, _hermetic_secrets_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        _app(),
        [
            "config",
            "validate",
            "--json",
            "--install-dir",
            str(_good_install(tmp_path, _hermetic_secrets_dir)),
            "--no-drift",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_cli_config_validate_failure_exit_code(tmp_path: Path) -> None:
    install = tmp_path / "broken3"
    install.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        _app(),
        ["config", "validate", "--json", "--install-dir", str(install), "--no-drift"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "env-file-missing" in {f["id"] for f in payload["findings"]}


def test_cli_config_validate_strict_flips_on_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    # A config whose ONLY finding is a warning (drift-not-running): non-strict
    # passes (exit 0 / ok True), --strict fails (exit 1 / ok False).
    from agmind.config import validation

    # _good_install stages the postgres secret in the hermetic tmp dir, so the
    # ONLY finding is the drift warning (deterministic across CI hosts).
    install = _good_install(tmp_path, _hermetic_secrets_dir)
    monkeypatch.setattr(
        validation,
        "_running_image_digests",
        lambda selected, install_dir=None: {"postgres": validation._NOT_RUNNING},
    )
    monkeypatch.setattr(validation, "_running_agmind_containers", list)
    runner = CliRunner()

    lenient = runner.invoke(
        _app(),
        ["config", "validate", "--json", "--install-dir", str(install), "--drift"],
    )
    assert lenient.exit_code == 0
    lenient_payload = json.loads(lenient.stdout)
    assert lenient_payload["ok"] is True
    assert lenient_payload["warning_count"] >= 1

    strict = runner.invoke(
        _app(),
        ["config", "validate", "--json", "--install-dir", str(install), "--drift", "--strict"],
    )
    assert strict.exit_code == 1
    strict_payload = json.loads(strict.stdout)
    assert strict_payload["ok"] is False


def test_cli_config_validate_registered_on_main_app() -> None:
    # config validate must be reachable from the real agmind app (wired in __init__).
    from agmind.cli import _make_app

    runner = CliRunner()
    result = runner.invoke(_make_app(), ["config", "validate", "--json", "--no-drift"])
    # /opt/agmind default dir likely absent in CI → ok False but a clean JSON exit 1, no traceback.
    assert result.exit_code in (0, 1)
    payload = json.loads(result.stdout)
    assert "ok" in payload


# --------------------------------------------------------------------------- #
# _format_report — header wording (UX-4) + collapse of homogeneous findings (M2)
# --------------------------------------------------------------------------- #


def _report(*findings: object) -> object:
    from agmind.config.validation import ConfigValidationReport

    return ConfigValidationReport(findings=tuple(findings))  # type: ignore[arg-type]


def _finding(**kw: object) -> object:
    from agmind.config.validation import ConfigFinding

    base: dict[str, object] = {"id": "x", "severity": "warning", "message": "m"}
    base.update(kw)
    return ConfigFinding(**base)  # type: ignore[arg-type]


def test_format_report_does_not_say_ok_when_warnings_present() -> None:
    """live-audit 2026-06-08 UX-4: a warning wall must NOT be headed by an unqualified 'OK'."""
    rep = _report(_finding(id="drift-orphan", severity="warning", message="m", evidence="agmind-x"))
    rendered = config_cmd._format_report(rep)  # type: ignore[arg-type]
    first_line = rendered.splitlines()[0]
    assert "OK" not in first_line
    assert "warning" in first_line.lower()


def test_format_report_collapses_repeated_not_running() -> None:
    """M2: 34 identical drift-not-running findings collapse into ONE summarized line."""
    findings = [
        _finding(
            id="drift-not-running",
            severity="warning",
            message=f"service s{i} ...",
            evidence=f"agmind-s{i}",
            fixable=True,
            fix_cmd="agmind deploy --apply",
        )
        for i in range(34)
    ]
    rendered = config_cmd._format_report(_report(*findings))  # type: ignore[arg-type]
    assert rendered.count("drift-not-running") == 1  # collapsed, not 34 lines
    assert "34 services affected" in rendered
    assert "agmind deploy --apply" in rendered
