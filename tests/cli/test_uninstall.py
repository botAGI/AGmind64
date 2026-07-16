"""`agmind uninstall` — tear down the stack for a clean reinstall. live-deploy 2026-06-07:
the in-place reconcile left a wedged stack (a dependency-failed deploy step), so the operator
needs a full-clean path. Default keeps data; --data wipes everything."""

from __future__ import annotations

from pathlib import Path

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
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    sweep_argvs: list[list[str]] = []

    def fake_docker_ids(cmd: list[str]) -> list[str]:
        sweep_argvs.append(cmd)
        # simulate compose-down-missed orphans (e.g. a removed subsystem still running)
        if "ps" in cmd:
            return ["agmind-phoenix"]
        if "network" in cmd:
            return ["agmind"]
        return []

    monkeypatch.setattr(install_cmd, "_docker_ids", fake_docker_ids)

    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)
    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("docker rm -f agmind-phoenix" in f for f in flat)  # orphan container force-removed
    assert any("docker network rm agmind" in f for f in flat)  # orphan network removed

    # Test C (label-filter argv): every sweep query filters by the compose-project label,
    # never by a name substring that a foreign `agmind-*` project could also match.
    assert sweep_argvs, "expected at least one sweep query"
    for argv in sweep_argvs:
        joined = " ".join(argv)
        assert "label=com.docker.compose.project=agmind" in joined
        assert "name=agmind-" not in joined
        assert "name=agmind" not in joined


def test_uninstall_decoy_foreign_compose_project_survives(monkeypatch, tmp_path):
    """Test D (decoy survives — unit level, per orchestrator decision 3): a foreign compose
    project's container (e.g. the live `agmind-dify-cf` tunnel decoy class) is matchable ONLY
    by a name-substring query, never by the fixed `agmind` compose-project label — so the sweep
    must never hand it to `_run_cmd` for removal."""
    calls = _capture(monkeypatch)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    def fake_docker_ids(cmd: list[str]) -> list[str]:
        # The label-filtered query (what the fixed sweep issues) sees nothing for the decoy;
        # only an (obsolete) name-substring query would have matched it.
        if "label=com.docker.compose.project=agmind" in " ".join(cmd):
            return []
        if "ps" in cmd:
            return ["agmind-foo-decoy"]  # foreign compose project, name-substring match only
        return []

    monkeypatch.setattr(install_cmd, "_docker_ids", fake_docker_ids)

    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)

    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert not any("agmind-foo-decoy" in f for f in flat)  # decoy survives, never removed


def test_uninstall_aborts_when_not_confirmed(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    # sentinel present — this test isolates the confirm-abort path, not the sentinel guard
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(install_cmd.typer, "confirm", lambda *a, **k: False)

    rc = install_cmd.cmd_uninstall(yes=False, install_dir=tmp_path)

    assert rc == 1
    assert calls == []  # nothing torn down


def test_uninstall_skips_compose_down_when_no_compose_file(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    # no docker-compose.yml in tmp_path — missing sentinel, bypass explicitly via --force
    rc = install_cmd.cmd_uninstall(yes=True, force=True, install_dir=tmp_path)
    assert rc == 0
    # list-membership, not substring: the tmp path itself may contain "compose"
    assert not any("compose" in c for c in calls)  # no compose-down command
    assert any(f"rm -rf {tmp_path}" in " ".join(c) for c in calls)  # still removes the dir


# --- P0.10 / D-02: forbidden-ancestor + sentinel gate (fix 2, task 1) ---


def test_forbidden_uninstall_target_rejects_ancestors_but_allows_opt_agmind_child():
    """EXACT-equality allowlist: `/`, `/home`, `/etc`, `/var`, `/opt`, $HOME are forbidden
    uninstall targets, but a legitimate CHILD of a forbidden ancestor (the default
    /opt/agmind install dir) must NOT be rejected."""
    for forbidden in (
        Path("/"),
        Path("/home"),
        Path("/etc"),
        Path("/var"),
        Path("/opt"),
        Path.home(),
    ):
        assert install_cmd._forbidden_uninstall_target(forbidden) is not None

    # child-of-forbidden control: /opt/agmind is a legitimate install dir, not /opt itself
    assert install_cmd._forbidden_uninstall_target(Path("/opt/agmind")) is None


def test_uninstall_refuses_forbidden_ancestor_root(monkeypatch):
    """cmd_uninstall(install_dir=Path("/")) must refuse before any teardown — including
    before the confirm prompt (default yes=False, no --force, no docker-compose.yml at /)."""
    calls = _capture(monkeypatch)

    rc = install_cmd.cmd_uninstall(install_dir=Path("/"))

    assert rc != 0
    assert calls == []  # no `sudo rm -rf` attempted, no compose down, no sweep


def test_uninstall_refuses_missing_sentinel_without_force(monkeypatch, tmp_path):
    """No docker-compose.yml at install_dir and no --force → refuse before any teardown."""
    calls = _capture(monkeypatch)
    # tmp_path deliberately left empty — no install sentinel

    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)

    assert rc != 0
    assert calls == []


def test_uninstall_force_bypasses_missing_sentinel_guard(monkeypatch, tmp_path):
    """--force lets an operator proceed even without the docker-compose.yml sentinel."""
    calls = _capture(monkeypatch)

    rc = install_cmd.cmd_uninstall(yes=True, force=True, install_dir=tmp_path)

    assert rc == 0
    assert any(f"rm -rf {tmp_path}" in " ".join(c) for c in calls)


def test_uninstall_sentinel_present_proceeds_without_force(monkeypatch, tmp_path):
    """A real install dir (docker-compose.yml sentinel present) proceeds without --force."""
    calls = _capture(monkeypatch)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    rc = install_cmd.cmd_uninstall(yes=True, install_dir=tmp_path)

    assert rc == 0
    assert any(f"rm -rf {tmp_path}" in " ".join(c) for c in calls)


def test_uninstall_force_help_distinct_from_upgrade_force():
    """The new uninstall --force option must exist and its help must be distinct from
    `agmind upgrade --force` (different command namespace, different meaning — no operator
    confusion). Introspect the click param directly rather than parsing rendered --help
    text: typer 0.26 rich-wraps option names by terminal width, so a substring check on
    help output is CI-terminal-dependent (see project journal / CLAUDE.md)."""
    import typer

    from agmind.cli import _make_app

    group = typer.main.get_command(_make_app())
    uninstall_cmd = group.commands["uninstall"]
    force_params = [p for p in uninstall_cmd.params if p.name == "force"]

    assert force_params, "uninstall must expose a --force option"
    assert "--force" in force_params[0].opts
    assert "held in version_holds" not in (force_params[0].help or "")
