"""Tests for agmind.diagnostics.live — the doctor overhaul pure logic.

Covers:
- _is_safe_auto_fix allow-list (perm-class only; deploy/install rejected)
- finding_to_check folds a ConfigFinding into a CheckResult
- merge_live_findings folds + re-sorts a DoctorReport with live findings
- apply_safe_fixes runs ONLY perm-class fix_cmds (mocked subprocess) and never
  invokes deploy/install/gc
- create_support_bundle redacts .env secret VALUES and never embeds raw secrets
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from agmind.config.validation import ConfigFinding
from agmind.diagnostics import live
from agmind.diagnostics.doctor import CheckResult, DoctorReport

pytestmark = pytest.mark.backend_any


# --------------------------------------------------------------------------- #
# _is_safe_auto_fix allow-list
# --------------------------------------------------------------------------- #


def test_safe_fix_accepts_env_file_mode_chmod() -> None:
    f = ConfigFinding(
        id="env-file-mode",
        severity="error",
        message="x",
        fixable=True,
        fix_cmd="sudo chmod 600 /opt/agmind/.env",
    )
    assert live._is_safe_auto_fix(f) is True


def test_safe_fix_accepts_secret_unreadable_chown() -> None:
    f = ConfigFinding(
        id="secret-file-unreadable",
        severity="error",
        message="x",
        fixable=True,
        fix_cmd="sudo chown 999:999 /var/lib/agmind/secrets/postgres_password",
    )
    assert live._is_safe_auto_fix(f) is True


def test_safe_fix_rejects_deploy() -> None:
    f = ConfigFinding(
        id="drift-digest-mismatch",
        severity="error",
        message="x",
        fixable=True,
        fix_cmd="agmind deploy --apply",
    )
    assert live._is_safe_auto_fix(f) is False


def test_safe_fix_rejects_install() -> None:
    f = ConfigFinding(
        id="secret-file-missing",
        severity="error",
        message="x",
        fixable=True,
        fix_cmd="agmind install",
    )
    assert live._is_safe_auto_fix(f) is False


def test_safe_fix_rejects_gc() -> None:
    f = ConfigFinding(
        id="drift-orphan",
        severity="warning",
        message="x",
        fixable=True,
        fix_cmd="agmind gc",
    )
    assert live._is_safe_auto_fix(f) is False


def test_safe_fix_rejects_unknown_id_even_with_chmod_cmd() -> None:
    """The id must be in the allow-list AND the cmd perm-class — both gates."""
    f = ConfigFinding(
        id="some-other-thing",
        severity="error",
        message="x",
        fixable=True,
        fix_cmd="sudo chmod 600 /etc/passwd",
    )
    assert live._is_safe_auto_fix(f) is False


def test_safe_fix_rejects_not_fixable() -> None:
    f = ConfigFinding(
        id="env-file-mode",
        severity="error",
        message="x",
        fixable=False,
        fix_cmd="sudo chmod 600 /opt/agmind/.env",
    )
    assert live._is_safe_auto_fix(f) is False


# --------------------------------------------------------------------------- #
# finding_to_check / merge
# --------------------------------------------------------------------------- #


def test_finding_to_check_maps_severity_to_status() -> None:
    f = ConfigFinding(id="env-file-mode", severity="error", message="m", fix_cmd="sudo chmod 600 x")
    c = live.finding_to_check(f)
    assert c.name == "env-file-mode"
    assert c.status == "fail"  # error -> fail
    assert c.message == "m"
    assert c.fix_hint == "sudo chmod 600 x"

    w = ConfigFinding(id="drift-orphan", severity="warning", message="m")
    assert live.finding_to_check(w).status == "warn"

    i = ConfigFinding(id="drift-skipped", severity="info", message="m")
    assert live.finding_to_check(i).status == "skip"


def test_merge_live_findings_appends_and_resorts() -> None:
    base = DoctorReport(checks=[CheckResult("kernel", "ok", "fine")])
    findings = (
        ConfigFinding(id="drift-orphan", severity="warning", message="orphan"),
        ConfigFinding(id="env-file-mode", severity="error", message="bad mode"),
    )
    merged = live.merge_live_findings(base, findings)
    names = [c.name for c in merged.checks]
    # error sorts before warn before ok; preflight ok kept after.
    assert names.index("env-file-mode") < names.index("drift-orphan")
    assert "kernel" in names
    assert merged.has_failures is True
    assert merged.has_warnings is True


# --------------------------------------------------------------------------- #
# apply_safe_fixes — ONLY perm-class subprocess invocations
# --------------------------------------------------------------------------- #


def test_apply_safe_fixes_runs_only_perm_class(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[list[str]] = []

    class _CP:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        invoked.append(list(cmd))
        return _CP()

    monkeypatch.setattr("agmind.diagnostics.live.subprocess.run", _fake_run)

    findings = (
        ConfigFinding(
            id="env-file-mode",
            severity="error",
            message="bad mode",
            fixable=True,
            fix_cmd="sudo chmod 600 /opt/agmind/.env",
        ),
        ConfigFinding(
            id="secret-file-unreadable",
            severity="error",
            message="locked",
            fixable=True,
            fix_cmd="sudo chown 999:999 /var/lib/agmind/secrets/pg",
        ),
        ConfigFinding(
            id="drift-digest-mismatch",
            severity="error",
            message="drift",
            fixable=True,
            fix_cmd="agmind deploy --apply",
        ),
        ConfigFinding(
            id="secret-file-missing",
            severity="error",
            message="gone",
            fixable=True,
            fix_cmd="agmind install",
        ),
    )

    result = live.apply_safe_fixes(findings, sudo_password=None)

    # exactly the two perm-class commands ran
    assert len(invoked) == 2
    flat = [" ".join(c) for c in invoked]
    assert any("chmod" in c for c in flat)
    assert any("chown" in c for c in flat)
    # deploy / install / gc never invoked as subprocess
    for c in flat:
        assert "deploy" not in c
        assert "install" not in c
        assert " gc" not in c

    assert {r.finding.id for r in result.fixed} == {"env-file-mode", "secret-file-unreadable"}
    assert {f.id for f in result.unfixable} == {"drift-digest-mismatch", "secret-file-missing"}


def test_apply_safe_fixes_sudo_password_wraps_command(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[list[str]] = []

    class _CP:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        invoked.append(list(cmd))
        return _CP()

    monkeypatch.setattr("agmind.diagnostics.live.subprocess.run", _fake_run)

    findings = (
        ConfigFinding(
            id="env-file-mode",
            severity="error",
            message="bad mode",
            fixable=True,
            fix_cmd="sudo chmod 600 /opt/agmind/.env",
        ),
    )
    live.apply_safe_fixes(findings, sudo_password="hunter2")
    # the original "sudo" token is dropped and re-wrapped as `sudo -S -p "" -- ...`
    assert invoked[0][:4] == ["sudo", "-S", "-p", ""]
    assert "chmod" in invoked[0]


def test_apply_safe_fixes_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _CP:
        returncode = 1
        stderr = "permission denied"

    monkeypatch.setattr("agmind.diagnostics.live.subprocess.run", lambda *a, **k: _CP())

    findings = (
        ConfigFinding(
            id="env-file-mode",
            severity="error",
            message="bad mode",
            fixable=True,
            fix_cmd="sudo chmod 600 /opt/agmind/.env",
        ),
    )
    result = live.apply_safe_fixes(findings, sudo_password=None)
    assert result.fixed == []
    assert len(result.failed) == 1
    assert "permission denied" in result.failed[0].detail


# --------------------------------------------------------------------------- #
# create_support_bundle — redaction
# --------------------------------------------------------------------------- #


def _members(tar_path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(tar_path, "r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                fh = tar.extractfile(m)
                out[m.name] = fh.read() if fh else b""
    return out


def test_bundle_redacts_env_secret_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = tmp_path / "opt-agmind"
    install.mkdir()
    secret = "TOPSECRET_VALUE_123"
    (install / ".env").write_text(
        f"POSTGRES_PASSWORD={secret}\n# comment\nAGMIND_DOMAIN=example.com\n",
        encoding="utf-8",
    )
    (install / "docker-compose.yml").write_text(
        "services:\n  postgres:\n    image: postgres:17\n", encoding="utf-8"
    )

    # avoid touching real docker / host validation
    monkeypatch.setattr(live, "_collect_docker_ps", lambda *a, **k: b"[]")
    monkeypatch.setattr(live, "_collect_docker_logs", lambda *a, **k: b"")

    out = tmp_path / "bundle.tar.gz"
    result = live.create_support_bundle(out, install_dir=install, include_logs=False)
    assert result.output_path == out
    assert out.exists()

    members = _members(out)
    # a redacted .env member must exist, with NO secret value
    assert "env_redacted.txt" in members
    env_text = members["env_redacted.txt"].decode("utf-8")
    assert secret not in env_text
    assert "POSTGRES_PASSWORD=***" in env_text
    assert "AGMIND_DOMAIN=***" in env_text
    # comments preserved
    assert "# comment" in env_text

    # the raw .env file name must NOT be present anywhere as a member
    assert ".env" not in members
    assert "env" not in members

    # secret VALUE must not appear in ANY bundle member
    for name, data in members.items():
        assert secret.encode() not in data, f"secret leaked into {name}"


def test_bundle_output_must_not_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = tmp_path / "opt-agmind"
    install.mkdir()
    (install / ".env").write_text("X=1\n", encoding="utf-8")
    out = tmp_path / "exists.tar.gz"
    out.write_text("already here", encoding="utf-8")
    with pytest.raises(FileExistsError):
        live.create_support_bundle(out, install_dir=install, include_logs=False)


def test_bundle_succeeds_without_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = tmp_path / "opt-agmind"
    install.mkdir()
    (install / ".env").write_text("A=b\n", encoding="utf-8")
    monkeypatch.setattr(live, "_collect_docker_ps", lambda *a, **k: b"[]")
    monkeypatch.setattr(live, "_collect_docker_logs", lambda *a, **k: b"")
    out = tmp_path / "b.tar.gz"
    result = live.create_support_bundle(out, install_dir=install, include_logs=False)
    members = _members(out)
    assert "agmind-bundle.json" in members
    # metadata records the missing compose as an issue but the bundle still builds
    assert out.exists()
    assert any("compose" in issue for issue in result.issues)
