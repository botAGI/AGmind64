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
    assert partial.read_bytes() == b"too small"


def test_skip_step_if_no_model_configured(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.model_repo = None
    cfg.model_file = None
    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success
    assert "skip" in result.message.lower()
