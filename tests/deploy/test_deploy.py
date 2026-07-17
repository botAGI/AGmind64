"""Phase L.B: tests for agmind.deploy (snapshot + diff + runner)."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from agmind.deploy import runner
from agmind.deploy import snapshot as snapshot_module
from agmind.deploy.diff import ComposeDiff, compute_diff, format_diff
from agmind.deploy.snapshot import SnapshotManager

pytestmark = pytest.mark.backend_any


# ---------- snapshot ----------


@pytest.fixture
def snapshot_mgr(tmp_path: Path) -> SnapshotManager:
    return SnapshotManager(snapshots_dir=tmp_path / "snapshots", retention=3)


def test_save_creates_snapshot_dir(snapshot_mgr: SnapshotManager) -> None:
    snap = snapshot_mgr.save(
        compose_text="services: {}\n",
        profile="core",
        reason="test",
    )
    assert snap.path.exists()
    assert snap.compose_file.exists()
    assert snap.meta_file.exists()
    assert snap.profile == "core"
    assert snap.reason == "test"


def test_save_preserves_compose_content(snapshot_mgr: SnapshotManager) -> None:
    content = "version: '3.9'\nservices:\n  foo:\n    image: bar:1\n"
    snap = snapshot_mgr.save(compose_text=content, profile="core")
    assert snap.compose_file.read_text(encoding="utf-8") == content


def test_save_removes_partial_compose_on_write_failure(
    snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.core import files as files_mod

    def flaky_fsync(fd: int) -> None:
        # write_text_atomic fsyncs the compose temp fd before the atomic replace;
        # failing there must unlink the partial temp and leave no compose.yml.
        raise OSError("disk full")

    monkeypatch.setattr(files_mod.os, "fsync", flaky_fsync)

    with pytest.raises(OSError, match="disk full"):
        snapshot_mgr.save(compose_text="services: {}\n", profile="core")

    assert list(snapshot_mgr.snapshots_dir.rglob("compose.yml")) == []
    # mkstemp uses a random suffix, so assert no leftover temp by glob.
    assert list(snapshot_mgr.snapshots_dir.rglob(".compose.yml.*.tmp")) == []


def test_save_removes_partial_snapshot_on_descriptor_copy_failure(
    snapshot_mgr: SnapshotManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptors = tmp_path / "descriptors"
    descriptors.mkdir()
    (descriptors / "llama-llm.yaml").write_text("name: llama-llm\n", encoding="utf-8")

    def flaky_copytree(source: Path, target: Path, *args: object, **kwargs: object) -> None:
        del source, args, kwargs
        target.mkdir(parents=True, exist_ok=True)
        (target / "BROKEN.yaml").write_text("broken\n", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(snapshot_module.shutil, "copytree", flaky_copytree)

    with pytest.raises(OSError, match="disk full"):
        snapshot_mgr.save(
            compose_text="services: {}\n",
            profile="core",
            descriptors_dir=descriptors,
        )

    assert list(snapshot_mgr.snapshots_dir.iterdir()) == []


def test_meta_json_has_required_fields(snapshot_mgr: SnapshotManager) -> None:
    snap = snapshot_mgr.save(compose_text="", profile="rag", reason="manual")
    meta = json.loads(snap.meta_file.read_text(encoding="utf-8"))
    assert "id" in meta
    assert "timestamp" in meta
    assert meta["profile"] == "rag"
    assert meta["reason"] == "manual"


def test_list_returns_newest_first(snapshot_mgr: SnapshotManager) -> None:
    snapshot_mgr.save(compose_text="", profile="p1")
    snapshot_mgr.save(compose_text="", profile="p2")

    snaps = snapshot_mgr.list()
    assert len(snaps) == 2
    assert snaps[0].profile == "p2"
    assert snaps[1].profile == "p1"


def test_latest_returns_most_recent(snapshot_mgr: SnapshotManager) -> None:
    assert snapshot_mgr.latest() is None
    snapshot_mgr.save(compose_text="", profile="x")
    assert snapshot_mgr.latest() is not None
    assert snapshot_mgr.latest().profile == "x"


def test_get_by_id(snapshot_mgr: SnapshotManager) -> None:
    s = snapshot_mgr.save(compose_text="", profile="x")
    found = snapshot_mgr.get(s.id)
    assert found is not None
    assert found.id == s.id


def test_get_missing_id_returns_none(snapshot_mgr: SnapshotManager) -> None:
    assert snapshot_mgr.get("nonexistent-id") is None


def test_prune_keeps_only_retention_count(snapshot_mgr: SnapshotManager) -> None:
    for i in range(5):
        snapshot_mgr.save(compose_text="", profile=f"p{i}")

    # retention=3, so only 3 newest remain after auto-prune in save()
    snaps = snapshot_mgr.list()
    assert len(snaps) == 3
    profiles = [s.profile for s in snaps]
    assert profiles == ["p4", "p3", "p2"]  # newest first


def test_prune_old_returns_local_removed_count(tmp_path: Path) -> None:
    mgr = SnapshotManager(snapshots_dir=tmp_path / "snapshots", retention=10)
    mgr.save(compose_text="", profile="old")
    mgr.save(compose_text="", profile="new")

    mgr.retention = 1
    assert mgr.prune_old() == 1


def test_prune_old_uses_sudo_for_root_owned_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots_dir = tmp_path / "snapshots"
    newer = snapshots_dir / "2026-05-26T11-10-02.000000Z"
    older = snapshots_dir / "2026-05-26T11-10-01.000000Z"
    newer.mkdir(parents=True)
    older.mkdir(parents=True)
    for path, profile in ((newer, "new"), (older, "old")):
        (path / "meta.json").write_text(
            json.dumps(
                {
                    "id": path.name,
                    "timestamp": "2026-05-26T11:10:02+00:00"
                    if profile == "new"
                    else "2026-05-26T11:10:01+00:00",
                    "profile": profile,
                    "reason": "",
                    "agmind_version": "",
                }
            ),
            encoding="utf-8",
        )
    calls: list[dict[str, object]] = []

    def fake_rmtree(path: Path) -> None:
        raise PermissionError(f"root-owned: {path}")

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "input": input})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(snapshot_module.shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(snapshot_module.subprocess, "run", fake_run)

    mgr = SnapshotManager(snapshots_dir=snapshots_dir, retention=1, sudo_password="pw")

    assert mgr.prune_old() == 1
    assert calls == [
        {
            "cmd": [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "rm",
                "-rf",
                "--one-file-system",
                str(older),
            ],
            "input": "pw\n",
        }
    ]


def test_save_copies_descriptors(snapshot_mgr: SnapshotManager, tmp_path: Path) -> None:
    desc_dir = tmp_path / "src_descriptors"
    desc_dir.mkdir()
    (desc_dir / "foo.yaml").write_text("name: foo\n", encoding="utf-8")

    snap = snapshot_mgr.save(
        compose_text="",
        profile="x",
        descriptors_dir=desc_dir,
    )
    assert (snap.descriptors_dir / "foo.yaml").exists()


def test_save_copies_env_file(snapshot_mgr: SnapshotManager, tmp_path: Path) -> None:
    env_file = tmp_path / "src.env"
    env_file.write_text("AGMIND_DOMAIN=test.lan\n", encoding="utf-8")

    snap = snapshot_mgr.save(
        compose_text="",
        profile="x",
        env_file=env_file,
    )

    assert snap.env_file.exists()
    assert stat.S_IMODE(snap.env_file.stat().st_mode) == 0o600
    assert "AGMIND_DOMAIN" in snap.env_file.read_text(encoding="utf-8")


def test_save_copies_version_env_file(snapshot_mgr: SnapshotManager, tmp_path: Path) -> None:
    version_env = tmp_path / "version.env"
    version_env.write_text("AGMIND_VERSION=0.6.0\nLLAMA_LLM_VERSION=v1\n", encoding="utf-8")

    snap = snapshot_mgr.save(
        compose_text="",
        profile="x",
        version_env_file=version_env,
    )

    assert snap.version_env_file.exists()
    assert stat.S_IMODE(snap.version_env_file.stat().st_mode) == 0o644
    assert "LLAMA_LLM_VERSION=v1" in snap.version_env_file.read_text(encoding="utf-8")


def test_save_uses_sudo_for_root_owned_snapshot_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "runtime.env"
    env_file.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    root = tmp_path / "root-owned" / "snapshots"
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "input": input})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        snapshot_module,
        "subprocess",
        type("FakeSubprocess", (), {"run": staticmethod(fake_run)})(),
        raising=False,
    )

    mgr = SnapshotManager(snapshots_dir=root, retention=3, sudo_password="pw")
    snap = mgr.save(compose_text="services: {}\n", profile="core", env_file=env_file)

    assert snap.path.parent == root
    assert any(
        call["cmd"][:8] == ["sudo", "-S", "-p", "", "--", "install", "-d", "-m"] for call in calls
    )
    installed_paths = [
        call["cmd"][-1]
        for call in calls
        if call["cmd"][:7] == ["sudo", "-S", "-p", "", "--", "install", "-D"]
    ]
    assert str(snap.compose_file) in installed_paths
    assert str(snap.meta_file) in installed_paths
    assert str(snap.env_file) in installed_paths
    env_call = next(call for call in calls if call["cmd"][-1] == str(snap.env_file))
    assert env_call["cmd"][-3] == "0600"
    assert all(call["input"] == "pw\n" for call in calls)
    assert all("pw" not in call["cmd"] for call in calls)


# ---------- diff ----------


def test_diff_empty_no_changes() -> None:
    diff = compute_diff("services: {}", "services: {}")
    assert not diff.has_changes
    assert diff.total_changes == 0


def test_diff_added_service() -> None:
    current = "services: {}"
    new = "services:\n  qdrant:\n    image: qdrant/qdrant:v1.18.0\n"
    diff = compute_diff(current, new)
    assert diff.has_changes
    assert "qdrant" in diff.added
    assert not diff.removed


def test_diff_removed_service() -> None:
    current = "services:\n  oldsvc:\n    image: a:1\n"
    new = "services: {}"
    diff = compute_diff(current, new)
    assert "oldsvc" in diff.removed


def test_diff_image_changed() -> None:
    current = "services:\n  foo:\n    image: foo:1\n"
    new = "services:\n  foo:\n    image: foo:2\n"
    diff = compute_diff(current, new)
    assert len(diff.image_changed) == 1
    assert diff.image_changed[0].name == "foo"
    assert "foo:1" in diff.image_changed[0].detail
    assert "foo:2" in diff.image_changed[0].detail


def test_diff_config_changed() -> None:
    current = "services:\n  foo:\n    image: foo:1\n    cpus: 1\n"
    new = "services:\n  foo:\n    image: foo:1\n    cpus: 2\n"
    diff = compute_diff(current, new)
    assert len(diff.config_changed) == 1


def test_format_diff_no_changes_shows_check() -> None:
    diff = ComposeDiff()
    out = format_diff(diff)
    assert "no changes" in out


def test_format_diff_shows_counts() -> None:
    current = "services: {}"
    new = "services:\n  a:\n    image: a:1\n  b:\n    image: b:2\n"
    diff = compute_diff(current, new)
    out = format_diff(diff)
    assert "Added (2)" in out
    assert "+ a" in out
    assert "+ b" in out


def test_format_diff_verbose_includes_raw() -> None:
    current = "services:\n  foo:\n    image: foo:1\n"
    new = "services:\n  foo:\n    image: foo:2\n"
    diff = compute_diff(current, new)
    out = format_diff(diff, verbose=True)
    assert "Full unified diff" in out


# ---------- runner compose guard ----------


def test_runner_run_compose_uses_env_file_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "check": check,
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner._run_compose(["config", "--quiet"], cwd=tmp_path) == (0, "", "")
    assert calls == [
        {
            "cmd": [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "config",
                "--quiet",
            ],
            "cwd": tmp_path,
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": runner.COMPOSE_SHORT_TIMEOUT,
        }
    ]


def test_runner_run_compose_can_use_sudo_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        input: str | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "check": check,
                "input": input,
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    # No user docker login present -> plain sudo (anonymous) form, unchanged.
    monkeypatch.setattr(runner, "_user_docker_config_dir", lambda: None)

    assert runner._run_compose(["ps"], cwd=tmp_path, sudo_password="pw") == (0, "", "")

    assert calls[0]["cmd"] == ["sudo", "-S", "-p", "", "--", "docker", "compose", "ps"]
    assert calls[0]["input"] == "pw\n"
    assert calls[0]["timeout"] == runner.COMPOSE_SHORT_TIMEOUT
    assert "pw" not in calls[0]["cmd"]


def test_runner_sudo_compose_uses_invoking_user_docker_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sudo-docker must use the invoking user's authenticated ~/.docker, not root's empty
    config — otherwise `docker compose up` pulls 36 images ANONYMOUSLY and hits Docker
    Hub's `toomanyrequests` mid-deploy (the real deploy failure)."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_user_docker_config_dir", lambda: "/home/op/.docker")

    runner._run_compose(["pull"], cwd=tmp_path, sudo_password="pw")

    assert calls[0] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "env",
        "DOCKER_CONFIG=/home/op/.docker",
        "docker",
        "compose",
        "pull",
    ]


def test_runner_run_compose_reports_oserror_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise PermissionError("docker denied")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc, stdout, stderr = runner._run_compose(["ps"], cwd=tmp_path)

    assert rc == 127
    assert stdout == ""
    assert "docker denied" in stderr


def test_runner_run_compose_times_out_short_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kwargs})
        raise subprocess.TimeoutExpired(cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc, stdout, stderr = runner._run_compose(["ps"], cwd=tmp_path)

    assert rc == 124
    assert stdout == ""
    assert "timed out" in stderr
    assert calls[0]["timeout"] == runner.COMPOSE_SHORT_TIMEOUT


def test_deploy_apply_validates_compose_before_replacing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    calls: list[list[str]] = []

    monkeypatch.setattr(
        runner,
        "render_to_string",
        lambda **_kwargs: (
            "services:\n"
            "  postgres:\n"
            "    image: postgres:17.6-alpine\n"
            "    environment:\n"
            "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}\n"
        ),
    )

    def fake_run_compose(args: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(args)
        return 1, "", "POSTGRES_PASSWORD is required"

    monkeypatch.setattr(runner, "_run_compose", fake_run_compose)

    result = runner.deploy(
        profiles=["core", "rag"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
    )

    assert not result.success
    assert "docker compose config failed" in result.message
    assert "POSTGRES_PASSWORD is required" in result.message
    assert not compose_file.exists()
    assert calls and calls[0][0] == "-f"


def test_deploy_apply_reports_compose_validation_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    rendered = "services:\n  traefik:\n    image: traefik:v3.6.2\n"

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)

    def fail_validate(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("validation temp denied")

    def fail_write_text_maybe_sudo(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compose file should not be written after validation failure")

    monkeypatch.setattr(runner, "_validate_compose_config", fail_validate)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fail_write_text_maybe_sudo)

    result = runner.deploy(
        profiles=[],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        services=["traefik"],
    )

    assert result.success is False
    assert "docker compose config failed" in result.message
    assert "validation temp denied" in result.message
    assert not compose_file.exists()


def test_deploy_apply_reports_compose_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = "services:\n  traefik:\n    image: traefik:v3.6.2\n"

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args, **_kwargs: (0, ""))

    def fail_write_text_maybe_sudo(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    def fail_run_compose(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        raise AssertionError("compose up should not run after compose write failure")

    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fail_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "_run_compose", fail_run_compose)

    result = runner.deploy(
        profiles=[],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        services=["traefik"],
    )

    assert result.success is False
    assert result.rollback_performed is False
    assert "write compose failed" in result.message
    assert "disk full" in result.message


def test_deploy_apply_reports_snapshot_failure_before_replacing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    rendered = "services:\n  traefik:\n    image: traefik:v3.6.2\n"

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args, **_kwargs: (0, ""))

    class FailingSnapshotManager:
        def __init__(self, sudo_password: str | None = None) -> None:
            self.sudo_password = sudo_password

        def save(self, **_kwargs: object) -> object:
            raise OSError("snapshot disk full")

    def fail_write_text_maybe_sudo(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compose file should not be replaced after snapshot failure")

    monkeypatch.setattr(runner, "SnapshotManager", FailingSnapshotManager)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fail_write_text_maybe_sudo)

    result = runner.deploy(
        profiles=[],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        services=["traefik"],
    )

    assert result.success is False
    assert result.snapshot is None
    assert "snapshot failed" in result.message
    assert "snapshot disk full" in result.message
    assert (install_dir / "docker-compose.yml").read_text(encoding="utf-8") == "services: {}\n"


def test_deploy_apply_reports_snapshot_env_prep_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    compose_file = install_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    (install_dir / ".env").write_text("LOCAL=placeholder\n", encoding="utf-8")
    rendered = "services:\n  postgres:\n    image: postgres:17.6-alpine\n"

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args, **_kwargs: (0, ""))

    def fail_read_text_maybe_sudo(path: Path, sudo_password: str | None = None) -> str:
        if path.name == ".env":
            raise OSError("env sudo read denied")
        return path.read_text(encoding="utf-8")

    class FailingSnapshotManager:
        def __init__(self, sudo_password: str | None = None) -> None:
            self.sudo_password = sudo_password

        def save(self, **_kwargs: object) -> object:
            raise AssertionError("snapshot save should not run after env prep failure")

    def fail_write_text_maybe_sudo(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compose file should not be replaced after env prep failure")

    monkeypatch.setattr(runner, "_read_text_maybe_sudo", fail_read_text_maybe_sudo)
    monkeypatch.setattr(runner, "SnapshotManager", FailingSnapshotManager)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fail_write_text_maybe_sudo)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        sudo_password="pw",
        services=["postgres"],
    )

    assert result.success is False
    assert result.snapshot is None
    assert "snapshot prep failed" in result.message
    assert "env sudo read denied" in result.message
    assert compose_file.read_text(encoding="utf-8") == "services: {}\n"


def test_deploy_apply_reports_install_dir_prepare_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "install"
    rendered = "services:\n  traefik:\n    image: traefik:v3.6.2\n"
    original_mkdir = Path.mkdir

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == install_dir:
            raise PermissionError("install dir denied")
        return original_mkdir(self, *args, **kwargs)

    def fail_validate(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise AssertionError("compose validation should not run after install dir failure")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    monkeypatch.setattr(runner, "_validate_compose_config", fail_validate)

    result = runner.deploy(
        profiles=[],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        services=["traefik"],
    )

    assert result.success is False
    assert result.diff is not None
    assert "prepare install dir failed" in result.message
    assert "install dir denied" in result.message
    assert not install_dir.exists()


def test_deploy_rejects_explicit_empty_services_before_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_load_descriptors() -> object:
        raise AssertionError("descriptors should not be loaded")

    def fail_render_to_string(**_kwargs: object) -> str:
        raise AssertionError("compose render should not run")

    monkeypatch.setattr(runner, "load_descriptors", fail_load_descriptors)
    monkeypatch.setattr(runner, "render_to_string", fail_render_to_string)

    result = runner.deploy(
        profiles=[],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        services=[],
    )

    assert result.success is False
    assert result.message == "no selected services for deploy"
    assert not (tmp_path / "docker-compose.yml").exists()


def test_deploy_rejects_unknown_explicit_service_before_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_render_to_string(**_kwargs: object) -> str:
        raise AssertionError("compose render should not run")

    monkeypatch.setattr(runner, "render_to_string", fail_render_to_string)

    install_dir = tmp_path / "install"
    result = runner.deploy(
        profiles=[],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        services=["missing-service"],
    )

    assert result.success is False
    assert result.message == "unknown selected services for deploy: missing-service"
    assert not install_dir.exists()


def test_deploy_rejects_unknown_profile_before_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_render_to_string(**_kwargs: object) -> str:
        raise AssertionError("compose render should not run")

    monkeypatch.setattr(runner, "render_to_string", fail_render_to_string)

    install_dir = tmp_path / "install"
    result = runner.deploy(
        profiles=["core", "missing-profile"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
    )

    assert result.success is False
    assert result.message == "unknown selected profiles for deploy: missing-profile"
    assert not install_dir.exists()


def test_deploy_render_failure_does_not_create_install_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_render_to_string(**_kwargs: object) -> str:
        raise ValueError(
            "Missing dependencies for selected services: dify-api requires postgres, redis"
        )

    monkeypatch.setattr(runner, "render_to_string", fail_render_to_string)

    install_dir = tmp_path / "install"
    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        services=["dify-api"],
    )

    assert result.success is False
    assert "render failed: Missing dependencies" in result.message
    assert not install_dir.exists()


def test_deploy_explicit_services_ignore_unused_unknown_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    render_kwargs: dict[str, object] = {}

    def fake_render_to_string(**kwargs: object) -> str:
        render_kwargs.update(kwargs)
        return "services:\n  traefik:\n    image: traefik:v3.6.2\n"

    monkeypatch.setattr(runner, "render_to_string", fake_render_to_string)

    install_dir = tmp_path / "install"
    result = runner.deploy(
        profiles=["missing-profile"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=False,
        services=["traefik"],
    )

    assert result.success is True
    assert result.message == "1 pending change(s) — re-run with --apply to deploy"
    assert render_kwargs["profiles"] == ["missing-profile"]
    assert render_kwargs["services"] == ["traefik"]
    assert not (install_dir / "docker-compose.yml").exists()


def test_deploy_progress_describes_explicit_service_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner,
        "render_to_string",
        lambda **_kwargs: "services:\n  traefik:\n    image: traefik:v3.6.2\n",
    )
    events: list[tuple[str, str]] = []

    result = runner.deploy(
        profiles=["stale-profile"],
        install_dir=tmp_path / "install",
        domain="ci.example.com",
        apply=False,
        services=["traefik"],
        progress=lambda step, message: events.append((step, message)),
    )

    assert result.success is True
    assert ("render", "rendering compose for services=['traefik'], domain=ci.example.com") in events
    assert not any(
        step == "render" and "profiles=['stale-profile']" in message for step, message in events
    )


def test_validate_compose_config_uses_tempfile_outside_install_dir_when_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    install_dir.chmod(0o555)
    calls: list[dict[str, object]] = []

    def fake_run_compose_maybe_sudo(
        args: list[str],
        cwd: Path,
        sudo_password: str | None,
    ) -> tuple[int, str, str]:
        calls.append({"args": args, "cwd": cwd, "sudo_password": sudo_password})
        return 0, "", ""

    monkeypatch.setattr(runner, "_run_compose_maybe_sudo", fake_run_compose_maybe_sudo)

    try:
        rc, stderr = runner._validate_compose_config(
            "services: {}\n",
            install_dir,
            sudo_password="pw",
        )
    finally:
        install_dir.chmod(0o755)

    assert (rc, stderr) == (0, "")
    assert calls
    compose_path = Path(calls[0]["args"][1])
    assert compose_path.parent != install_dir
    assert calls[0]["cwd"] == install_dir
    assert calls[0]["sudo_password"] == "pw"


def test_deploy_apply_writes_compose_via_sudo_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = "services:\n  postgres:\n    image: postgres:17.6-alpine\n"
    writes: list[dict[str, object]] = []

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args, **_kwargs: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_args, **_kwargs: (0, ""))

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append({"path": path, "text": text, "sudo_password": sudo_password, "mode": mode})

    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo, raising=False)

    result = runner.deploy(
        profiles=["core"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        sudo_password="pw",
        services=["postgres"],
    )

    assert result.success
    assert writes == [
        {
            "path": tmp_path / "docker-compose.yml",
            "text": rendered,
            "sudo_password": "pw",
            "mode": "0644",
        }
    ]


def test_write_text_maybe_sudo_respects_mode_on_local_write(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    target.chmod(0o644)

    runner._write_text_maybe_sudo(target, "POSTGRES_PASSWORD=new\n", mode="0600")

    assert target.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_text_maybe_sudo_preserves_local_file_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    target.chmod(0o600)
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.parent == tmp_path:
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        runner._write_text_maybe_sudo(target, "POSTGRES_PASSWORD=new\n", mode="0600")

    assert target.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=old\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_deploy_snapshot_uses_sudo_readable_env_copy(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    current_compose = "services: {}\n"
    rendered = "services:\n  postgres:\n    image: postgres:17.6-alpine\n"
    (install_dir / "docker-compose.yml").write_text(current_compose, encoding="utf-8")
    (install_dir / ".env").write_text("LOCAL=placeholder\n", encoding="utf-8")
    (install_dir / "version.env").write_text("AGMIND_VERSION=local\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args, **_kwargs: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_args, **_kwargs: (0, ""))

    def fake_read_text_maybe_sudo(path: Path, sudo_password: str | None = None) -> str:
        if path.name == ".env":
            return "POSTGRES_PASSWORD=old\n"
        if path.name == "version.env":
            return "AGMIND_VERSION=old\n"
        return path.read_text(encoding="utf-8")

    class FakeSnapshotManager:
        def __init__(self, sudo_password: str | None = None) -> None:
            captured["snapshot_sudo_password"] = sudo_password

        def save(
            self,
            compose_text: str,
            profile: str,
            reason: str = "",
            descriptors_dir: Path | None = None,
            env_file: Path | None = None,
            version_env_file: Path | None = None,
            agmind_version: str = "",
        ):
            assert env_file is not None
            assert version_env_file is not None
            captured["env_file"] = env_file
            captured["env_text"] = env_file.read_text(encoding="utf-8")
            captured["version_env_file"] = version_env_file
            captured["version_env_text"] = version_env_file.read_text(encoding="utf-8")
            return snapshot_mgr.save(compose_text=compose_text, profile=profile, reason=reason)

    monkeypatch.setattr(runner, "_read_text_maybe_sudo", fake_read_text_maybe_sudo, raising=False)
    monkeypatch.setattr(runner, "SnapshotManager", FakeSnapshotManager)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        sudo_password="pw",
        services=["postgres"],
    )

    assert result.success
    assert captured["snapshot_sudo_password"] == "pw"
    assert captured["env_file"] != install_dir / ".env"
    assert captured["env_text"] == "POSTGRES_PASSWORD=old\n"
    assert captured["version_env_file"] != install_dir / "version.env"
    assert captured["version_env_text"] == "AGMIND_VERSION=old\n"


def test_deploy_blocks_deploy_level_conflicts_before_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deploy() blocks on host-port conflicts before calling render_to_string.

    Injects a synthetic conflict report so the test does not depend on any
    particular pair of services being present in the catalog.
    """
    from agmind.components.checks import DeployIssue, DeployReport

    rendered = False

    def fake_render_to_string(**_kwargs: object) -> str:
        nonlocal rendered
        rendered = True
        return "services: {}\n"

    def fake_check_deploy_conflicts(_selected: object) -> DeployReport:
        return DeployReport(
            issues=(
                DeployIssue(
                    severity="error",
                    kind="host_port_conflict",
                    services=("svc-a", "svc-b"),
                    detail="80",
                    message="Host port 80 is published by svc-a and svc-b",
                ),
                DeployIssue(
                    severity="error",
                    kind="host_port_conflict",
                    services=("svc-a", "svc-b"),
                    detail="443",
                    message="Host port 443 is published by svc-a and svc-b",
                ),
            )
        )

    monkeypatch.setattr(runner, "render_to_string", fake_render_to_string)
    monkeypatch.setattr(runner, "check_deploy_conflicts", fake_check_deploy_conflicts)

    result = runner.deploy(
        profiles=["core"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=False,
    )

    assert not result.success
    assert "deploy conflict" in result.message
    assert "Host port 80 is published by" in result.message
    assert "Host port 443 is published by" in result.message
    assert not rendered


def test_deploy_apply_starts_rendered_services_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = (
        "services:\n"
        "  llama-llm:\n"
        "    image: ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049\n"
        "    profiles:\n"
        "      - core\n"
        "  qdrant:\n"
        "    image: qdrant/qdrant:v1.18.0\n"
        "    profiles:\n"
        "      - core\n"
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_args, **_kwargs: (True, []))

    def fake_stream_compose(args: list[str], **_kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(runner, "_stream_compose", fake_stream_compose)

    result = runner.deploy(
        profiles=["core"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
    )

    assert result.success
    # Streamed pull phase, then `up --pull never` (no silent --quiet-pull inside up).
    # --ignore-buildable: build-only services (agent-agno/pydanticai/ui) have no
    # registry image; `up` still builds them regardless of --pull policy.
    assert calls == [
        [
            "--progress",
            "plain",
            "pull",
            "--ignore-buildable",
            "--policy",
            "missing",
            "llama-llm",
            "qdrant",
        ],
        ["up", "-d", "--remove-orphans", "--pull", "never", "llama-llm", "qdrant"],
    ]


def test_deploy_apply_offline_uses_policy_never(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGMIND_OFFLINE must reach the REAL deploy path (runner), not just the orphaned
    ImagePullStep. Air-gap: `pull --policy never` so a digest-pinned compose never re-pulls
    from the network (docker save/load strips RepoDigest). Regression: the policy was
    hardcoded `missing` here, so the P1.7 offline guard was bypassed by the live install."""
    rendered = (
        "services:\n"
        "  llama-llm:\n"
        "    image: ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049\n"
        "    profiles:\n"
        "      - core\n"
        "  qdrant:\n"
        "    image: qdrant/qdrant:v1.18.0\n"
        "    profiles:\n"
        "      - core\n"
    )
    calls: list[list[str]] = []

    monkeypatch.setenv("AGMIND_OFFLINE", "1")
    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_args: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_args, **_kwargs: (True, []))

    def fake_stream_compose(args: list[str], **_kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(runner, "_stream_compose", fake_stream_compose)

    result = runner.deploy(
        profiles=["core"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
    )

    assert result.success
    # Air-gap: NO network pull. `--policy never` skips, `up --pull never` uses local images.
    assert calls == [
        [
            "--progress",
            "plain",
            "pull",
            "--ignore-buildable",
            "--policy",
            "never",
            "llama-llm",
            "qdrant",
        ],
        ["up", "-d", "--remove-orphans", "--pull", "never", "llama-llm", "qdrant"],
    ]


def test_resolve_pull_policy_honors_offline_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single source of truth for the compose pull policy (deploy layer)."""
    monkeypatch.delenv("AGMIND_OFFLINE", raising=False)
    assert runner.resolve_pull_policy() == "missing"
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv("AGMIND_OFFLINE", val)
        assert runner.resolve_pull_policy() == "never"
    monkeypatch.setenv("AGMIND_OFFLINE", "0")
    assert runner.resolve_pull_policy() == "missing"
    # Explicit override wins over the env.
    monkeypatch.setenv("AGMIND_OFFLINE", "1")
    assert runner.resolve_pull_policy(offline=False) == "missing"


def test_rollback_writes_compose_and_env_via_sudo_helper(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_env = tmp_path / "current.env"
    current_version_env = tmp_path / "version.env"
    current_env.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    current_version_env.write_text("AGMIND_VERSION=old\n", encoding="utf-8")
    snap = snapshot_mgr.save(
        compose_text="services: {}\n",
        profile="core",
        env_file=current_env,
        version_env_file=current_version_env,
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    writes: list[dict[str, object]] = []
    original_write_text = Path.write_text

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append({"path": path, "text": text, "sudo_password": sudo_password, "mode": mode})
        original_write_text(path, text, encoding="utf-8")

    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "_run_compose", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    assert runner._rollback_to_snapshot(snap, install_dir, sudo_password="pw")

    assert writes[:3] == [
        {
            "path": install_dir / "docker-compose.yml",
            "text": "services: {}\n",
            "sudo_password": "pw",
            "mode": "0644",
        },
        {
            "path": install_dir / ".env",
            "text": "POSTGRES_PASSWORD=old\n",
            "sudo_password": "pw",
            "mode": "0600",
        },
        {
            "path": install_dir / "version.env",
            "text": "AGMIND_VERSION=old\n",
            "sudo_password": "pw",
            "mode": "0644",
        },
    ]


def test_rollback_removes_stale_version_env_when_snapshot_has_none(
    tmp_path: Path,
    snapshot_mgr: SnapshotManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot_mgr.save(compose_text="services: {}\n", profile="legacy")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    version_env = install_dir / "version.env"
    version_env.write_text("AGMIND_VERSION=newer\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_run_compose", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    assert runner._rollback_to_snapshot(snap, install_dir)
    assert not version_env.exists()


def test_rollback_uses_snapshot_compose_after_sudo_write(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-owned rollback must not re-read install compose without sudo."""
    snap = snapshot_mgr.save(
        compose_text="services:\n  llama-llm:\n    image: llama:1\n",
        profile="core",
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    compose_calls: list[list[str]] = []

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        assert path == install_dir / "docker-compose.yml"
        assert sudo_password == "pw"
        assert mode == "0644"
        # Simulate sudo write into a root-owned file that the current user
        # still cannot read directly.

    def fake_stream_compose(
        args: list[str],
        cwd: Path,
        sudo_password: str | None = None,
        on_line: object = None,
        cancel_event: object = None,
    ) -> tuple[int, str]:
        compose_calls.append(args)
        assert cwd == install_dir
        assert sudo_password == "pw"
        return 0, ""

    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "_stream_compose", fake_stream_compose)

    assert runner._rollback_to_snapshot(snap, install_dir, sudo_password="pw")
    # Rollback up is now streamed (no 60s cap) with an explicit --pull policy (offline-safe).
    assert compose_calls == [["up", "-d", "--remove-orphans", "--pull", "missing", "llama-llm"]]


def test_rollback_restores_descriptors_via_sudo_helper(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptors = tmp_path / "descriptors"
    descriptors.mkdir()
    (descriptors / "llama-llm.yaml").write_text("name: llama-llm\n", encoding="utf-8")
    snap = snapshot_mgr.save(
        compose_text="services: {}\n",
        profile="core",
        descriptors_dir=descriptors,
    )
    install_dir = tmp_path / "install"
    target = install_dir / "templates" / "services"
    tmp_target = target.with_name(f".{target.name}.tmp")
    backup_target = target.with_name(f".{target.name}.rollback")
    target.mkdir(parents=True)
    (target / "old.yaml").write_text("name: old\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_rmtree(path: Path) -> None:
        raise PermissionError(f"root-owned: {path}")

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "input": input})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_run_compose", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    assert runner._rollback_to_snapshot(snap, install_dir, sudo_password="pw")

    assert calls[:4] == [
        {
            "cmd": [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "rm",
                "-rf",
                "--one-file-system",
                str(tmp_target),
            ],
            "input": "pw\n",
        },
        {
            "cmd": [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "rm",
                "-rf",
                "--one-file-system",
                str(backup_target),
            ],
            "input": "pw\n",
        },
        {
            "cmd": [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "install",
                "-d",
                "-m",
                "0755",
                str(tmp_target),
            ],
            "input": "pw\n",
        },
        {
            "cmd": [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "cp",
                "-R",
                "--no-preserve=ownership",
                f"{snap.descriptors_dir}/.",
                str(tmp_target),
            ],
            "input": "pw\n",
        },
    ]
    assert len(calls) == 5
    swap_cmd = calls[4]["cmd"]
    assert isinstance(swap_cmd, list)
    assert swap_cmd[:7] == ["sudo", "-S", "-p", "", "--", "sh", "-c"]
    assert 'mv "$target" "$backup_target"' in swap_cmd[7]
    assert 'mv "$tmp_target" "$target"' in swap_cmd[7]
    assert swap_cmd[8:] == [
        "agmind-restore-descriptors",
        str(target),
        str(tmp_target),
        str(backup_target),
    ]
    assert calls[4]["input"] == "pw\n"


def test_restore_descriptors_preserves_existing_target_on_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "snapshot" / "descriptors"
    source.mkdir(parents=True)
    (source / "new.yaml").write_text("name: new\n", encoding="utf-8")

    target = tmp_path / "install" / "templates" / "services"
    target.mkdir(parents=True)
    old_descriptor = target / "old.yaml"
    old_descriptor.write_text("name: old\n", encoding="utf-8")

    def flaky_copytree(src: Path, dst: Path, *args: object, **kwargs: object) -> None:
        del src, args, kwargs
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "BROKEN.yaml").write_text("broken\n", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(runner.shutil, "copytree", flaky_copytree)

    with pytest.raises(OSError, match="disk full"):
        runner._restore_descriptors_from_snapshot(source, target)

    assert old_descriptor.read_text(encoding="utf-8") == "name: old\n"
    assert not (target / "BROKEN.yaml").exists()
    assert not target.with_name(f".{target.name}.tmp").exists()


def test_restore_descriptors_via_sudo_stages_copy_before_target_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "snapshot" / "descriptors"
    source.mkdir(parents=True)
    target = tmp_path / "install" / "templates" / "services"
    tmp_target = target.with_name(f".{target.name}.tmp")
    backup_target = target.with_name(f".{target.name}.rollback")
    calls: list[list[str]] = []

    def fake_run(args: list[str], sudo_password: str) -> None:
        assert sudo_password == "pw"
        calls.append(args)

    monkeypatch.setattr(runner, "_run_sudo_no_output", fake_run)

    runner._restore_descriptors_from_snapshot(source, target, sudo_password="pw")

    assert calls[:4] == [
        ["rm", "-rf", "--one-file-system", str(tmp_target)],
        ["rm", "-rf", "--one-file-system", str(backup_target)],
        ["install", "-d", "-m", "0755", str(tmp_target)],
        ["cp", "-R", "--no-preserve=ownership", f"{source}/.", str(tmp_target)],
    ]
    assert calls[4][0:2] == ["sh", "-c"]
    assert calls[4][3:] == [
        "agmind-restore-descriptors",
        str(target),
        str(tmp_target),
        str(backup_target),
    ]


def test_runner_rollback_passes_sudo_password_to_snapshot_manager_and_restore(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = snapshot_mgr.save(compose_text="services: {}\n", profile="core")
    captured: dict[str, object] = {}

    class FakeSnapshotManager:
        def __init__(self, sudo_password: str | None = None) -> None:
            captured["manager_sudo_password"] = sudo_password

        def latest(self):
            return snap

        def get(self, snapshot_id: str):
            captured["snapshot_id"] = snapshot_id
            return snap

    def fake_rollback_to_snapshot(
        snapshot,
        install_dir: Path,
        sudo_password: str | None = None,
    ) -> bool:
        captured["snapshot"] = snapshot
        captured["install_dir"] = install_dir
        captured["restore_sudo_password"] = sudo_password
        return True

    monkeypatch.setattr(runner, "SnapshotManager", FakeSnapshotManager)
    monkeypatch.setattr(runner, "_rollback_to_snapshot", fake_rollback_to_snapshot)

    result = runner.rollback(
        snapshot_id="manual-id",
        install_dir=tmp_path / "install",
        sudo_password="pw",
    )

    assert result.success
    assert captured["manager_sudo_password"] == "pw"
    assert captured["snapshot_id"] == "manual-id"
    assert captured["restore_sudo_password"] == "pw"


# ---------- G.1: destructive --apply confirmation gate (no_prompt) ----------


def _gate_render_with_removal() -> str:
    """Rendered compose dropping a service so the diff has a non-empty `removed`."""
    return "services:\n  keeper:\n    image: keeper:1\n"


def _gate_install_dir_with_removed_service(tmp_path: Path) -> Path:
    """Install dir whose current compose has a service that the render removes."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "docker-compose.yml").write_text(
        "services:\n  keeper:\n    image: keeper:1\n  doomed:\n    image: doomed:1\n",
        encoding="utf-8",
    )
    return install_dir


class _StubSnapshot:
    id = "stub-snapshot"


class _NoopSnapshotManager:
    """Stub snapshot manager so proceed-path gate tests never touch the real store."""

    def __init__(self, sudo_password: str | None = None) -> None:
        self.sudo_password = sudo_password

    def save(self, **_kwargs: object) -> object:
        return _StubSnapshot()


def test_deploy_apply_prompts_before_destructive_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diff with removals + no_prompt=False asks typer.confirm before mutating."""
    install_dir = _gate_install_dir_with_removed_service(tmp_path)
    confirm_calls: list[str] = []

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: _gate_render_with_removal())

    def fake_confirm(prompt: str, *args: object, **kwargs: object) -> bool:
        confirm_calls.append(prompt)
        return False

    def fail_validate(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise AssertionError("validation must not run after a declined confirmation")

    def fail_write_text_maybe_sudo(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compose file must not be written after a declined confirmation")

    def fail_run_compose(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        raise AssertionError("docker compose must not run after a declined confirmation")

    class FailingSnapshotManager:
        def __init__(self, sudo_password: str | None = None) -> None:
            self.sudo_password = sudo_password

        def save(self, **_kwargs: object) -> object:
            raise AssertionError("snapshot must not run after a declined confirmation")

    original_mkdir = Path.mkdir

    def guard_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == install_dir:
            raise AssertionError(
                "install dir must not be (re)created after a declined confirmation"
            )
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(runner.typer, "confirm", fake_confirm)
    monkeypatch.setattr(runner, "_validate_compose_config", fail_validate)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fail_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "_run_compose", fail_run_compose)
    monkeypatch.setattr(runner, "SnapshotManager", FailingSnapshotManager)
    monkeypatch.setattr(Path, "mkdir", guard_mkdir)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=False,
    )

    assert len(confirm_calls) == 1
    assert result.success is False
    assert result.diff is not None
    assert "doomed" in result.diff.removed
    # Aborting leaves the current compose untouched (zero mutating calls).
    assert (install_dir / "docker-compose.yml").read_text(encoding="utf-8") == (
        "services:\n  keeper:\n    image: keeper:1\n  doomed:\n    image: doomed:1\n"
    )


def test_deploy_apply_no_prompt_bypasses_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """no_prompt=True (CI/Ansible/upgrade-apply) never calls typer.confirm and proceeds."""
    install_dir = _gate_install_dir_with_removed_service(tmp_path)
    writes: list[Path] = []

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: _gate_render_with_removal())
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_a, **_k: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    def fail_confirm(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("typer.confirm must NOT be called when no_prompt=True")

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append(path)

    monkeypatch.setattr(runner.typer, "confirm", fail_confirm)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "SnapshotManager", _NoopSnapshotManager)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=True,
    )

    assert result.success
    assert install_dir / "docker-compose.yml" in writes


def test_deploy_apply_confirm_yes_proceeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A destructive diff + no_prompt=False + confirm->True reaches the mutating path."""
    install_dir = _gate_install_dir_with_removed_service(tmp_path)
    writes: list[Path] = []
    confirm_calls: list[str] = []

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: _gate_render_with_removal())
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_a, **_k: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    def fake_confirm(prompt: str, *args: object, **kwargs: object) -> bool:
        confirm_calls.append(prompt)
        return True

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append(path)

    monkeypatch.setattr(runner.typer, "confirm", fake_confirm)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "SnapshotManager", _NoopSnapshotManager)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=False,
    )

    assert len(confirm_calls) == 1
    assert result.success
    assert install_dir / "docker-compose.yml" in writes


def test_deploy_apply_non_destructive_diff_does_not_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diff with NO removals proceeds without prompting (LOCKED minimal predicate)."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "docker-compose.yml").write_text(
        "services:\n  keeper:\n    image: keeper:1\n",
        encoding="utf-8",
    )
    writes: list[Path] = []

    # Image bump only — added/changed, no removals.
    monkeypatch.setattr(
        runner,
        "render_to_string",
        lambda **_kwargs: "services:\n  keeper:\n    image: keeper:2\n",
    )
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_a, **_k: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    def fail_confirm(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("typer.confirm must NOT be called for a non-destructive diff")

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append(path)

    monkeypatch.setattr(runner.typer, "confirm", fail_confirm)
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo)
    monkeypatch.setattr(runner, "SnapshotManager", _NoopSnapshotManager)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=False,
    )

    assert result.success
    assert install_dir / "docker-compose.yml" in writes


# ---------- D-05b: no-op apply still reconciles runtime (idempotent apply) ----------


def test_deploy_apply_reconciles_runtime_when_no_config_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply=True + has_changes=False must still run compose up + health (no lying "no changes")."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    rendered = "services:\n  postgres:\n    image: postgres:17.6-alpine\n"
    (install_dir / "docker-compose.yml").write_text(rendered, encoding="utf-8")

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "SnapshotManager", _NoopSnapshotManager)

    stream_calls: list[list[str]] = []

    def fake_stream_compose(
        args: list[str],
        cwd: Path,
        sudo_password: str | None = None,
        on_line: object = None,
        cancel_event: object = None,
    ) -> tuple[int, str]:
        stream_calls.append(args)
        return 0, ""

    monkeypatch.setattr(runner, "_stream_compose", fake_stream_compose)

    healthy_calls: list[object] = []

    def fake_wait_healthy(*args: object, **kwargs: object) -> tuple[bool, list[str]]:
        healthy_calls.append((args, kwargs))
        return True, []

    monkeypatch.setattr(runner, "_wait_healthy", fake_wait_healthy)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=True,
        services=["postgres"],
    )

    assert result.success
    assert result.diff is not None
    assert not result.diff.has_changes
    up_calls = [c for c in stream_calls if c[:2] == ["up", "-d"]]
    assert len(up_calls) == 1
    assert healthy_calls
    assert "reconciled" in result.message
    assert "unchanged" in result.message


def test_deploy_dry_run_no_changes_does_not_call_compose_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply=False (dry-run) with no changes still returns early — unchanged UX."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    rendered = "services:\n  postgres:\n    image: postgres:17.6-alpine\n"
    (install_dir / "docker-compose.yml").write_text(rendered, encoding="utf-8")

    monkeypatch.setattr(runner, "render_to_string", lambda **_kwargs: rendered)

    def fail_stream_compose(*_a: object, **_k: object) -> tuple[int, str]:
        raise AssertionError("compose up must not run on a dry-run no-op")

    monkeypatch.setattr(runner, "_stream_compose", fail_stream_compose)

    result = runner.deploy(
        profiles=["core"],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=False,
        services=["postgres"],
    )

    assert result.success
    assert result.message == "no changes — current deployment matches rendered"


def test_wait_healthy_accepts_compose_json_array(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker Compose v2 commonly emits one JSON array for ps --format json."""

    def fake_run_compose(args: list[str], cwd: Path) -> tuple[int, str, str]:
        assert args == ["ps", "--format", "json"]
        assert cwd == tmp_path
        return (
            0,
            json.dumps(
                [
                    {
                        "Service": "llama-llm",
                        "State": "running",
                        "Health": "healthy",
                    },
                    {
                        "Service": "qdrant",
                        "State": "running",
                        "Health": "",
                    },
                ]
            ),
            "",
        )

    monkeypatch.setattr(runner, "_run_compose", fake_run_compose)

    healthy, unhealthy = runner._wait_healthy(tmp_path, timeout=1)

    assert healthy
    assert unhealthy == []


def test_wait_healthy_returns_early_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The healthcheck poll (up to healthcheck_timeout, now 900s) is the longest part
    of a deploy. It must break out promptly when the cancel_event fires, instead of
    blocking the worker thread (and the TUI) for the full timeout."""
    import threading
    import time as _time

    # Always report a 'starting' container so the loop would otherwise run to timeout.
    monkeypatch.setattr(
        runner,
        "_run_compose_maybe_sudo",
        lambda *_a, **_k: (0, '[{"Service": "x", "Health": "starting", "State": "running"}]', ""),
    )
    ev = threading.Event()
    ev.set()  # already cancelled

    t0 = _time.monotonic()
    healthy, _unhealthy = runner._wait_healthy(Path("/tmp"), timeout=300, cancel_event=ev)
    elapsed = _time.monotonic() - t0

    assert elapsed < 5.0, f"_wait_healthy ignored cancel (took {elapsed:.1f}s)"
    assert healthy is False


def test_wait_healthy_flags_expected_service_with_no_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expected service that produced NO container must not pass as healthy (audit H#3)."""
    monkeypatch.setattr(
        runner,
        "_run_compose_maybe_sudo",
        lambda *_a, **_k: (0, '[{"Service": "a", "Health": "healthy", "State": "running"}]', ""),
    )
    monkeypatch.setattr(runner, "_interruptible_sleep", lambda *_a, **_k: True)  # bail fast
    healthy, unhealthy = runner._wait_healthy(Path("/x"), timeout=5, expected_services=["a", "b"])
    assert healthy is False
    assert any(u.startswith("b") for u in unhealthy)


def test_wait_healthy_empty_ps_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0-container `ps` while a service is expected must be unhealthy, never (True, [])."""
    monkeypatch.setattr(runner, "_run_compose_maybe_sudo", lambda *_a, **_k: (0, "[]", ""))
    monkeypatch.setattr(runner, "_interruptible_sleep", lambda *_a, **_k: True)
    healthy, unhealthy = runner._wait_healthy(
        Path("/x"), timeout=5, expected_services=["llama-llm"]
    )
    assert healthy is False
    assert unhealthy


def test_rollback_up_uses_pull_never_when_offline(
    tmp_path: Path, snapshot_mgr: SnapshotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Air-gap rollback must NOT network-pull: resolve_pull_policy() → never (audit H#9)."""
    monkeypatch.setenv("AGMIND_OFFLINE", "1")
    snap = snapshot_mgr.save(
        compose_text="services:\n  llama-llm:\n    image: llama:1\n", profile="core"
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    calls: list[list[str]] = []

    def fake_stream(args, cwd, sudo_password=None, on_line=None, cancel_event=None):  # noqa: ANN001
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(runner, "_stream_compose", fake_stream)
    assert runner._rollback_to_snapshot(snap, install_dir)
    assert calls == [["up", "-d", "--remove-orphans", "--pull", "never", "llama-llm"]]
