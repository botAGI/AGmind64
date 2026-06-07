from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from agmind.config import validation
from agmind.config.validation import (
    ConfigFinding,
    ConfigValidationReport,
    _uid_can_read,
    validate_config,
)

pytestmark = pytest.mark.backend_any


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


def _write_env(install_dir: Path, lines: dict[str, str], *, mode: int = 0o600) -> Path:
    env = install_dir / ".env"
    env.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n", encoding="utf-8")
    os.chmod(env, mode)
    return env


def _write_compose(install_dir: Path, services: dict[str, object]) -> Path:
    compose = install_dir / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    return compose


def _stage_postgres_secret(secrets_dir: Path) -> None:
    """Stage an owner-readable postgres_password so A8 finds a present secret.

    postgres has NO ``reader_uid`` requirement (it reads ``*_PASSWORD_FILE`` while
    still root), so a plain 0600 file owned by the test user satisfies the A8 stat
    (``_check_secret_files`` returns early on ``reader_uid is None``). No chown is
    needed — which is what keeps this hermetic on a non-root CI runner.
    """
    secret = secrets_dir / "postgres_password"
    secret.write_text("s3cret-value", encoding="utf-8")
    os.chmod(secret, 0o600)


@pytest.fixture
def good_install(tmp_path: Path, _hermetic_secrets_dir: Path) -> Path:
    """A clean install dir: 0600 .env with the required var, valid compose.

    Selects postgres and stages its secret in the hermetic tmp secrets dir so the
    A8 secret-file check passes deterministically on any host (never reads the
    live ``/var/lib/agmind``).
    """
    install = tmp_path / "opt-agmind"
    install.mkdir()
    _write_env(install, {"POSTGRES_PASSWORD": "s3cret-value"}, mode=0o600)
    _write_compose(
        install,
        {
            "postgres": {
                "image": "postgres:17",
                "environment": ["POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?required}"],
            }
        },
    )
    _stage_postgres_secret(_hermetic_secrets_dir)
    return install


def _ids(report: ConfigValidationReport) -> set[str]:
    return {f.id for f in report.findings}


# --------------------------------------------------------------------------- #
# pure predicate: _uid_can_read
# --------------------------------------------------------------------------- #


def test_uid_can_read_root_always_true() -> None:
    assert _uid_can_read(0o600, file_uid=0, file_gid=0, reader_uid=0) is True


def test_uid_can_read_root_owned_0600_blocks_nonroot_reader() -> None:
    # non-root-reader class: file root:root 0600, reader uid 999 → cannot read.
    assert _uid_can_read(0o600, file_uid=0, file_gid=0, reader_uid=999) is False


def test_uid_can_read_owner_match_0600() -> None:
    assert _uid_can_read(0o600, file_uid=999, file_gid=999, reader_uid=999) is True


def test_uid_can_read_group_match() -> None:
    assert _uid_can_read(0o040, file_uid=0, file_gid=999, reader_uid=999) is True


def test_uid_can_read_world_readable() -> None:
    assert _uid_can_read(0o604, file_uid=0, file_gid=0, reader_uid=999) is True


# --------------------------------------------------------------------------- #
# report data model
# --------------------------------------------------------------------------- #


def test_report_ok_and_payload() -> None:
    report = ConfigValidationReport(
        findings=(
            ConfigFinding(id="x-warn", severity="warning", message="w"),
            ConfigFinding(id="x-info", severity="info", message="i"),
        )
    )
    assert report.ok is True  # no errors
    payload = report.to_payload()
    assert payload["ok"] is True
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 1
    assert payload["info_count"] == 1
    assert len(payload["findings"]) == 2


def test_report_strict_flips_on_warning() -> None:
    report = ConfigValidationReport(
        findings=(ConfigFinding(id="x-warn", severity="warning", message="w"),),
        strict=True,
    )
    assert report.ok is False


def test_report_error_flips_ok() -> None:
    report = ConfigValidationReport(
        findings=(ConfigFinding(id="x-err", severity="error", message="e"),)
    )
    assert report.ok is False
    assert report.to_payload()["ok"] is False


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_good_install_no_errors(good_install: Path) -> None:
    report = validate_config(good_install, check_drift=False)
    assert report.ok is True
    assert not report.by_severity("error"), _ids(report)


# --------------------------------------------------------------------------- #
# preamble: missing artifacts
# --------------------------------------------------------------------------- #


def test_missing_env_file(tmp_path: Path) -> None:
    install = tmp_path / "empty"
    install.mkdir()
    _write_compose(install, {"postgres": {"image": "postgres:17"}})
    report = validate_config(install, check_drift=False)
    assert "env-file-missing" in _ids(report)
    assert report.ok is False


def test_missing_compose(tmp_path: Path) -> None:
    install = tmp_path / "no-compose"
    install.mkdir()
    _write_env(install, {"POSTGRES_PASSWORD": "x"}, mode=0o600)
    report = validate_config(install, check_drift=False)
    assert "compose-missing" in _ids(report)
    # still does raw .env checks (no traceback)
    assert report.ok is False


def test_nonexistent_install_dir_graceful() -> None:
    report = validate_config(Path("/tmp/agmind-does-not-exist-xyz"), check_drift=False)
    assert "env-file-missing" in _ids(report)
    assert "compose-missing" in _ids(report)


# --------------------------------------------------------------------------- #
# (A) .env health
# --------------------------------------------------------------------------- #


def test_env_file_mode_not_0600(good_install: Path) -> None:
    os.chmod(good_install / ".env", 0o644)
    report = validate_config(good_install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "env-file-mode")
    assert finding.severity == "error"
    assert "0644" in finding.evidence
    assert finding.fixable and "chmod 600" in finding.fix_cmd


def test_required_var_missing(tmp_path: Path) -> None:
    install = tmp_path / "missing-req"
    install.mkdir()
    _write_env(install, {"OTHER": "x"}, mode=0o600)  # FOO omitted
    _write_compose(
        install,
        {"svc": {"image": "x", "command": "boot --token ${FOO:?must be set}"}},
    )
    report = validate_config(install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "env-required-var-missing")
    assert finding.severity == "error"
    assert "FOO" in finding.message
    assert "svc" in finding.evidence  # best-effort service attribution


def test_required_var_empty_value_is_missing(tmp_path: Path) -> None:
    install = tmp_path / "empty-req"
    install.mkdir()
    _write_env(install, {"FOO": ""}, mode=0o600)
    _write_compose(install, {"svc": {"image": "x", "command": "${FOO:?}"}})
    report = validate_config(install, check_drift=False)
    assert "env-required-var-missing" in _ids(report)


def test_required_var_in_yaml_comment_not_flagged(tmp_path: Path) -> None:
    # A2 scans the RE-SERIALIZED compose, so a ${VAR:?} inside a YAML COMMENT
    # must NOT false-positive, while a real string VALUE still must be flagged.
    install = tmp_path / "comment-vs-value"
    install.mkdir()
    _write_env(install, {"OTHER": "x"}, mode=0o600)  # neither required var present
    compose = install / "docker-compose.yml"
    compose.write_text(
        "# legacy ${OBSOLETE_VAR:?gone}\n"
        + yaml.safe_dump(
            {
                "services": {
                    "svc": {
                        "image": "x",
                        "command": ["sh", "-c", "echo ${REAL_REQUIRED:?}"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    report = validate_config(install, check_drift=False)
    missing = {f.message for f in report.findings if f.id == "env-required-var-missing"}
    assert any("REAL_REQUIRED" in m for m in missing)  # real command value flagged
    assert not any("OBSOLETE_VAR" in m for m in missing)  # comment NOT flagged


def test_weak_default_secret(tmp_path: Path) -> None:
    # A7: a selected descriptor whose secret env resolves (against .env) to a
    # weak/well-known default → error, never echoing the secret VALUE.
    # dify-api PLUGIN_DAEMON_KEY = ${DIFY_PLUGIN_DAEMON_KEY:-changeme-plugin-daemon-key}
    install = tmp_path / "weak-secret"
    install.mkdir()
    # DIFY_PLUGIN_DAEMON_KEY absent → resolves to its "changeme-*" default.
    _write_env(install, {"OTHER": "x"}, mode=0o600)
    _write_compose(install, {"dify-api": {"image": "dify"}})
    report = validate_config(install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "env-weak-default-secret")
    assert finding.severity == "error"
    assert "dify-api" in finding.message
    # Must NEVER echo the resolved weak value.
    assert "changeme" not in finding.evidence
    assert "changeme" not in finding.message


def test_env_file_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # THE root-owned-0600 live-crash class: parse_env_file raises PermissionError.
    install = tmp_path / "unreadable-env"
    install.mkdir()
    _write_env(install, {"POSTGRES_PASSWORD": "x"}, mode=0o600)
    _write_compose(install, {"postgres": {"image": "postgres:17"}})

    def _boom(_path: object) -> dict[str, str]:
        raise PermissionError("locked down")

    monkeypatch.setattr(validation, "parse_env_file", _boom)
    report = validate_config(install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "env-file-unreadable")
    assert finding.severity == "error"
    assert finding.fixable
    assert "sudo" in finding.fix_cmd
    assert report.ok is False


def test_compose_unreadable_is_compose_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The unreadable-compose branch also surfaces a compose-missing finding.
    install = tmp_path / "unreadable-compose"
    install.mkdir()
    _write_env(install, {"POSTGRES_PASSWORD": "x"}, mode=0o600)
    _write_compose(install, {"postgres": {"image": "postgres:17"}})

    def _boom(_path: object) -> dict[str, object]:
        raise PermissionError("locked down")

    monkeypatch.setattr(validation, "_load_compose", _boom)
    report = validate_config(install, check_drift=False)
    assert "compose-missing" in _ids(report)
    assert report.ok is False


def test_unresolved_placeholder_value(good_install: Path) -> None:
    _write_env(
        good_install,
        {"POSTGRES_PASSWORD": "ok", "BROKEN": "prefix-${STILL_UNRESOLVED}-suffix"},
        mode=0o600,
    )
    report = validate_config(good_install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "env-unresolved-placeholder")
    assert finding.severity == "error"
    assert finding.evidence == "BROKEN"  # the KEY, never the value
    # Message must not leak the secret VALUE (the var name / placeholder content).
    assert "STILL_UNRESOLVED" not in finding.message
    assert "prefix" not in finding.message


# --------------------------------------------------------------------------- #
# (A8) secret files — DB-server *_PASSWORD_FILE class
# --------------------------------------------------------------------------- #


def test_secret_file_missing(tmp_path: Path, _hermetic_secrets_dir: Path) -> None:
    # The autouse _hermetic_secrets_dir fixture gives an EMPTY tmp secrets dir, so
    # selecting a DB service WITHOUT staging its secret naturally fires
    # secret-file-missing (no reliance on the host /var/lib/agmind).
    install = tmp_path / "secfile"
    install.mkdir()
    _write_env(install, {"POSTGRES_PASSWORD": "x"}, mode=0o600)
    _write_compose(install, {"postgres": {"image": "postgres:17"}})
    report = validate_config(install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "secret-file-missing")
    assert finding.severity == "error"


def test_secret_file_unreadable_by_reader_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    # The catalog currently has no DB server that drops to a non-root uid before reading its
    # *_FILE, so DB_SECRET_FILE_READER_UID is empty. Inject a synthetic non-root reader entry so
    # the still-live secret-file-unreadable detection path stays covered (the mechanism is generic).
    monkeypatch.setattr(
        validation, "DB_SECRET_FILES", (("nonrootdb", "nonrootdb_password", "NONROOTDB_PASSWORD"),)
    )
    monkeypatch.setattr(validation, "DB_SECRET_FILE_READER_UID", {"nonrootdb_password": 999})

    install = tmp_path / "secfile2"
    install.mkdir()
    secret = _hermetic_secrets_dir / "nonrootdb_password"
    secret.write_text("pw", encoding="utf-8")
    os.chmod(secret, 0o600)
    _write_env(install, {"NONROOTDB_PASSWORD": "x"}, mode=0o600)
    _write_compose(install, {"nonrootdb": {"image": "mongo:8"}})

    # Simulate the root:root 0600 live case: stat reports owner uid 0.
    real_stat = os.stat

    def fake_stat(path: object, *a: object, **k: object) -> os.stat_result:
        st = real_stat(path)  # type: ignore[arg-type]
        if str(path) == str(secret):
            fields = list(st)
            fields[stat.ST_UID] = 0
            fields[stat.ST_GID] = 0
            fields[stat.ST_MODE] = (st.st_mode & ~0o777) | 0o600
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(validation.os, "stat", fake_stat)
    report = validate_config(install, check_drift=False)
    finding = next(f for f in report.findings if f.id == "secret-file-unreadable")
    assert finding.severity == "error"
    assert "999" in finding.fix_cmd  # chown to the non-root reader uid
    assert finding.fixable


def test_secret_file_not_checked_when_service_unselected(
    tmp_path: Path, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "secfile3"
    install.mkdir()
    _write_env(install, {"FOO": "x"}, mode=0o600)
    # grafana is NOT a DB_SECRET_FILES service → no secret-file finding at all.
    _write_compose(install, {"grafana": {"image": "grafana/grafana:11"}})
    report = validate_config(install, check_drift=False)
    assert "secret-file-missing" not in _ids(report)
    assert "secret-file-unreadable" not in _ids(report)


# --------------------------------------------------------------------------- #
# (B) drift — monkeypatch the docker-facing helpers
# --------------------------------------------------------------------------- #


def _compose_with_postgres(install: Path, secrets_dir: Path) -> None:
    _write_env(install, {"POSTGRES_PASSWORD": "x"}, mode=0o600)
    _write_compose(
        install,
        {
            "postgres": {
                "image": "postgres:17",
                "environment": ["POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?req}"],
            }
        },
    )
    # Stage the postgres secret in the hermetic tmp dir so the A8 check is clean
    # (no spurious secret-file-missing) on a fresh CI runner.
    _stage_postgres_secret(secrets_dir)


def test_drift_skipped_when_docker_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "drift-skip"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)
    monkeypatch.setattr(
        validation, "_running_image_digests", lambda selected, install_dir=None: None
    )
    monkeypatch.setattr(validation, "_running_agmind_containers", list)
    report = validate_config(install, check_drift=True)
    assert "drift-skipped" in _ids(report)
    # drift-skipped is info, never an error
    assert not report.by_severity("error")


def test_drift_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "drift-down"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)
    monkeypatch.setattr(
        validation,
        "_running_image_digests",
        lambda selected, install_dir=None: {"postgres": validation._NOT_RUNNING},
    )
    monkeypatch.setattr(validation, "_running_agmind_containers", list)
    report = validate_config(install, check_drift=True)
    finding = next(f for f in report.findings if f.id == "drift-not-running")
    assert finding.severity == "warning"
    # strict promotes the report failure
    strict_report = validate_config(install, check_drift=True, strict=True)
    assert strict_report.ok is False


def test_drift_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "drift-mismatch"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)

    # Real postgres descriptor digest vs a bogus running digest.
    from agmind.services.renderer import load_descriptors

    pinned = (load_descriptors()["postgres"].digest or "").lower()
    assert pinned  # sanity: postgres is digest-pinned
    monkeypatch.setattr(
        validation,
        "_running_image_digests",
        lambda selected, install_dir=None: {"postgres": "deadbeef" * 8},
    )
    monkeypatch.setattr(validation, "_running_agmind_containers", list)
    report = validate_config(install, check_drift=True)
    finding = next(f for f in report.findings if f.id == "drift-digest-mismatch")
    assert finding.severity == "error"
    assert finding.evidence.startswith("pinned ")
    assert "agmind deploy --apply" in finding.fix_cmd


def test_drift_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "drift-orphan"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)
    monkeypatch.setattr(
        validation,
        "_running_image_digests",
        lambda selected, install_dir=None: {"postgres": validation._NOT_RUNNING},
    )
    monkeypatch.setattr(
        validation, "_running_agmind_containers", lambda: ["agmind-postgres", "agmind-ghost"]
    )
    report = validate_config(install, check_drift=True)
    orphan = next(f for f in report.findings if f.id == "drift-orphan")
    assert orphan.severity == "warning"
    assert "agmind-ghost" in orphan.evidence


def test_drift_undeterminable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "drift-undet"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)
    monkeypatch.setattr(
        validation, "_running_image_digests", lambda selected, install_dir=None: {"postgres": ""}
    )
    monkeypatch.setattr(validation, "_running_agmind_containers", list)
    report = validate_config(install, check_drift=True)
    assert "drift-digest-undeterminable" in _ids(report)


def test_no_drift_findings_when_check_drift_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _hermetic_secrets_dir: Path
) -> None:
    install = tmp_path / "no-drift"
    install.mkdir()
    _compose_with_postgres(install, _hermetic_secrets_dir)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("docker helper must not be called when check_drift=False")

    monkeypatch.setattr(validation, "_running_image_digests", _boom)
    monkeypatch.setattr(validation, "_running_agmind_containers", _boom)
    report = validate_config(install, check_drift=False)
    drift_ids = {f.id for f in report.findings if f.id.startswith("drift-")}
    assert drift_ids == set()


def test_running_container_with_missing_repodigests_is_not_marked_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """live-audit 2026-06-08 UX-2: a running container whose `docker inspect` template ERRORS
    (no top-level RepoDigests key → rc≠0) must NOT be reported as "not running". Running-ness is
    decided from `docker ps`; the failed digest only makes the entry undeterminable ("")."""
    import subprocess

    monkeypatch.setattr(validation.shutil, "which", lambda _name: "/usr/bin/docker")  # type: ignore[attr-defined]
    monkeypatch.setattr(validation, "_running_service_names", lambda _d: {"authelia"})

    def _fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        # Reproduce the live failure: template error → rc=1, error text on stderr.
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr='template: :1:8: executing "" at <.RepoDigests>: map has no entry',
        )

    monkeypatch.setattr(validation.subprocess, "run", _fake_run)
    digests = validation._running_image_digests({"authelia"}, Path("/opt/agmind"))
    assert digests == {"authelia": ""}  # running-but-undeterminable, NOT _NOT_RUNNING


def test_drift_uses_docker_ps_for_running_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service absent from `docker ps` maps to _NOT_RUNNING even if inspect would succeed."""
    import subprocess

    monkeypatch.setattr(validation.shutil, "which", lambda _name: "/usr/bin/docker")  # type: ignore[attr-defined]
    monkeypatch.setattr(validation, "_running_service_names", lambda _d: set())  # nothing running

    def _never(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("inspect must be skipped for a not-running service")

    monkeypatch.setattr(validation.subprocess, "run", _never)
    digests = validation._running_image_digests({"postgres"}, Path("/opt/agmind"))
    assert digests == {"postgres": validation._NOT_RUNNING}
