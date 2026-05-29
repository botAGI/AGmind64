"""Phase N.H: tests for ModelDownloadStep detect/reuse logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agmind.install.orchestrator import InstallConfig, ProgressEvent
from agmind.install.steps import ModelDownloadStep

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path, file_name: str = "Qwen.gguf") -> InstallConfig:
    return InstallConfig(
        domain="x.example",
        cf_api_token="t" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "prod_models",
        model_repo="user/repo",
        model_file=file_name,
        sudo_password="pw",
    )


def _make_blob(path: Path, size_mb: int = 200) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * (size_mb * 1024 * 1024))


def test_skip_download_if_model_in_models_dir(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    _make_blob(target, size_mb=150)

    events: list[ProgressEvent] = []
    result = ModelDownloadStep().run(events.append, cfg)
    assert result.success
    assert "reused" in result.message.lower()
    assert target.exists()


def test_skip_if_too_small_blob_present(tmp_path: Path) -> None:
    """Файл < MIN_VALID_SIZE = не считается reusable."""
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    _make_blob(target, size_mb=1)  # too small

    # Now mock curl to avoid network — assert we DO attempt to download
    with patch("agmind.install.steps._stream_subprocess") as m:
        m.return_value = (0, [])

        # Make the file "appear" after download
        def fake_run(cmd, *args, **kwargs):
            del args, kwargs
            output = Path(cmd[cmd.index("-o") + 1])
            _make_blob(output, size_mb=200)
            return (0, [])

        m.side_effect = fake_run
        result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert "downloaded" in result.message.lower()


def test_relocate_from_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель в ~/.local/share/agmind/models/ → move into models_dir, skip download."""
    cfg = _cfg(tmp_path)
    fallback_dir = tmp_path / "fake_home_local_share" / "agmind" / "models"
    fallback_blob = fallback_dir / cfg.model_file
    _make_blob(fallback_blob, size_mb=200)

    # Patch _fallback_dirs чтобы вернуло наш fake fallback (без real $HOME)
    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: [fallback_dir]),
    )

    events: list[ProgressEvent] = []
    result = ModelDownloadStep().run(events.append, cfg)
    assert result.success
    assert "relocate" in result.message.lower() or "from fallback" in result.message.lower()
    # File moved to expected place
    assert (cfg.models_dir / cfg.model_file).exists()
    assert not fallback_blob.exists()


def test_relocate_from_fallback_removes_partial_target_on_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    fallback_dir = tmp_path / "fake_home_local_share" / "agmind" / "models"
    fallback_blob = fallback_dir / cfg.model_file
    _make_blob(fallback_blob, size_mb=200)
    target = cfg.models_dir / cfg.model_file

    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: [fallback_dir]),
    )

    def fail_move(src: str, dst: str) -> None:
        del src, dst
        raise OSError("cross-device link")

    def fail_copy2(src: str, dst: str) -> None:
        del src
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr("agmind.install.steps.shutil.move", fail_move)
    monkeypatch.setattr("agmind.install.steps.shutil.copy2", fail_copy2)

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert not result.success
    assert "cannot relocate model" in result.message
    assert fallback_blob.exists()
    assert not target.exists()
    assert not target.with_name(f".{target.name}.tmp").exists()


def test_skip_relocate_if_target_already_correct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если model уже в models_dir — игнорируем fallback (default wins)."""
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    _make_blob(target, size_mb=200)
    fallback = tmp_path / "fb"
    _make_blob(fallback / cfg.model_file, size_mb=200)

    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: [fallback]),
    )

    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert "reused" in result.message.lower()
    # Fallback file untouched
    assert (fallback / cfg.model_file).exists()


def test_download_called_when_nowhere_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: []),
    )

    captured_cmd: list[list[str]] = []

    def fake_stream(cmd, callback, step_id, **kw):
        captured_cmd.append(cmd)
        # simulate download — write a 200 MB file
        output = Path(cmd[cmd.index("-o") + 1])
        _make_blob(output, size_mb=200)
        return (0, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert len(captured_cmd) == 1
    assert captured_cmd[0][0] == "curl"
    assert "huggingface.co/user/repo" in " ".join(captured_cmd[0])


def test_download_failure_keeps_partial_file_out_of_final_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    partial = target.with_name(f".{target.name}.part")
    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: []),
    )

    def fake_stream(cmd, callback, step_id, **kw):
        del callback, step_id, kw
        assert cmd[cmd.index("-o") + 1] == str(partial)
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(b"partial model")
        return (18, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert not result.success
    assert "curl rc=18" in result.message
    assert not target.exists()
    assert partial.read_bytes() == b"partial model"


def test_download_moves_too_small_final_target_to_partial_before_curl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    partial = target.with_name(f".{target.name}.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old partial")
    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: []),
    )

    def fake_stream(cmd, callback, step_id, **kw):
        del callback, step_id, kw
        assert cmd[cmd.index("-o") + 1] == str(partial)
        assert not target.exists()
        assert partial.read_bytes() == b"old partial"
        return (18, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert not result.success
    assert "curl rc=18" in result.message
    assert not target.exists()
    assert partial.read_bytes() == b"old partial"


def test_curl_command_has_network_timeout_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """curl must carry connect/stall timeouts. The download runs in an uncancellable
    thread-worker; a half-open HF socket with no --connect-timeout/--speed-time hangs
    the read loop forever and freezes the TUI. These flags make curl exit non-zero on
    a stall, which the step already handles cleanly."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(ModelDownloadStep, "_fallback_dirs", staticmethod(lambda _c: []))
    captured_cmd: list[list[str]] = []

    def fake_stream(cmd, callback, step_id, **kw):
        captured_cmd.append(cmd)
        output = Path(cmd[cmd.index("-o") + 1])
        _make_blob(output, size_mb=200)
        return (0, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    cmd = captured_cmd[0]
    assert "--connect-timeout" in cmd
    assert "--speed-limit" in cmd
    assert "--speed-time" in cmd


def test_download_fails_cleanly_when_curl_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If curl is absent the step must fail with a clear, actionable message BEFORE
    attempting a download — not a deep rc=127 OSError mid-run."""
    import agmind.install.steps as steps_mod

    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    _make_blob(target, size_mb=1)  # too small -> would trigger a download
    monkeypatch.setattr(ModelDownloadStep, "_fallback_dirs", staticmethod(lambda _c: []))
    monkeypatch.setattr(
        steps_mod.shutil,
        "which",
        lambda name: None if name == "curl" else f"/usr/bin/{name}",
    )

    called: list[object] = []

    def fake_stream(*args: object, **kwargs: object) -> tuple[int, list[str]]:
        called.append(args)
        return (0, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert not result.success
    assert "curl" in result.message.lower()
    assert called == []  # never attempted the download


def test_download_success_rejects_too_small_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.model_file
    partial = target.with_name(f".{target.name}.part")
    monkeypatch.setattr(
        ModelDownloadStep,
        "_fallback_dirs",
        staticmethod(lambda _c: []),
    )

    def fake_stream(cmd, callback, step_id, **kw):
        del callback, step_id, kw
        output = Path(cmd[cmd.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"too small")
        return (0, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert not result.success
    assert "downloaded file too small" in result.message
    assert not target.exists()
    # curl reported success (rc=0) but produced a too-small file — that is poison for a
    # later `curl -C -` resume (the real "100% then 0 MiB" failure). It MUST be cleared
    # so the next retry starts clean instead of resuming from garbage.
    assert not partial.exists()


def test_skip_step_if_no_model_configured(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.model_repo = None
    cfg.model_file = None
    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert "skip" in result.message.lower()


def test_ensure_models_dir_skips_sudo_when_writable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If models_dir is already user-writable, no sudo is needed."""
    from agmind.install import steps

    cfg = _cfg(tmp_path)  # models_dir under a writable tmp tree
    sudo_calls: list[object] = []
    monkeypatch.setattr(steps, "_run_sudo_runtime_command", lambda *a, **k: sudo_calls.append(a))
    steps._ensure_models_dir(cfg, lambda _e: None, "model_pull")
    assert cfg.models_dir.is_dir()
    assert sudo_calls == []


def test_ensure_models_dir_creates_via_sudo_when_parent_root_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models_dir lives under a root-owned system path (/var/lib/agmind). A plain
    user-level mkdir raises [Errno 13]; the step must create+chown it via sudo
    instead of crashing the install (BREA01 — the real model_pull failure)."""
    import os

    if os.getuid() == 0:
        pytest.skip("permission semantics don't apply when running as root")

    from agmind.install import steps
    from agmind.install.orchestrator import InstallConfig

    sysdata = tmp_path / "sysdata"
    sysdata.mkdir()
    models_dir = sysdata / "models"
    cfg = InstallConfig(
        domain="x.example",
        cf_api_token="t" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
        models_dir=models_dir,
        sudo_password="pw",
    )
    sysdata.chmod(0o555)  # read-only parent -> user mkdir(models) raises PermissionError

    recorded: list[list[str]] = []

    def fake_sudo(config: object, cmd: list[str], callback: object, step_id: object) -> None:
        recorded.append(cmd)
        sysdata.chmod(0o755)  # emulate sudo's privilege
        models_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(steps, "_run_sudo_runtime_command", fake_sudo)
    try:
        steps._ensure_models_dir(cfg, lambda _e: None, "model_pull")
    finally:
        sysdata.chmod(0o755)  # so tmp cleanup can remove it

    assert recorded, "sudo was not used to create the root-owned models dir"
    assert recorded[0][:2] == ["install", "-d"]
    assert str(models_dir) in recorded[0]
    assert models_dir.is_dir()


def test_model_download_run_prepares_models_dir_before_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() must prepare (ensure-writable) the models dir before any download so the
    root-owned-parent case is handled up front rather than crashing mid-download."""
    from agmind.install import steps

    cfg = _cfg(tmp_path)
    order: list[str] = []
    monkeypatch.setattr(steps, "_ensure_models_dir", lambda c, cb, sid: order.append("ensure"))
    monkeypatch.setattr(
        ModelDownloadStep,
        "_download_one",
        lambda self, role, repo, fn, config, cb: (order.append(f"dl:{role}"), (True, f"{role} ok"))[
            1
        ],
    )

    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert order[0] == "ensure", f"models dir not prepared before downloads: {order}"
