"""Phase L.E: tests for agmind.ops.backup."""

from __future__ import annotations

import io
import json
import stat
import subprocess
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

    (user / "setup-state.json").write_text(json.dumps({"domain": "x"}), encoding="utf-8")
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


def test_default_sources_include_runtime_version_manifest(tmp_path: Path) -> None:
    install = tmp_path / "opt"
    user = tmp_path / "user"
    system = tmp_path / "system"

    sources = default_sources(install_dir=install, user_dir=user, system_dir=system)
    by_label = {source.label: source.path for source in sources}

    assert by_label["versions"] == install / "version.env"


# ---------- create_backup ----------


def test_backup_creates_tarball(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"

    result = create_backup(output_path=out, sources=_custom_sources(install, user, system))
    assert out.exists()
    assert result.bytes_written > 0
    assert "compose" in result.sources_included
    assert "env" in result.sources_included
    assert "descriptors" in result.sources_included
    assert "setup_state" in result.sources_included
    assert "schema_state" in result.sources_included
    assert "snapshots" in result.sources_included
    assert result.sources_missing == ()


def test_backup_archive_is_private_when_it_contains_runtime_secrets(tmp_path: Path) -> None:
    install, _user, _system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"

    create_backup(output_path=out, sources=[BackupSource("env", install / ".env")])

    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_backup_unlinks_stale_temp_archive_symlink(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    out = tmp_path / "backup.tar.gz"
    temp_link = out.with_name(f".{out.name}.tmp")
    attacker_archive = tmp_path / "attacker-controlled-archive"
    temp_link.symlink_to(attacker_archive)

    result = create_backup(output_path=out, sources=_custom_sources(install, user, system))

    assert result.output_path == out
    assert out.exists()
    assert not out.is_symlink()
    assert not temp_link.exists()
    assert not attacker_archive.exists()


def test_backup_records_missing_sources(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    (user / "setup-state.json").unlink()
    out = tmp_path / "backup.tar.gz"

    result = create_backup(output_path=out, sources=_custom_sources(install, user, system))
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


def test_backup_reads_root_owned_file_via_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=placeholder\n", encoding="utf-8")
    out = tmp_path / "backup.tar.gz"
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        check: bool = False,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append({"cmd": cmd, "input": input})
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=b"POSTGRES_PASSWORD=from-sudo\n",
            stderr=b"",
        )

    monkeypatch.setattr("agmind.ops.backup.subprocess.run", fake_run)

    result = create_backup(
        output_path=out,
        sources=[BackupSource("env", env_file)],
        sudo_password="pw",
    )

    assert result.sources_included == ("env",)
    assert calls == [
        {
            "cmd": ["sudo", "-S", "-p", "", "--", "cat", str(env_file)],
            "input": b"pw\n",
        }
    ]
    with tarfile.open(out, "r:gz") as tar:
        member = tar.getmember("env")
        extracted = tar.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"POSTGRES_PASSWORD=from-sudo\n"
        assert member.mode & 0o777 == 0o600


def test_backup_failure_does_not_leave_partial_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose = tmp_path / "docker-compose.yml"
    env_file = tmp_path / ".env"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("POSTGRES_PASSWORD=placeholder\n", encoding="utf-8")
    out = tmp_path / "backup.tar.gz"

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        check: bool = False,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"sudo denied")

    monkeypatch.setattr("agmind.ops.backup.subprocess.run", fake_run)

    with pytest.raises(OSError, match="sudo command failed"):
        create_backup(
            output_path=out,
            sources=[
                BackupSource("compose", compose),
                BackupSource("env", env_file),
            ],
            sudo_password="pw",
        )

    assert not out.exists()


def test_backup_rejects_directory_symlink_member(tmp_path: Path) -> None:
    source = tmp_path / "descriptors"
    source.mkdir()
    (source / "qdrant.yaml").write_text("name: qdrant\n", encoding="utf-8")
    (source / "traefik.yaml").symlink_to(source / "qdrant.yaml")
    out = tmp_path / "backup.tar.gz"

    with pytest.raises(ValueError, match="unsupported backup source member"):
        create_backup(output_path=out, sources=[BackupSource("descriptors", source)])

    assert not out.exists()
    assert not out.with_name(f".{out.name}.tmp").exists()


def test_backup_rejects_sudo_directory_symlink_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "descriptors"
    source.mkdir()
    out = tmp_path / "backup.tar.gz"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)

        service_payload = b"name: qdrant\n"
        service = tarfile.TarInfo("./qdrant.yaml")
        service.size = len(service_payload)
        tar.addfile(service, io.BytesIO(service_payload))

        link = tarfile.TarInfo("./traefik.yaml")
        link.type = tarfile.SYMTYPE
        link.linkname = "./qdrant.yaml"
        tar.addfile(link)

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        check: bool = False,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=payload.getvalue(), stderr=b"")

    monkeypatch.setattr("agmind.ops.backup.subprocess.run", fake_run)

    with pytest.raises(ValueError, match="unsupported backup source member"):
        create_backup(
            output_path=out,
            sources=[BackupSource("descriptors", source)],
            sudo_password="pw",
        )

    assert not out.exists()
    assert not out.with_name(f".{out.name}.tmp").exists()


def test_restore_writes_env_via_sudo_with_restrictive_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    target_env = tmp_path / "root-owned" / ".env"
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, "input": input})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agmind.ops.backup.subprocess.run", fake_run)

    result = restore_backup(
        backup_path=backup,
        sources=_custom_sources(install, user, system),
        destinations={"env": target_env},
        sudo_password="pw",
    )

    assert "env" in result.extracted
    env_call = next(call for call in calls if call["cmd"][-1] == str(target_env))
    assert env_call["cmd"][:8] == ["sudo", "-S", "-p", "", "--", "install", "-D", "-m"]
    assert env_call["cmd"][-3] == "0600"
    assert env_call["input"] == "pw\n"
    assert "pw" not in env_call["cmd"]


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


def test_read_metadata_rejects_invalid_tarball(tmp_path: Path) -> None:
    backup = tmp_path / "broken.tar.gz"
    backup.write_bytes(b"not a tarball")

    with pytest.raises(ValueError, match="invalid backup archive"):
        read_metadata(backup)


def test_read_metadata_rejects_symlink_member(tmp_path: Path) -> None:
    backup = tmp_path / "malicious.tar.gz"
    payload = json.dumps(
        {
            "format_version": BACKUP_FORMAT_VERSION,
            "included": [],
        }
    ).encode("utf-8")

    with tarfile.open(backup, "w:gz") as tar:
        source = tarfile.TarInfo("payload.json")
        source.size = len(payload)
        tar.addfile(source, io.BytesIO(payload))

        metadata = tarfile.TarInfo(METADATA_FILENAME)
        metadata.type = tarfile.SYMTYPE
        metadata.linkname = "payload.json"
        tar.addfile(metadata)

    with pytest.raises(ValueError, match="unsupported metadata member type"):
        read_metadata(backup)


def test_read_metadata_rejects_non_object_payload(tmp_path: Path) -> None:
    backup = tmp_path / "malformed.tar.gz"
    payload = json.dumps(["not", "an", "object"]).encode("utf-8")

    with tarfile.open(backup, "w:gz") as tar:
        metadata = tarfile.TarInfo(METADATA_FILENAME)
        metadata.size = len(payload)
        tar.addfile(metadata, io.BytesIO(payload))

    with pytest.raises(ValueError, match="metadata payload must be an object"):
        read_metadata(backup)


def test_read_metadata_rejects_non_list_included(tmp_path: Path) -> None:
    backup = tmp_path / "malformed-included.tar.gz"
    payload = json.dumps(
        {
            "format_version": BACKUP_FORMAT_VERSION,
            "included": "env",
        }
    ).encode("utf-8")

    with tarfile.open(backup, "w:gz") as tar:
        metadata = tarfile.TarInfo(METADATA_FILENAME)
        metadata.size = len(payload)
        tar.addfile(metadata, io.BytesIO(payload))

    with pytest.raises(ValueError, match="metadata included must be a list"):
        read_metadata(backup)


def test_read_metadata_rejects_non_list_missing(tmp_path: Path) -> None:
    backup = tmp_path / "malformed-missing.tar.gz"
    payload = json.dumps(
        {
            "format_version": BACKUP_FORMAT_VERSION,
            "included": [],
            "missing": "env",
        }
    ).encode("utf-8")

    with tarfile.open(backup, "w:gz") as tar:
        metadata = tarfile.TarInfo(METADATA_FILENAME)
        metadata.size = len(payload)
        tar.addfile(metadata, io.BytesIO(payload))

    with pytest.raises(ValueError, match="metadata missing must be a list"):
        read_metadata(backup)


def test_restore_rejects_unsupported_backup_format_version(tmp_path: Path) -> None:
    backup = tmp_path / "future-format.tar.gz"
    target = tmp_path / ".env"
    payload = b"AGMIND_DOMAIN=example.com\n"
    metadata = {"format_version": BACKUP_FORMAT_VERSION + 1, "included": ["env"]}

    with tarfile.open(backup, "w:gz") as tar:
        env = tarfile.TarInfo("env")
        env.size = len(payload)
        tar.addfile(env, io.BytesIO(payload))

        meta_payload = json.dumps(metadata).encode("utf-8")
        meta = tarfile.TarInfo(METADATA_FILENAME)
        meta.size = len(meta_payload)
        tar.addfile(meta, io.BytesIO(meta_payload))

    with pytest.raises(ValueError, match="unsupported backup format version"):
        restore_backup(
            backup_path=backup,
            sources=[BackupSource("env", target)],
        )

    assert not target.exists()


# ---------- restore_backup ----------


def test_restore_roundtrip_files(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))

    # Wipe targets, then restore.
    (install / "docker-compose.yml").unlink()
    (install / ".env").unlink()
    (user / "setup-state.json").unlink()

    result = restore_backup(backup_path=backup, sources=_custom_sources(install, user, system))
    assert "compose" in result.extracted
    assert "env" in result.extracted
    assert (install / "docker-compose.yml").read_text() == "services: {}\n"
    assert (install / ".env").read_text() == "AGMIND_DOMAIN=example.com\n"
    assert (user / "setup-state.json").exists()


def test_restore_failure_preserves_existing_file_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    target_env = install / ".env"
    target_env.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    target_env.chmod(0o600)
    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(path: Path, data: bytes) -> int:
        if ".env" in path.name:
            original_write_bytes(path, b"BROKEN\n")
            raise OSError("disk full")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    with pytest.raises(OSError, match="disk full"):
        restore_backup(backup_path=backup, sources=_custom_sources(install, user, system))

    assert target_env.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=old\n"
    assert stat.S_IMODE(target_env.stat().st_mode) == 0o600
    assert not any(path.name != ".env" for path in install.glob("*.env*"))


def test_restore_file_unlinks_stale_temp_symlink(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    target_env = install / ".env"
    target_env.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    target_env.chmod(0o600)
    attacker_file = tmp_path / "attacker-controlled-env"
    temp_link = target_env.with_name(f".{target_env.name}.tmp")
    temp_link.symlink_to(attacker_file)

    result = restore_backup(backup_path=backup, sources=_custom_sources(install, user, system))

    assert "env" in result.extracted
    assert target_env.read_text(encoding="utf-8") == "AGMIND_DOMAIN=example.com\n"
    assert not target_env.is_symlink()
    assert stat.S_IMODE(target_env.stat().st_mode) == 0o600
    assert not temp_link.exists()
    assert not attacker_file.exists()


def test_restore_rejects_top_level_symlink_member(tmp_path: Path) -> None:
    backup = tmp_path / "malicious.tar.gz"
    target = tmp_path / ".env"
    metadata = {"format_version": BACKUP_FORMAT_VERSION, "included": ["env"]}

    with tarfile.open(backup, "w:gz") as tar:
        compose_payload = b"POSTGRES_PASSWORD=from-compose\n"
        compose = tarfile.TarInfo("compose")
        compose.size = len(compose_payload)
        tar.addfile(compose, io.BytesIO(compose_payload))

        env = tarfile.TarInfo("env")
        env.type = tarfile.SYMTYPE
        env.linkname = "compose"
        tar.addfile(env)

        meta_payload = json.dumps(metadata).encode("utf-8")
        meta = tarfile.TarInfo(METADATA_FILENAME)
        meta.size = len(meta_payload)
        tar.addfile(meta, io.BytesIO(meta_payload))

    with pytest.raises(ValueError, match="unsupported file member type"):
        restore_backup(
            backup_path=backup,
            sources=[BackupSource("env", target)],
        )

    assert not target.exists()


def test_restore_rejects_directory_path_traversal(tmp_path: Path) -> None:
    backup = tmp_path / "malicious.tar.gz"
    target = tmp_path / "target"
    payload = b"owned"
    metadata = {"format_version": BACKUP_FORMAT_VERSION, "included": ["descriptors"]}

    with tarfile.open(backup, "w:gz") as tar:
        top = tarfile.TarInfo("descriptors")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)

        bad = tarfile.TarInfo("descriptors/../escape.txt")
        bad.size = len(payload)
        tar.addfile(bad, io.BytesIO(payload))

        meta_payload = json.dumps(metadata).encode("utf-8")
        meta = tarfile.TarInfo(METADATA_FILENAME)
        meta.size = len(meta_payload)
        tar.addfile(meta, io.BytesIO(meta_payload))

    with pytest.raises(ValueError, match="unsafe member"):
        restore_backup(
            backup_path=backup,
            sources=[BackupSource("descriptors", target)],
        )

    assert not (tmp_path / "escape.txt").exists()


def test_restore_rejects_directory_symlink_member(tmp_path: Path) -> None:
    backup = tmp_path / "malicious.tar.gz"
    target = tmp_path / "target"
    metadata = {"format_version": BACKUP_FORMAT_VERSION, "included": ["descriptors"]}

    with tarfile.open(backup, "w:gz") as tar:
        top = tarfile.TarInfo("descriptors")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)

        payload = b"name: qdrant\n"
        source = tarfile.TarInfo("descriptors/qdrant.yaml")
        source.size = len(payload)
        tar.addfile(source, io.BytesIO(payload))

        link = tarfile.TarInfo("descriptors/traefik.yaml")
        link.type = tarfile.SYMTYPE
        link.linkname = "descriptors/qdrant.yaml"
        tar.addfile(link)

        meta_payload = json.dumps(metadata).encode("utf-8")
        meta = tarfile.TarInfo(METADATA_FILENAME)
        meta.size = len(meta_payload)
        tar.addfile(meta, io.BytesIO(meta_payload))

    with pytest.raises(ValueError, match="unsupported member type"):
        restore_backup(
            backup_path=backup,
            sources=[BackupSource("descriptors", target)],
        )

    assert not target.exists()
    assert not target.with_name(f".{target.name}.tmp").exists()


def test_restore_directory_extracts_children(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))

    # Wipe descriptors dir
    import shutil

    shutil.rmtree(install / "templates" / "services")
    assert not (install / "templates" / "services").exists()

    restore_backup(backup_path=backup, sources=_custom_sources(install, user, system))
    assert (install / "templates" / "services" / "traefik.yaml").exists()
    assert (install / "templates" / "services" / "qdrant.yaml").exists()


def test_restore_snapshot_env_members_are_private(tmp_path: Path) -> None:
    backup = tmp_path / "backup.tar.gz"
    target = tmp_path / "system" / "snapshots"
    metadata = {"format_version": BACKUP_FORMAT_VERSION, "included": ["snapshots"]}

    with tarfile.open(backup, "w:gz") as tar:
        top = tarfile.TarInfo("snapshots")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)

        snapshot_dir = tarfile.TarInfo("snapshots/2026-05-27T00-00-00Z")
        snapshot_dir.type = tarfile.DIRTYPE
        tar.addfile(snapshot_dir)

        env_payload = b"POSTGRES_PASSWORD=secret\n"
        env = tarfile.TarInfo("snapshots/2026-05-27T00-00-00Z/env.snapshot")
        env.size = len(env_payload)
        tar.addfile(env, io.BytesIO(env_payload))

        meta_payload = b"{}"
        snapshot_meta = tarfile.TarInfo("snapshots/2026-05-27T00-00-00Z/meta.json")
        snapshot_meta.size = len(meta_payload)
        tar.addfile(snapshot_meta, io.BytesIO(meta_payload))

        backup_meta_payload = json.dumps(metadata).encode("utf-8")
        backup_meta = tarfile.TarInfo(METADATA_FILENAME)
        backup_meta.size = len(backup_meta_payload)
        tar.addfile(backup_meta, io.BytesIO(backup_meta_payload))

    result = restore_backup(
        backup_path=backup,
        sources=[BackupSource("snapshots", target)],
    )

    restored_env = target / "2026-05-27T00-00-00Z" / "env.snapshot"
    restored_meta = target / "2026-05-27T00-00-00Z" / "meta.json"
    assert result.extracted == ("snapshots",)
    assert restored_env.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=secret\n"
    assert stat.S_IMODE(restored_env.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored_meta.stat().st_mode) == 0o644


def test_restore_directory_failure_preserves_existing_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / "backup.tar.gz"
    target = tmp_path / "templates" / "services"
    target.mkdir(parents=True)
    (target / "old.yaml").write_text("name: old\n", encoding="utf-8")
    metadata = {"format_version": BACKUP_FORMAT_VERSION, "included": ["descriptors"]}

    with tarfile.open(backup, "w:gz") as tar:
        top = tarfile.TarInfo("descriptors")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        for name, payload in (
            ("descriptors/traefik.yaml", b"name: traefik\n"),
            ("descriptors/qdrant.yaml", b"name: qdrant\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        meta_payload = json.dumps(metadata).encode("utf-8")
        meta = tarfile.TarInfo(METADATA_FILENAME)
        meta.size = len(meta_payload)
        tar.addfile(meta, io.BytesIO(meta_payload))

    original_write_bytes = Path.write_bytes

    def flaky_write_bytes(path: Path, data: bytes) -> int:
        if "qdrant.yaml" in path.name:
            original_write_bytes(path, b"BROKEN\n")
            raise OSError("disk full")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    with pytest.raises(OSError, match="disk full"):
        restore_backup(
            backup_path=backup,
            sources=[BackupSource("descriptors", target)],
        )

    assert sorted(path.name for path in target.iterdir()) == ["old.yaml"]
    assert (target / "old.yaml").read_text(encoding="utf-8") == "name: old\n"
    assert not target.with_name(f".{target.name}.tmp").exists()
    assert not target.with_name(f".{target.name}.rollback").exists()


def test_restore_directory_unlinks_stale_stage_symlink(tmp_path: Path) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    target = tmp_path / "restored" / "templates" / "services"
    staged = target.with_name(f".{target.name}.tmp")
    attacker_dir = tmp_path / "attacker-controlled-stage"
    attacker_dir.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    staged.symlink_to(attacker_dir, target_is_directory=True)

    result = restore_backup(
        backup_path=backup,
        sources=[BackupSource("descriptors", install / "templates" / "services")],
        destinations={"descriptors": target},
    )

    assert "descriptors" in result.extracted
    assert target.is_dir()
    assert not target.is_symlink()
    assert (target / "traefik.yaml").exists()
    assert not staged.exists()
    assert not any(attacker_dir.iterdir())


def test_restore_directory_via_sudo_stages_before_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "backup.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    target = tmp_path / "root-owned" / "templates" / "services"
    staged = target.with_name(f".{target.name}.tmp")
    rollback = target.with_name(f".{target.name}.rollback")
    commands: list[list[str]] = []
    installed_targets: list[Path] = []

    def fake_run_sudo(args: list[str], sudo_password: str) -> None:
        assert sudo_password == "pw"
        commands.append(args)

    def fake_sudo_install(
        payload: bytes,
        install_target: Path,
        mode: int,
        sudo_password: str,
    ) -> None:
        del payload
        assert sudo_password == "pw"
        assert mode == 0o644
        installed_targets.append(install_target)

    monkeypatch.setattr("agmind.ops.backup._run_sudo_no_output", fake_run_sudo)
    monkeypatch.setattr("agmind.ops.backup._sudo_install_bytes", fake_sudo_install)

    result = restore_backup(
        backup_path=backup,
        sources=[BackupSource("descriptors", install / "templates" / "services")],
        destinations={"descriptors": target},
        sudo_password="pw",
    )

    assert "descriptors" in result.extracted
    assert commands[:2] == [
        ["rm", "-rf", "--one-file-system", str(staged)],
        ["rm", "-rf", "--one-file-system", str(rollback)],
    ]
    assert ["install", "-d", "-m", "0755", str(staged)] in commands
    assert all(staged in path.parents for path in installed_targets)
    assert all(target not in (path, *path.parents) for path in installed_targets)
    assert commands[-1][0:2] == ["sh", "-c"]
    assert commands[-1][3:] == [
        "agmind-restore-directory",
        str(target),
        str(staged),
        str(rollback),
    ]


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


# ---------- L.E.1 / L.E.4 / L.E.5: cmd_restore hints ----------


def _make_minimal_backup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    install, user, system = _make_repo(tmp_path)
    backup = tmp_path / "b.tar.gz"
    create_backup(output_path=backup, sources=_custom_sources(install, user, system))
    return backup, install, user, system


def test_backup_cli_prompts_and_passes_sudo_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ops_cmd
    from agmind.ops.backup import BackupResult

    backup_path = tmp_path / "backup.tar.gz"
    calls: dict[str, object] = {}
    monkeypatch.setattr(ops_cmd.getpass, "getpass", lambda _prompt: "pw")

    def fake_create_backup(
        output_path: Path,
        sudo_password: str | None = None,
        data_sources: object = None,
    ) -> BackupResult:
        calls["output_path"] = output_path
        calls["sudo_password"] = sudo_password
        return BackupResult(
            output_path=Path(output_path),
            bytes_written=128,
            sources_included=("env",),
            sources_missing=(),
        )

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    assert ops_cmd.cmd_backup(backup_path, ask_sudo_password=True) == 0
    assert calls == {"output_path": backup_path, "sudo_password": "pw"}


def test_backup_cli_reports_sudo_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import ops_cmd

    backup_path = tmp_path / "backup.tar.gz"

    def fake_create_backup(
        output_path: Path,
        sudo_password: str | None = None,
        data_sources: object = None,
    ) -> None:
        raise OSError("sudo command failed (cat): sudo denied")

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    assert ops_cmd.cmd_backup(backup_path) == 1
    err = capsys.readouterr().err
    assert "sudo command failed" in err
    assert "Traceback" not in err


def test_backup_cli_reports_invalid_source_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import ops_cmd

    backup_path = tmp_path / "backup.tar.gz"

    def fake_create_backup(
        output_path: Path,
        sudo_password: str | None = None,
        data_sources: object = None,
    ) -> None:
        raise ValueError("unsupported backup source member: /opt/AGmind/.env")

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    assert ops_cmd.cmd_backup(backup_path) == 1
    err = capsys.readouterr().err
    assert "unsupported backup source member" in err
    assert "Traceback" not in err


def test_restore_cli_prompts_and_passes_sudo_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ops_cmd
    from agmind.ops.backup import RestoreResult

    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_bytes(b"placeholder")
    install = tmp_path / "install"
    user = tmp_path / "user"
    system = tmp_path / "system"
    install.mkdir()
    user.mkdir()
    system.mkdir()
    calls: dict[str, object] = {}

    monkeypatch.setattr(ops_cmd.getpass, "getpass", lambda _prompt: "pw")
    monkeypatch.setattr(ops_cmd, "read_metadata", lambda _path: {"included": ["env"]})
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _install: [])
    # This test exercises the prompt/sudo flow, not the integrity gate (M#17).
    monkeypatch.setattr(ops_cmd, "verify_backup", lambda _path: [])

    def fake_restore_backup(
        backup_path: Path,
        sources: list[BackupSource],
        sudo_password: str | None = None,
        **kwargs: object,
    ) -> RestoreResult:
        calls["backup_path"] = backup_path
        calls["source_labels"] = tuple(source.label for source in sources)
        calls["sudo_password"] = sudo_password
        return RestoreResult(extracted=("env",), metadata={})

    monkeypatch.setattr(ops_cmd, "restore_backup", fake_restore_backup)

    rc = ops_cmd.cmd_restore(
        backup_path=backup_path,
        yes=True,
        install_dir=install,
        user_dir=user,
        system_dir=system,
        ask_sudo_password=True,
    )

    assert rc == 0
    assert calls["backup_path"] == backup_path
    assert calls["sudo_password"] == "pw"
    assert "env" in calls["source_labels"]


def test_restore_cli_reports_invalid_archive_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import ops_cmd

    backup_path = tmp_path / "broken.tar.gz"
    backup_path.write_bytes(b"not a tarball")

    assert ops_cmd.cmd_restore(backup_path=backup_path, yes=True) == 1
    err = capsys.readouterr().err
    assert "invalid backup archive" in err
    assert "Traceback" not in err


def test_running_compose_services_ignores_subprocess_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ops_cmd

    install = tmp_path / "install"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    def fake_run(*args: object, **kwargs: object) -> object:
        raise PermissionError("docker socket denied")

    monkeypatch.setattr(ops_cmd.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(ops_cmd.subprocess, "run", fake_run)

    assert ops_cmd._running_compose_services(install) == []


def test_restore_warns_when_cf_token_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L.E.1: restore output должен подсказать восстановить cf_dns_api_token вручную."""
    from agmind.cli import ops_cmd

    backup, install, user, system = _make_minimal_backup(tmp_path)
    # Make sure no cf_dns_api_token in user_dir (это уже так _make_repo)
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _i: [])

    rc = ops_cmd.cmd_restore(
        backup_path=backup,
        yes=True,
        install_dir=install,
        user_dir=user,
        system_dir=system,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cf_dns_api_token" in out
    assert "chmod 600" in out


def test_restore_warns_when_models_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L.E.4: warn если /var/lib/agmind/models пуст после restore."""
    from agmind.cli import ops_cmd

    backup, install, user, system = _make_minimal_backup(tmp_path)
    # Empty models dir
    (system / "models").mkdir(exist_ok=True)
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _i: [])

    rc = ops_cmd.cmd_restore(
        backup_path=backup,
        yes=True,
        install_dir=install,
        user_dir=user,
        system_dir=system,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "models" in out
    assert "empty" in out
    assert "models pull" in out


def test_restore_silent_when_models_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import ops_cmd

    backup, install, user, system = _make_minimal_backup(tmp_path)
    models_dir = system / "models"
    models_dir.mkdir(exist_ok=True)
    (models_dir / "anymodel.gguf").write_bytes(b"\x00" * 16)
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _i: [])

    rc = ops_cmd.cmd_restore(
        backup_path=backup,
        yes=True,
        install_dir=install,
        user_dir=user,
        system_dir=system,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "empty" not in out  # warn не должна сработать


def test_restore_warns_on_running_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L.E.5: detect running compose services и WARN перед restore."""
    from agmind.cli import ops_cmd

    backup, install, user, system = _make_minimal_backup(tmp_path)
    monkeypatch.setattr(
        ops_cmd,
        "_running_compose_services",
        lambda _i: ["traefik", "llama-llm"],
    )

    rc = ops_cmd.cmd_restore(
        backup_path=backup,
        yes=True,
        install_dir=install,
        user_dir=user,
        system_dir=system,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "running services" in out
    assert "traefik" in out
    assert "llama-llm" in out
    assert "docker compose" in out


def test_running_compose_services_no_compose_file(tmp_path: Path) -> None:
    from agmind.cli.ops_cmd import _running_compose_services

    assert _running_compose_services(tmp_path) == []


def test_running_compose_services_no_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import ops_cmd

    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert ops_cmd._running_compose_services(tmp_path) == []


def test_running_compose_services_parses_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import ops_cmd

    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")

    class FakeProc:
        stdout = "traefik\nllama-llm\nqdrant\n\n"
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
    assert ops_cmd._running_compose_services(tmp_path) == ["traefik", "llama-llm", "qdrant"]
