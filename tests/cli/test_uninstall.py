"""`agmind uninstall` — tear down the stack for a clean reinstall. live-deploy 2026-06-07:
the in-place reconcile left a wedged stack (a dependency-failed deploy step), so the operator
needs a full-clean path. Default keeps data; --data wipes everything."""

from __future__ import annotations

import pytest

from agmind.cli import install_cmd

pytestmark = pytest.mark.backend_any


def _capture(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(install_cmd, "_run_cmd", lambda cmd: calls.append(cmd) or 0)
    # hermetic: never touch real docker for the orphan-sweep id lookups (clean-runner lesson)
    monkeypatch.setattr(install_cmd, "_docker_ids", lambda _cmd: [])
    return calls


def test_uninstall_default_downs_and_removes_dir_and_shim_but_keeps_data(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)

    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("compose" in f and "down" in f and "--remove-orphans" in f for f in flat)
    assert not any("--volumes" in f for f in flat)  # default keeps named volumes
    assert any(f"rm -rf {tmp_path}" in f for f in flat)  # install dir gone
    assert any("rm -rf /usr/local/bin/agmind" in f for f in flat)  # global shim gone
    assert not any("/var/lib/agmind" in f for f in flat)  # DATA kept
    assert not any("/etc/agmind" in f for f in flat)  # config kept


def test_uninstall_data_wipes_data_config_and_volumes(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    rc = install_cmd.cmd_uninstall(data=True, yes=True, install_dir=tmp_path)

    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("down" in f and "--volumes" in f for f in flat)
    assert any("rm -rf /var/lib/agmind" in f for f in flat)
    assert any("rm -rf /etc/agmind" in f for f in flat)
    # the stale-wizard-state wipe (the "Unknown services requested" blocker) — under --data only
    assert any(".local/share/agmind" in f for f in flat)


def test_uninstall_sweeps_lingering_agmind_containers_and_networks(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    # simulate compose-down-missed orphans (e.g. a removed subsystem still running)
    monkeypatch.setattr(
        install_cmd,
        "_docker_ids",
        lambda cmd: ["agmind-phoenix"] if "ps" in cmd else (["agmind"] if "network" in cmd else []),
    )
    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)
    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("docker rm -f agmind-phoenix" in f for f in flat)  # orphan container force-removed
    assert any("docker network rm agmind" in f for f in flat)  # orphan network removed


def test_uninstall_aborts_when_not_confirmed(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(install_cmd.typer, "confirm", lambda *a, **k: False)

    rc = install_cmd.cmd_uninstall(yes=False, install_dir=tmp_path)

    assert rc == 1
    assert calls == []  # nothing torn down


def test_uninstall_skips_compose_down_when_no_compose_file(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    # no docker-compose.yml in tmp_path
    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)
    assert rc == 0
    # list-membership, not substring: the tmp path itself may contain "compose"
    assert not any("compose" in c for c in calls)  # no compose-down command
    assert any(f"rm -rf {tmp_path}" in " ".join(c) for c in calls)  # still removes the dir
