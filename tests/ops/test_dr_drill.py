"""agmind ops dr-drill — backup → integrity → sandbox-restore → (live) orchestrator.

The orchestrator is pure: backup/verify/restore (and the GPU-gated live restore +
health) are injected, so steps 1-4 are fully unit-tested offline with fakes. RTO
is measured via an injected clock for determinism. The live restore is skipped by
default and only runs with --no-skip-restore on a real host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.ops.dr_drill import DrillReport, run_drill

pytestmark = pytest.mark.backend_any


def _clock():
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    return lambda: next(ticks)


def test_happy_path_runs_backup_integrity_restore() -> None:
    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: [],
        restore_fn=lambda p: ["compose", "env"],
        clock=_clock(),
    )
    assert isinstance(report, DrillReport)
    assert report.ok is True
    names = [s.name for s in report.steps]
    assert names == ["backup", "integrity", "sandbox-restore"]
    assert all(s.ok for s in report.steps)
    assert report.rto_seconds > 0


def test_backup_failure_aborts_early() -> None:
    def boom() -> Path:
        raise OSError("disk full")

    report = run_drill(
        backup_fn=boom,
        verify_fn=lambda p: [],
        restore_fn=lambda p: [],
        clock=_clock(),
    )
    assert report.ok is False
    assert [s.name for s in report.steps] == ["backup"]
    assert "disk full" in report.steps[0].detail


def test_integrity_failure_aborts_before_restore() -> None:
    calls = {"restore": 0}

    def restore(_p: Path):
        calls["restore"] += 1
        return []

    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: ["env: sha256 mismatch (corrupt)"],
        restore_fn=restore,
        clock=_clock(),
    )
    assert report.ok is False
    assert [s.name for s in report.steps] == ["backup", "integrity"]
    assert calls["restore"] == 0, "must not restore from a corrupt backup"


def test_sandbox_restore_failure_marks_report_failed() -> None:
    def restore(_p: Path):
        raise ValueError("unsafe member")

    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: [],
        restore_fn=restore,
        clock=_clock(),
    )
    assert report.ok is False
    assert report.steps[-1].name == "sandbox-restore"
    assert report.steps[-1].ok is False


def test_live_restore_skipped_by_default() -> None:
    live_called = {"n": 0}

    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: [],
        restore_fn=lambda p: ["compose"],
        live_restore_fn=lambda: live_called.__setitem__("n", live_called["n"] + 1) or True,
        clock=_clock(),
    )
    assert "live-restore" not in [s.name for s in report.steps]
    assert live_called["n"] == 0


def test_live_restore_runs_with_skip_restore_false() -> None:
    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: [],
        restore_fn=lambda p: ["compose"],
        live_restore_fn=lambda: True,
        health_fn=lambda: True,
        skip_restore=False,
        clock=_clock(),
    )
    names = [s.name for s in report.steps]
    assert "live-restore" in names and "health" in names
    assert report.ok is True


def test_report_payload_shape() -> None:
    report = run_drill(
        backup_fn=lambda: Path("/tmp/b.tar.gz"),
        verify_fn=lambda p: [],
        restore_fn=lambda p: ["compose"],
        clock=_clock(),
    )
    payload = report.to_payload()
    assert set(payload) >= {"ok", "rto_seconds", "steps"}
    assert payload["steps"][0]["name"] == "backup"


# ---- CLI end-to-end (offline: real backup→verify→sandbox-restore, no docker) ----


def test_dr_drill_cli_offline_roundtrip(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    install = tmp_path / "opt"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install / ".env").write_text("AGMIND_DOMAIN=lab.example.com\n", encoding="utf-8")

    result = CliRunner().invoke(
        _make_app(),
        [
            "ops",
            "dr-drill",
            "--install-dir",
            str(install),
            "--user-dir",
            str(tmp_path / "user"),
            "--system-dir",
            str(tmp_path / "system"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "backup" in result.output.lower()
    assert "rto" in result.output.lower()
    # the drill must not have mutated the real install
    assert (install / ".env").read_text(encoding="utf-8") == "AGMIND_DOMAIN=lab.example.com\n"


def test_dr_drill_include_data_backs_up_data_but_restore_excludes_dbdump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """live-audit MED dr-drill-skips-data-tier: --include-data must back up + integrity-verify the
    data tier, but the SANDBOX restore must EXCLUDE dbdump/* — a dbdump restore execs into the LIVE
    db container, not the sandbox, so exec'ing it in a drill would overwrite live data."""
    from agmind.cli import ops_cmd
    from agmind.ops.backup import BackupResult, RestoreResult

    install = tmp_path / "opt"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install / ".env").write_text("X=1\n", encoding="utf-8")

    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _i: ["postgres", "qdrant"])
    captured: dict[str, object] = {}

    def fake_create_backup(*, output_path, sources, data_sources=None, sudo_password=None, **kw):  # type: ignore[no-untyped-def]
        captured["data_sources"] = data_sources
        Path(output_path).write_bytes(b"archive")
        return BackupResult(Path(output_path), 7, (), ())

    def fake_read_metadata(_p):  # type: ignore[no-untyped-def]
        return {
            "included": ["compose", "env"],
            "data": [
                {"label": "dbdump/postgres", "kind": "dbdump"},
                {"label": "dbdump/postgres-globals", "kind": "dbdump"},
                {"label": "volume/qdrant", "kind": "dir"},
            ],
        }

    def fake_restore_backup(*, backup_path, sources, labels=None, **kw):  # type: ignore[no-untyped-def]
        captured["restore_labels"] = labels
        return RestoreResult(extracted=tuple(labels or ()), metadata={}, failed=())

    monkeypatch.setattr("agmind.ops.backup.create_backup", fake_create_backup)
    monkeypatch.setattr("agmind.ops.backup.verify_backup", lambda _p: [])
    monkeypatch.setattr("agmind.ops.backup.restore_backup", fake_restore_backup)
    monkeypatch.setattr(ops_cmd, "read_metadata", fake_read_metadata)

    rc = ops_cmd.cmd_dr_drill(
        install, user_dir=tmp_path / "u", system_dir=tmp_path / "s", include_data=True
    )
    assert rc == 0
    assert captured["data_sources"] is not None  # data tier was backed up
    rl = captured["restore_labels"]
    assert rl is not None
    assert "volume/qdrant" in rl and "compose" in rl  # config + volume restored into sandbox
    assert not any(
        str(label).startswith("dbdump/") for label in rl
    )  # dbdump excluded (live-exec danger)
