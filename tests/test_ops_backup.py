"""Phase L.E: tests for agmind.ops.backup."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from agmind.ops.backup import (
    BACKUP_FORMAT_VERSION,
    METADATA_FILENAME,
    BackupSource,
    create_backup,
    default_sources,
    read_metadata,
    restore_backup,
)

pytestmark = pytest.mark.backend_any


def _make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (install_dir, user_dir, system_dir) with mock files in каждой."""
    install = tmp_path / "opt"
    user = tmp_path / "user"
    system = tmp_path / "system"
    install.mkdir()
    user.mkdir()
    system.mkdir()

    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install / ".env").write_text("AGMIND_DOMAIN=example.com\n", encoding="utf-8")
    desc_dir = install / "templates" / "services"
    desc_dir.mkdir(parents=True)
    (desc_dir / "traefik.yaml").write_text("name: traefik\n", encoding="utf-8")
    (desc_dir / "qdrant.yaml").write_text("name: qdrant\n", encoding="utf-8")

    (user / "setup-state.json").write_text(
        json.dumps({"domain": "x"}), encoding="utf-8"
    )
    (user / "schema.json").write_text(
        json.dumps({"schema_version": 1, "applied": []}), encoding="utf-8"
    )

    snap_dir = system / "snapshots" / "2026-05-20T10-00-00Z"
    snap_dir.mkdir(parents=True)
    (snap_dir / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (snap_dir / "meta.json").write_text("{}", encoding="utf-8")
    return install, user, system


def _custom_sources(install: Path, user: Path, system: Path) -> list[BackupSource]:
    return [
        BackupSource("compose", install / "docker-compose.yml"),
        BackupSource("env", install / ".env"),
        BackupSource("descriptors", install / "templates" / "services"),
        BackupSource("setup_state", user / "setup-state.json"),
        BackupSource("schema_state", user / "schema.json"),
        BackupSource("snapshots", system / "snapshots"),
    ]


# ---------- create_backup ----------


def test_backup_creates_tarball(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"

    result = create_backup(
        output_path=out, sources=_custom_sources(install, user, system)
    )
    assert out.exists()
    assert result.bytes_written > 0
    assert "compose" in result.sources_included
    assert "env" in result.sources_included
    assert "descriptors" in result.sources_included
    assert "setup_state" in result.sources_included
    assert "schema_state" in result.sources_included
    assert "snapshots" in result.sources_included
    assert result.sources_missing == ()


def test_backup_records_missing_sources(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    (user / "setup-state.json").unlink()
    out = tmp_path / "backup.tar.gz"

    result = create_backup(
        output_path=out, sources=_custom_sources(install, user, system)
    )
    assert "setup_state" in result.sources_missing
    assert "compose" in result.sources_included


def test_backup_metadata_inside(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"
    create_backup(output_path=out, sources=_custom_sources(install, user, system))

    with tarfile.open(out, "r:gz") as tar:
        meta_member = tar.getmember(METADATA_FILENAME)
        meta_file = tar.extractfile(meta_member)
        assert meta_file is not None
        meta = json.loads(meta_file.read().decode("utf-8"))
    assert meta["format_version"] == BACKUP_FORMAT_VERSION
    assert "compose" in meta["included"]
    assert "created_at" in meta


def test_backup_required_source_missing_raises(tmp_path: Path) -> None:
    out = tmp_path / "backup.tar.gz"
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        create_backup(
            output_path=out,
            sources=[BackupSource("must_have", missing, optional=False)],
        )


# ---------- read_metadata ----------


def test_read_metadata_round_trip(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"
    create_backup(output_path=out, sources=_custom_sources(install, user, system))

    meta = read_metadata(out)
    assert meta["format_version"] == BACKUP_FORMAT_VERSION
    assert isinstance(meta["included"], list)


def test_read_metadata_on_non_agmind_archive(tmp_path: Path) -> None:
    out = tmp_path / "random.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        info = tarfile.TarInfo("hello.txt")
        info.size = 5
        import io

        tar.addfile(info, io.BytesIO(b"hello"))
    with pytest.raises(ValueError, match="not an agmind backup"):
        read_metadata(out)


# ---------- restore_backup ----------


def test_restore_roundtrip_files(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))

    # Wipe targets, then restore.
    (install / "docker-compose.yml").unlink()
    (install / ".env").unlink()
    (user / "setup-state.json").unlink()

    result = restore_backup(
        backup_path=backup, sources=_custom_sources(install, user, system)
    )
    assert "compose" in result.extracted
    assert "env" in result.extracted
    assert (install / "docker-compose.yml").read_text() == "services: {}\n"
    assert (install / ".env").read_text() == "AGMIND_DOMAIN=example.com\n"
    assert (user / "setup-state.json").exists()


def test_restore_directory_extracts_children(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))

    # Wipe descriptors dir
    import shutil

    shutil.rmtree(install / "templates" / "services")
    assert not (install / "templates" / "services").exists()

    restore_backup(
        backup_path=backup, sources=_custom_sources(install, user, system)
    )
    assert (install / "templates" / "services" / "traefik.yaml").exists()
    assert (install / "templates" / "services" / "qdrant.yaml").exists()


def test_restore_destinations_override(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))

    alt_compose = tmp_path / "alt" / "docker-compose.yml"
    restore_backup(
        backup_path=backup,
        sources=_custom_sources(install, user, system),
        destinations={"compose": alt_compose},
    )
    assert alt_compose.exists()
    assert alt_compose.read_text() == "services: {}\n"


def test_restore_missing_file_raises(tmp_path: Path) -> None:
    from agmind.ops.backup import restore_backup

    with pytest.raises(FileNotFoundError):
        restore_backup(backup_path=tmp_path / "does-not-exist.tar.gz")


# ---------- default_sources ----------


def test_default_sources_layout() -> None:
    srcs = default_sources()
    labels = {s.label for s in srcs}
    assert {"compose", "env", "descriptors", "setup_state", "schema_state", "snapshots"} <= labels
