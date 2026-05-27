"""Phase M3.Q: tests for `agmind models` standalone CLI subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.cli import models_cmd

pytestmark = pytest.mark.backend_any


@pytest.fixture
def models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setattr(models_cmd, "_models_dir", lambda: d)
    return d


def _write_blob(p: Path, size_mb: int = 1) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * (size_mb * 1024 * 1024))


# ---------- cmd_list_local ----------


def test_list_local_empty(models_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = models_cmd.cmd_list_local()
    assert rc == 0
    assert "empty" in capsys.readouterr().out.lower()


def test_list_local_with_files(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_blob(models_dir / "alpha.gguf", size_mb=10)
    _write_blob(models_dir / "beta.gguf", size_mb=20)
    rc = models_cmd.cmd_list_local()
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha.gguf" in out
    assert "beta.gguf" in out
    assert "2 files" in out


def test_list_local_json(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_blob(models_dir / "x.gguf", size_mb=5)
    rc = models_cmd.cmd_list_local(as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["name"] == "x.gguf"
    assert data[0]["size_bytes"] > 0


def test_list_local_skips_non_model_files(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (models_dir / "readme.txt").write_text("ignore me")
    _write_blob(models_dir / "real.gguf", size_mb=2)
    rc = models_cmd.cmd_list_local()
    assert rc == 0
    out = capsys.readouterr().out
    assert "real.gguf" in out
    assert "readme.txt" not in out


def test_list_local_reports_iterdir_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_iterdir = Path.iterdir

    def fake_iterdir(self: Path):
        if self == models_dir:
            raise PermissionError("models dir denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    rc = models_cmd.cmd_list_local()

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to list models" in err
    assert "models dir denied" in err
    assert "Traceback" not in err


def test_list_local_reports_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "locked.gguf"
    _write_blob(target, size_mb=1)
    original_stat = Path.stat

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_list_local()

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to inspect model file" in err
    assert "locked.gguf" in err
    assert "stat denied" in err
    assert "Traceback" not in err


# ---------- cmd_info ----------


def test_info_curated_id(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = models_cmd.cmd_info(model_id="qwen36-a3b-q4km")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Qwen3.6" in out
    assert "21.2" in out
    assert "★ Strix Halo" in out


def test_info_unknown_id(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = models_cmd.cmd_info(model_id="bogus")
    assert rc == 1


def test_info_local_file(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_blob(models_dir / "my.gguf", size_mb=10)
    rc = models_cmd.cmd_info(file="my.gguf")
    assert rc == 0
    out = capsys.readouterr().out
    assert "my.gguf" in out
    assert "Size" in out


def test_info_local_file_missing(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = models_cmd.cmd_info(file="nope.gguf")
    assert rc == 2


def test_info_local_file_reports_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "locked.gguf"
    _write_blob(target, size_mb=1)
    original_stat = Path.stat

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_info(file="locked.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to inspect" in err
    assert "stat denied" in err
    assert "Traceback" not in err


def test_info_requires_id_or_file(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = models_cmd.cmd_info()
    assert rc == 2


# ---------- cmd_download ----------


def test_download_reports_models_dir_mkdir_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == models_dir:
            raise PermissionError("mkdir denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    rc = models_cmd.cmd_download(tier="L")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to prepare models dir" in err
    assert "mkdir denied" in err
    assert "Traceback" not in err


def test_download_reports_existing_file_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "Qwen3.6-35B-A3B-DYNAMIC.gguf"
    _write_blob(target, size_mb=1)
    original_exists = Path.exists
    original_stat = Path.stat

    def fake_exists(self: Path) -> bool:
        if self == target:
            return True
        return original_exists(self)

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_download(tier="L")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to inspect existing model" in err
    assert "stat denied" in err
    assert "Traceback" not in err


# ---------- cmd_verify ----------


def test_verify_reports_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "Qwen3.6-35B-A3B-DYNAMIC.gguf"
    _write_blob(target, size_mb=1)
    original_stat = Path.stat

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_verify(tier="L")

    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR" in out
    assert "stat denied" in out
    assert "Traceback" not in out


# ---------- cmd_pull ----------


def test_pull_skip_if_present(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    _write_blob(target, size_mb=5)
    rc = models_cmd.cmd_pull(model_id="qwen36-a3b-q4km")
    assert rc == 0
    assert "already present" in capsys.readouterr().out


def test_pull_reports_existing_file_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "locked.gguf"
    _write_blob(target, size_mb=1)
    original_exists = Path.exists
    original_stat = Path.stat

    def fake_exists(self: Path) -> bool:
        if self == target:
            return True
        return original_exists(self)

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_pull(repo="example/repo", file="locked.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to inspect existing model" in err
    assert "stat denied" in err
    assert "Traceback" not in err


def test_pull_unknown_id(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = models_cmd.cmd_pull(model_id="bogus")
    assert rc == 1


def test_pull_requires_id_or_repo_file(models_dir: Path) -> None:
    rc = models_cmd.cmd_pull()
    assert rc == 2


def test_pull_reports_models_dir_mkdir_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == models_dir:
            raise PermissionError("mkdir denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    rc = models_cmd.cmd_pull(repo="example/repo", file="model.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to prepare models dir" in err
    assert "mkdir denied" in err
    assert "Traceback" not in err


def test_pull_curated_invokes_curl(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    class FakeProc:
        returncode = 0

    def fake_run(cmd, **kw):
        captured.append(cmd)
        # Simulate download — write 5 MB blob
        target_idx = cmd.index("-o") + 1
        _write_blob(Path(cmd[target_idx]), size_mb=5)
        return FakeProc()

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr("subprocess.run", fake_run)
    rc = models_cmd.cmd_pull(model_id="qwen36-a3b-q4km", force=True)
    assert rc == 0
    assert captured[0][0] == "curl"
    assert "huggingface.co/0xSero/Qwen3.6-35B-A3B-GGUF-Strix" in " ".join(captured[0])


def test_pull_reports_curl_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(*args: object, **kwargs: object) -> object:
        raise PermissionError("curl denied")

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr("subprocess.run", fake_run)

    rc = models_cmd.cmd_pull(repo="example/repo", file="model.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "curl failed" in err
    assert "curl denied" in err
    assert "Traceback" not in err
    assert not (models_dir / "model.gguf").exists()


# ---------- cmd_rm ----------


def test_rm_by_file(
    models_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "rmme.gguf"
    _write_blob(target, size_mb=3)
    rc = models_cmd.cmd_rm(file="rmme.gguf")
    assert rc == 0
    assert not target.exists()
    assert "removed" in capsys.readouterr().out


def test_rm_by_curated_id(models_dir: Path) -> None:
    target = models_dir / "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    _write_blob(target, size_mb=3)
    rc = models_cmd.cmd_rm(model_id="qwen36-a3b-q4km")
    assert rc == 0
    assert not target.exists()


def test_rm_unknown_id(models_dir: Path) -> None:
    rc = models_cmd.cmd_rm(model_id="bogus")
    assert rc == 1


def test_rm_warns_if_in_use(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    target = models_dir / "running.gguf"
    _write_blob(target, size_mb=3)

    # Mock /opt/agmind/.env to contain reference
    fake_env = tmp_path / ".env"
    fake_env.write_text("AGMIND_MODEL_FILE=running.gguf\n")
    monkeypatch.setattr(
        "agmind.cli.models_cmd.Path",
        lambda p: fake_env if p == "/opt/agmind/.env" else Path(p),
    )

    rc = models_cmd.cmd_rm(file="running.gguf")
    assert rc == 1
    out = capsys.readouterr().err
    assert "referenced" in out or "WARNING" in out
    # File should NOT be deleted
    assert target.exists()


def test_rm_force_deletes_even_if_in_use(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = models_dir / "running.gguf"
    _write_blob(target, size_mb=3)
    fake_env = tmp_path / ".env"
    fake_env.write_text("AGMIND_MODEL_FILE=running.gguf\n")
    monkeypatch.setattr(
        "agmind.cli.models_cmd.Path",
        lambda p: fake_env if p == "/opt/agmind/.env" else Path(p),
    )

    rc = models_cmd.cmd_rm(file="running.gguf", force=True)
    assert rc == 0
    assert not target.exists()


def test_rm_reports_unlink_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "locked.gguf"
    _write_blob(target, size_mb=1)
    original_unlink = Path.unlink

    def fake_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise PermissionError("unlink denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    rc = models_cmd.cmd_rm(file="locked.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to remove" in err
    assert "unlink denied" in err
    assert "Traceback" not in err
    assert target.exists()


def test_rm_reports_stat_oserror_without_traceback(
    models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = models_dir / "locked.gguf"
    _write_blob(target, size_mb=1)
    original_exists = Path.exists
    original_stat = Path.stat

    def fake_exists(self: Path) -> bool:
        if self == target:
            return True
        return original_exists(self)

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("stat denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "stat", fake_stat)

    rc = models_cmd.cmd_rm(file="locked.gguf")

    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to inspect model" in err
    assert "stat denied" in err
    assert "Traceback" not in err
    assert target.exists()


def test_rm_requires_arg(models_dir: Path) -> None:
    rc = models_cmd.cmd_rm()
    assert rc == 2
