"""Phase L.C: tests for agmind.deploy.gc."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agmind.deploy import gc as gc_mod
from agmind.deploy.gc import (
    GcReport,
    _parse_size,
    _scan_used_models,
    format_gc_report,
    gc_all,
    gc_models,
    gc_volumes,
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


def test_scan_used_models_reads_descriptor_interpolation_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    services_dir = repo_root / "templates" / "services"
    services_dir.mkdir(parents=True)
    (repo_root / "templates" / "models.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (services_dir / "llama.yaml").write_text(
        """
name: llama
image: example/llama:pinned
env:
  AGMIND_MODEL_FILE: ${AGMIND_MODEL_FILE:-active-llm.gguf}
  AGMIND_EMBED_FILE: ${AGMIND_EMBED_FILE:-active-embed.safetensors}
command:
  - --model
  - /models/${AGMIND_RERANK_FILE:-active-rerank.bin}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agmind.deploy.gc.__file__", str(repo_root / "agmind" / "deploy" / "gc.py"))

    used = _scan_used_models()

    assert {"active-llm.gguf", "active-embed.safetensors", "active-rerank.bin"} <= used


def test_scan_used_models_raises_on_malformed_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    services_dir = repo_root / "templates" / "services"
    services_dir.mkdir(parents=True)
    (services_dir / "broken.yaml").write_text("name: broken\n  bad: : :\n", encoding="utf-8")
    monkeypatch.setattr("agmind.deploy.gc.__file__", str(repo_root / "agmind" / "deploy" / "gc.py"))

    with pytest.raises(ValueError, match="broken.yaml"):
        _scan_used_models()


def test_gc_models_refuses_to_delete_when_scan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orphan = tmp_path / "orphan.gguf"
    orphan.write_bytes(b"weights")

    def boom() -> set[str]:
        raise ValueError("cannot parse model source broken.yaml")

    monkeypatch.setattr("agmind.deploy.gc._scan_used_models", boom)

    report = gc_models(models_dir=tmp_path, used_filenames=None)

    assert report.error is not None
    assert report.items_removed == 0
    assert orphan.exists(), "models must not be deleted when the used-set is incomplete"


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


def test_gc_all_dry_run_reports_docker_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    from agmind.deploy import gc

    def fake_run(*args: object, **kwargs: object) -> object:
        raise PermissionError("docker socket denied")

    monkeypatch.setattr(gc, "_docker_available", lambda: True)
    monkeypatch.setattr(gc.subprocess, "run", fake_run)

    reports = gc_all(dry_run=True)

    assert [report.target for report in reports] == [
        "containers",
        "images",
        "volumes",
        "networks",
    ]
    assert all(report.error is not None for report in reports)
    assert all("docker socket denied" in (report.error or "") for report in reports)


# ---------- gc_volumes label-safety argv invariant (F.2) ----------
#
# Production-data safety (gc.py:4-5): non-aggressive volume GC must ONLY touch
# volumes labeled `agmind.gc=auto`; aggressive mode intentionally prunes ALL
# unused volumes. These guards lock the argv that decides what gets deleted, so a
# future edit cannot silently drop the safety filter (deleting unlabeled
# production volumes) or smuggle it into aggressive mode. SCOPE FENCE: gc_images
# is deliberately NOT asserted here (its dry-run/real mismatch is Phase 6.7 G.2).

_GC_AUTO_LABEL = "label=agmind.gc=auto"
_SAFETY_FILTER_PAIR = ["--filter", _GC_AUTO_LABEL]


def _record_volume_argv(
    monkeypatch: pytest.MonkeyPatch, *, aggressive: bool, dry_run: bool
) -> list[str]:
    """Run gc_volumes with subprocess.run recorded; return the captured argv.

    Mirrors the in-repo recorder idiom (test_gc_all_dry_run_reports_docker_oserror
    and tests/install/test_install_verify.py): monkeypatch _docker_available→True
    and gc.subprocess.run→a recorder returning a real CompletedProcess so `_run`
    and `_parse_size` stay happy.
    """
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gc_mod, "_docker_available", lambda: True)
    monkeypatch.setattr(gc_mod.subprocess, "run", fake_run)

    report = gc_volumes(aggressive=aggressive, dry_run=dry_run)

    assert report.error is None
    assert len(calls) == 1, f"expected exactly one docker invocation, got {calls}"
    return calls[0]


def _adjacent_filter_label_values(argv: list[str]) -> set[str]:
    """Return the set of values that immediately follow a `--filter` flag."""
    values: set[str] = set()
    for i, token in enumerate(argv[:-1]):
        if token == "--filter":
            values.add(argv[i + 1])
    return values


def _contains_adjacent_pair(argv: list[str], pair: list[str]) -> bool:
    """True if `pair` appears as consecutive elements in `argv`."""
    n = len(pair)
    return any(argv[i : i + n] == pair for i in range(len(argv) - n + 1))


def test_gc_volumes_non_aggressive_real_carries_safety_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real non-aggressive prune MUST include `--filter label=agmind.gc=auto`."""
    argv = _record_volume_argv(monkeypatch, aggressive=False, dry_run=False)
    assert argv[:3] == ["docker", "volume", "prune"]
    assert _contains_adjacent_pair(argv, _SAFETY_FILTER_PAIR), argv


def test_gc_volumes_non_aggressive_dry_run_carries_safety_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run non-aggressive preview MUST include the same safety filter."""
    argv = _record_volume_argv(monkeypatch, aggressive=False, dry_run=True)
    assert argv[:3] == ["docker", "volume", "ls"]
    assert _contains_adjacent_pair(argv, _SAFETY_FILTER_PAIR), argv


def test_gc_volumes_aggressive_real_omits_safety_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real aggressive prune intentionally prunes ALL volumes (no label filter)."""
    argv = _record_volume_argv(monkeypatch, aggressive=True, dry_run=False)
    assert argv[:3] == ["docker", "volume", "prune"]
    assert _GC_AUTO_LABEL not in argv, argv


def test_gc_volumes_aggressive_dry_run_omits_safety_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run aggressive preview must mirror real aggressive: no label filter."""
    argv = _record_volume_argv(monkeypatch, aggressive=True, dry_run=True)
    assert argv[:3] == ["docker", "volume", "ls"]
    assert _GC_AUTO_LABEL not in argv, argv


def test_gc_volumes_dry_run_real_label_filter_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label filter previewed by dry-run must equal what real mode deletes.

    For each `aggressive` flag, the set of label-bearing `--filter` values used by
    `dry_run=True` (what the operator is shown) must match `dry_run=False` (what is
    actually pruned). A drift here means the preview lies about deletions.
    """
    for aggressive in (False, True):
        dry_labels = {
            v
            for v in _adjacent_filter_label_values(
                _record_volume_argv(monkeypatch, aggressive=aggressive, dry_run=True)
            )
            if v.startswith("label=")
        }
        real_labels = {
            v
            for v in _adjacent_filter_label_values(
                _record_volume_argv(monkeypatch, aggressive=aggressive, dry_run=False)
            )
            if v.startswith("label=")
        }
        assert dry_labels == real_labels, (
            f"label-filter parity broken for aggressive={aggressive}: "
            f"dry-run={dry_labels} real={real_labels}"
        )
        if aggressive:
            assert dry_labels == set()
        else:
            assert dry_labels == {_GC_AUTO_LABEL}
