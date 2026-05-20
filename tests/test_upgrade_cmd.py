"""Phase M3.R: tests for `agmind upgrade` lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agmind.cli import upgrade_cmd

pytestmark = pytest.mark.backend_any


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create temp template/services + holds + state dirs, patch paths."""
    services = tmp_path / "templates" / "services"
    services.mkdir(parents=True)
    state_dir = tmp_path / "state"

    monkeypatch.setattr(upgrade_cmd, "SERVICES_DIR", services)
    monkeypatch.setattr(upgrade_cmd, "UPGRADE_STATE_DIR", state_dir)
    monkeypatch.setattr(upgrade_cmd, "HOLDS_FILE", tmp_path / "holds.yaml")
    return tmp_path


def _make_descriptor(services_dir: Path, name: str, image: str, tag: str,
                     digest: str | None = None) -> Path:
    yaml_path = services_dir / f"{name}.yaml"
    lines = [
        f"name: {name}",
        f"image: {image}:{tag}" + (f"@sha256:{digest}" if digest else ""),
        f"tier: storage",
        f"purpose: test",
        f"profiles:",
        f"- test",
    ]
    if digest:
        lines.insert(2, f"digest: {digest}")
    yaml_path.write_text("\n".join(lines) + "\n")
    return yaml_path


# ---------- _read_current_pin ----------


def test_read_current_pin_no_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "alpha", "vendor/alpha", "1.2.3")
    result = upgrade_cmd._read_current_pin(p)
    assert result == ("vendor/alpha", "1.2.3", None)


def test_read_current_pin_with_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "beta", "vendor/beta", "v0.1",
                         digest="abc123" + "0" * 58)
    result = upgrade_cmd._read_current_pin(p)
    assert result == ("vendor/beta", "v0.1", "abc123" + "0" * 58)


# ---------- _bump_pin_in_yaml ----------


def test_bump_pin_replaces_image_tag(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0.0")
    old, new = upgrade_cmd._bump_pin_in_yaml(p, "2.0.0")
    assert old == "1.0.0"
    assert new == "2.0.0"
    assert "image: vendor/x:2.0.0" in p.read_text()


def test_bump_pin_updates_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0", digest="a" * 64)
    new_digest = "b" * 64
    upgrade_cmd._bump_pin_in_yaml(p, "2.0", new_digest=new_digest)
    text = p.read_text()
    assert f"vendor/x:2.0@sha256:{new_digest}" in text
    assert f"digest: {new_digest}" in text


def test_bump_pin_adds_digest_if_absent(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0")  # no digest
    new_digest = "c" * 64
    upgrade_cmd._bump_pin_in_yaml(p, "2.0", new_digest=new_digest)
    text = p.read_text()
    assert f"digest: {new_digest}" in text


# ---------- cmd_component ----------


def test_component_bumps_tag(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")
    assert rc == 0
    out = capsys.readouterr().out
    assert "1.0.0 → vendor/alpha:1.0.1" in out
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.1" in yaml


def test_component_unknown_service(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = upgrade_cmd.cmd_component("ghost", "1.0.0")
    assert rc == 1


def test_component_noop_if_already_at_target(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    rc = upgrade_cmd.cmd_component("alpha", "1.0.0")
    assert rc == 0
    assert "already at" in capsys.readouterr().out.lower()


def test_component_respects_holds_without_force(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.HOLDS_FILE.write_text(
        "vendor/alpha:\n  reason: 'pinned for compat'\n",
    )
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")
    assert rc == 1
    err = capsys.readouterr().err
    assert "HELD" in err
    assert "compat" in err
    # Tag unchanged
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.0" in yaml


def test_component_force_bypasses_holds(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.HOLDS_FILE.write_text(
        "vendor/alpha:\n  reason: 'pinned'\n",
    )
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1", force=True)
    assert rc == 0
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.1" in yaml


def test_component_saves_upgrade_state(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "1.0.1")

    state_files = list(upgrade_cmd.UPGRADE_STATE_DIR.glob("*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text())
    assert state["service"] == "alpha"
    assert state["old_tag"] == "1.0.0"
    assert state["new_tag"] == "1.0.1"


# ---------- cmd_rollback ----------


def test_rollback_restores_old_tag(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "2.0.0")
    # Verify bumped
    assert "vendor/alpha:2.0.0" in (services / "alpha.yaml").read_text()

    rc = upgrade_cmd.cmd_rollback()
    assert rc == 0
    # Restored к 1.0.0
    assert "vendor/alpha:1.0.0" in (services / "alpha.yaml").read_text()


def test_rollback_no_state_returns_error(
    tmp_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = upgrade_cmd.cmd_rollback()
    assert rc == 1


def test_rollback_archives_state_file(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "2.0.0")
    upgrade_cmd.cmd_rollback()
    # Original state file moved to rolled_back/
    archived = list((upgrade_cmd.UPGRADE_STATE_DIR / "rolled_back").glob("*.json"))
    assert len(archived) == 1


# ---------- cmd_check ----------


def test_check_invokes_version_check_script(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cmd_check вызывает scripts/version_check.py через subprocess."""
    called: list[list[str]] = []

    class P:
        returncode = 0

    def fake_run(cmd, check):  # noqa: ARG001
        called.append(cmd)
        return P()

    monkeypatch.setattr("subprocess.run", fake_run)
    # Point script at real one (existing in repo)
    monkeypatch.setattr(upgrade_cmd, "REPO_ROOT", Path(__file__).resolve().parents[1])

    rc = upgrade_cmd.cmd_check()
    assert rc == 0
    assert any("version_check.py" in s for s in called[0])
