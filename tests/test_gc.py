"""Phase L.C: tests for agmind.deploy.gc."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agmind.deploy.gc import (
    GcReport,
    _parse_size,
    _scan_used_models,
    format_gc_report,
    gc_models,
)

pytestmark = pytest.mark.backend_any


# ---------- _parse_size ----------


def test_parse_size_gb() -> None:
    assert _parse_size("Total reclaimed space: 1.5GB") == int(1.5 * 1024**3)


def test_parse_size_mb() -> None:
    assert _parse_size("Total reclaimed space: 500MB") == 500 * 1024**2


def test_parse_size_zero() -> None:
    assert _parse_size("Total reclaimed space: 0B") == 0


def test_parse_size_no_match() -> None:
    assert _parse_size("nothing to reclaim") == 0


# ---------- gc_models ----------


def test_gc_models_nonexistent_dir(tmp_path: Path) -> None:
    report = gc_models(models_dir=tmp_path / "does-not-exist", used_filenames=set())
    assert report.error is not None


def test_gc_models_removes_orphans(tmp_path: Path) -> None:
    # Create 3 model files, only 1 used
    used = tmp_path / "used.gguf"
    orphan1 = tmp_path / "orphan1.gguf"
    orphan2 = tmp_path / "orphan2.safetensors"
    used.write_bytes(b"x" * 100)
    orphan1.write_bytes(b"x" * 200)
    orphan2.write_bytes(b"x" * 300)

    report = gc_models(models_dir=tmp_path, used_filenames={"used.gguf"})

    assert report.items_removed == 2
    assert report.bytes_freed == 500
    assert set(report.items) == {"orphan1.gguf", "orphan2.safetensors"}
    assert used.exists()
    assert not orphan1.exists()
    assert not orphan2.exists()


def test_gc_models_dry_run_doesnt_delete(tmp_path: Path) -> None:
    orphan = tmp_path / "orphan.gguf"
    orphan.write_bytes(b"x" * 100)

    report = gc_models(models_dir=tmp_path, used_filenames=set(), dry_run=True)

    assert report.dry_run is True
    assert report.items_removed == 1
    assert orphan.exists()


def test_gc_models_skips_non_model_files(tmp_path: Path) -> None:
    (tmp_path / "model.gguf").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("hi")
    (tmp_path / "config.json").write_text("{}")

    report = gc_models(models_dir=tmp_path, used_filenames=set())
    # Only model.gguf считается orphan; .txt/.json skipped
    assert report.items_removed == 1
    assert report.items == ["model.gguf"]


# ---------- _scan_used_models ----------


def test_scan_used_models_returns_set() -> None:
    """Scan should return a set (могут быть empty если нет descriptors)."""
    used = _scan_used_models()
    assert isinstance(used, set)


# ---------- format_gc_report ----------


def test_format_report_empty() -> None:
    out = format_gc_report([])
    assert isinstance(out, str)


def test_format_report_shows_total() -> None:
    reports = [
        GcReport(target="containers", items_removed=3, bytes_freed=1024**2),
        GcReport(target="images", items_removed=5, bytes_freed=2 * 1024**3),
    ]
    out = format_gc_report(reports)
    assert "containers" in out
    assert "images" in out
    assert "Total:" in out
    assert "2.00 GB" in out  # ~1MB + 2GB ≈ 2GB


def test_format_report_dry_run_prefix() -> None:
    out = format_gc_report([GcReport(target="containers", dry_run=True)])
    assert "[dry-run]" in out


def test_format_report_shows_error() -> None:
    out = format_gc_report([GcReport(target="volumes", error="docker not installed")])
    assert "✗" in out
    assert "docker not installed" in out


# ---------- gc_containers (without docker) ----------


def test_gc_containers_no_docker() -> None:
    with patch("agmind.deploy.gc._docker_available", return_value=False):
        from agmind.deploy.gc import gc_containers

        report = gc_containers()
        assert report.error == "docker not installed"
