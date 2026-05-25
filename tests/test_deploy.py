"""Phase L.B: tests for agmind.deploy (snapshot + diff + runner)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agmind.deploy import runner
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
    assert "AGMIND_DOMAIN" in snap.env_file.read_text(encoding="utf-8")


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
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "check": check,
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
        }
    ]


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
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_args: (True, []))

    def fake_run_compose(args: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(runner, "_run_compose", fake_run_compose)

    result = runner.deploy(
        profiles=["core"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
    )

    assert result.success
    assert calls == [["up", "-d", "--remove-orphans", "--quiet-pull", "llama-llm", "qdrant"]]


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
