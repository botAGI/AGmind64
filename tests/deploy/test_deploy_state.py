"""Tests for `agmind.deploy.state` — deploy-state.json паспорт установки (D-01, Phase 13.B)."""

from __future__ import annotations

import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agmind.deploy import state as state_module
from agmind.deploy.state import DeployState, load_deploy_state, write_deploy_state

pytestmark = pytest.mark.backend_any


def _make_state(**overrides: object) -> DeployState:
    fields: dict[str, object] = dict(
        agmind_version="1.2.3",
        profiles=["core", "rag"],
        requested_services=["qdrant"],
        resolved_services=["postgres", "qdrant"],
        domain="lab.example.com",
        edge_mode="lan",
        written_at=datetime.now(UTC).isoformat(),
    )
    fields.update(overrides)
    return DeployState(**fields)


def test_round_trips_through_json() -> None:
    state = _make_state()
    restored = DeployState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_forward_compat_ignores_unknown_keys() -> None:
    state = _make_state()
    data = state.model_dump()
    data["future_field"] = 1
    restored = DeployState.model_validate(data)
    assert not hasattr(restored, "future_field")
    assert restored == state


def test_edge_mode_rejects_invalid_value() -> None:
    data = _make_state().model_dump()
    data["edge_mode"] = "bogus"
    with pytest.raises(ValidationError):
        DeployState.model_validate(data)


def test_new_stamps_utc_iso_written_at() -> None:
    state = DeployState.new(
        agmind_version="1.2.3",
        profiles=["core"],
        requested_services=["qdrant"],
        resolved_services=["qdrant"],
        domain=None,
        edge_mode="local",
    )
    assert "+00:00" in state.written_at or state.written_at.endswith("Z")


def test_domain_none_and_config_hash_default() -> None:
    state = DeployState(
        agmind_version="1.2.3",
        profiles=[],
        requested_services=[],
        resolved_services=[],
        domain=None,
        edge_mode="local",
        written_at=datetime.now(UTC).isoformat(),
    )
    assert state.domain is None
    assert state.config_hash == ""


# ---------- write_deploy_state / load_deploy_state ----------


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    state = DeployState.new(
        agmind_version="1.2.3",
        profiles=["core"],
        requested_services=["qdrant"],
        resolved_services=["qdrant"],
        domain="lab.example.com",
        edge_mode="lan",
    )
    write_deploy_state(tmp_path, state)
    path = tmp_path / "deploy-state.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    loaded = load_deploy_state(tmp_path)
    assert loaded == state


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_deploy_state(tmp_path) is None


def test_load_corrupt_returns_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "deploy-state.json").write_text("{not valid json", encoding="utf-8")
    assert load_deploy_state(tmp_path) is None
    captured = capsys.readouterr()
    assert "deploy state" in captured.err.lower()


def test_write_reraises_permission_error_without_sudo_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_write_text_atomic(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(state_module, "write_text_atomic", fake_write_text_atomic)
    state = DeployState.new(
        agmind_version="1.2.3",
        profiles=[],
        requested_services=[],
        resolved_services=[],
        domain=None,
        edge_mode="local",
    )
    with pytest.raises(PermissionError):
        write_deploy_state(tmp_path, state)


def test_write_falls_back_to_sudo_install_when_password_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_write_text_atomic(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    calls: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(state_module, "write_text_atomic", fake_write_text_atomic)
    monkeypatch.setattr(state_module.subprocess, "run", fake_run)

    state = DeployState.new(
        agmind_version="1.2.3",
        profiles=[],
        requested_services=[],
        resolved_services=[],
        domain=None,
        edge_mode="local",
    )
    write_deploy_state(tmp_path, state, sudo_password="s3cr3t")

    argv = calls["argv"]
    assert argv[0] == "sudo"
    assert "install" in argv
    assert "-D" in argv
    assert "-m" in argv
    assert "0644" in argv
    assert str(tmp_path / "deploy-state.json") == argv[-1]
    assert calls["kwargs"]["input"] == "s3cr3t\n"
