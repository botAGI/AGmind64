"""Tests для scripts/audit_forbidden.py — поведение RULES + opt-out logic.

Запускается из tests/ — путь к скрипту относительно repo root.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AUDIT_SCRIPT = _REPO_ROOT / "scripts" / "audit_forbidden.py"


def _run_audit(target: Path, *extra: str, fail: bool = False) -> tuple[int, str]:
    cmd = [sys.executable, str(_AUDIT_SCRIPT), str(target), *extra]
    if fail:
        cmd.append("--fail")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout + result.stderr


def test_audit_script_exists() -> None:
    assert _AUDIT_SCRIPT.exists()


def test_audit_clean_file(tmp_path: Path) -> None:
    """Чистый Python-файл — 0 находок."""
    f = tmp_path / "clean.py"
    f.write_text("def hello():\n    return 42\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 0
    assert "Находок:          0" in out


def test_audit_detects_cuda_runtime(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("import cublas\nx = cudaMalloc(1024)\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "cuda_runtime" in out


def test_audit_detects_torch_cuda_call(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("tensor.cuda()\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "cuda_python" in out


def test_audit_detects_nvcr_path(tmp_path: Path) -> None:
    f = tmp_path / "compose.yml"
    f.write_text("image: nvcr.io/nvidia/pytorch:latest\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "cuda_paths" in out


def test_audit_detects_aarch64(tmp_path: Path) -> None:
    f = tmp_path / "build.sh"
    f.write_text("docker build --platform=linux/arm64 .\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "arm_aarch64" in out


def test_audit_detects_nvidia_hw_names(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    f.write_text("Tested on NVIDIA H100.\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "nvidia_hw" in out


def test_audit_detects_native_march(tmp_path: Path) -> None:
    f = tmp_path / "Dockerfile"
    f.write_text("ARG CFLAGS=-march=native\n")
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 1
    assert "native_march" in out


def test_audit_allow_marker_suppresses_finding(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text('msg = "torch.cuda. legacy ref"  # audit: allow rule-self-reference\n')
    code, out = _run_audit(tmp_path, fail=True)
    assert code == 0
    assert "Находок:          0" in out


def test_audit_legacy_dir_excluded(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "bad.py").write_text("import cublas\n")
    code, _ = _run_audit(tmp_path, fail=True)
    assert code == 0  # legacy/ excluded


def test_audit_json_output(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("import cupy\n")
    out_json = tmp_path / "out.json"
    code, _ = _run_audit(tmp_path, "--json", str(out_json), fail=False)
    assert out_json.exists()
    data = json.loads(out_json.read_text())
    assert data["scanned_files"] >= 1
    assert len(data["findings"]) >= 1
    finding = data["findings"][0]
    assert finding["rule"] == "cuda_python"
    assert "line" in finding


def test_audit_text_extensions_only(tmp_path: Path) -> None:
    """Binary файлы скан не проводится."""
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00cublas\xff\xfe")
    code, _ = _run_audit(tmp_path, fail=True)
    assert code == 0


def test_audit_main_repo_clean() -> None:
    """Главный smoke: текущее состояние репо проходит audit."""
    code, out = _run_audit(_REPO_ROOT, fail=True)
    assert code == 0, f"Repo audit failed:\n{out[-2000:]}"
