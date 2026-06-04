"""Data-tier backup: enumerate, dump, and round-trip the *data* (postgres/mysql logical dumps +
/var/lib/agmind/* volume dirs) that `agmind backup` previously excluded (config-only).

Enumeration is descriptor-driven; DB dumps use `docker exec` (postgres uses in-container local
trust — validated in research обкатка; mysql passes the root password via MYSQL_PWD env, not argv).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.ops.backup_data import (
    DataVolumeSource,
    DbDumpSource,
    data_sources,
)
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any


def _svc(name: str, *, volumes: list[str], env: dict[str, str] | None = None) -> ServiceDescriptor:
    return ServiceDescriptor(
        name=name, image="example/x:1", tier="storage", volumes=volumes, env=env or {}
    )


# ---- DbDumpSource.dump_command ----


def test_postgres_dump_command_local_trust_no_password_in_argv() -> None:
    src = DbDumpSource(
        label="pgdump/postgres",
        container="agmind-postgres",
        engine="postgres",
        user="dify",
        database="dify",
        password="s3cret",
    )
    cmd = src.dump_command()
    assert cmd == ["docker", "exec", "agmind-postgres", "pg_dump", "-U", "dify", "dify"]
    assert "s3cret" not in " ".join(cmd)  # postgres uses in-container local trust


def test_mysql_dump_command_passes_password_via_env_not_argv() -> None:
    src = DbDumpSource(
        label="pgdump/mysql",
        container="agmind-mysql",
        engine="mysql",
        user="root",
        database="rag_flow",
        password="rootpw",
    )
    cmd = src.dump_command()
    # password rides MYSQL_PWD via docker exec -e, never as a bare -p<pw> argv token
    assert "-e" in cmd and "MYSQL_PWD=rootpw" in cmd
    assert "-prootpw" not in cmd
    assert "mysqldump" in cmd and "--single-transaction" in cmd and "rag_flow" in cmd


# ---- data_sources enumeration ----


def test_enumerates_volume_dirs_for_plain_services() -> None:
    descriptors = {
        "qdrant": _svc("qdrant", volumes=["/var/lib/agmind/qdrant:/qdrant/storage"]),
    }
    sources = data_sources(["qdrant"], descriptors, env={})
    vols = [s for s in sources if isinstance(s, DataVolumeSource)]
    assert len(vols) == 1
    assert vols[0].host_path == Path("/var/lib/agmind/qdrant")
    assert vols[0].label == "volume/qdrant"


def test_postgres_becomes_a_db_dump_not_a_volume_tar() -> None:
    descriptors = {
        "postgres": _svc(
            "postgres",
            volumes=["/var/lib/agmind/postgres:/var/lib/postgresql/data"],
            env={"POSTGRES_USER": "dify", "POSTGRES_DB": "dify"},
        ),
    }
    sources = data_sources(["postgres"], descriptors, env={"POSTGRES_PASSWORD": "pw"})
    dumps = [s for s in sources if isinstance(s, DbDumpSource)]
    vols = [s for s in sources if isinstance(s, DataVolumeSource)]
    assert len(dumps) == 1 and not vols  # logical dump preferred over raw volume tar
    d = dumps[0]
    assert d.engine == "postgres" and d.container == "agmind-postgres"
    assert d.user == "dify" and d.database == "dify"


def test_mysql_db_dump_reads_database_and_root_password() -> None:
    descriptors = {
        "mysql": _svc(
            "mysql",
            volumes=["/var/lib/agmind/mysql:/var/lib/mysql"],
            env={"MYSQL_DATABASE": "rag_flow"},
        ),
    }
    sources = data_sources(["mysql"], descriptors, env={"MYSQL_ROOT_PASSWORD": "rootpw"})
    dumps = [s for s in sources if isinstance(s, DbDumpSource)]
    assert len(dumps) == 1
    assert dumps[0].engine == "mysql" and dumps[0].database == "rag_flow"
    assert dumps[0].user == "root" and dumps[0].password == "rootpw"


def test_skips_services_without_agmind_data_volume() -> None:
    descriptors = {
        "traefik": _svc("traefik", volumes=["/var/run/docker.sock:/var/run/docker.sock:ro"]),
    }
    assert data_sources(["traefik"], descriptors, env={}) == []


def test_only_enumerates_deployed_services() -> None:
    descriptors = {
        "qdrant": _svc("qdrant", volumes=["/var/lib/agmind/qdrant:/qdrant/storage"]),
        "redis": _svc("redis", volumes=["/var/lib/agmind/redis:/data"]),
    }
    sources = data_sources(["qdrant"], descriptors, env={})  # redis not deployed
    assert [s.label for s in sources] == ["volume/qdrant"]


def test_multi_bind_service_captures_every_data_dir_with_unique_labels() -> None:
    """Review MEDIUM backup-data-first-volume-only: a service binding two writable
    /var/lib/agmind/* dirs must back up BOTH (the old `break` kept only the first), each
    with a UNIQUE label so arcname/manifest/destination keys never collide."""
    descriptors = {
        "milvus": _svc(
            "milvus",
            volumes=[
                "/var/lib/agmind/milvus/etcd:/etcd",
                "/var/lib/agmind/milvus/minio:/minio",
            ],
        ),
    }
    sources = data_sources(["milvus"], descriptors, env={})
    vols = [s for s in sources if isinstance(s, DataVolumeSource)]
    labels = [v.label for v in vols]
    assert len(labels) == len(set(labels)) == 2, f"both binds, unique labels: {labels}"
    assert {v.host_path for v in vols} == {
        Path("/var/lib/agmind/milvus/etcd"),
        Path("/var/lib/agmind/milvus/minio"),
    }


def test_identical_data_bind_is_deduped() -> None:
    """Same host dir bound twice (e.g. :/data and :/data:rw) → one source, no collision."""
    descriptors = {
        "redis": _svc("redis", volumes=["/var/lib/agmind/redis:/data", "/var/lib/agmind/redis:/x"]),
    }
    vols = [
        s for s in data_sources(["redis"], descriptors, env={}) if isinstance(s, DataVolumeSource)
    ]
    assert len(vols) == 1 and vols[0].label == "volume/redis"


def test_volume_restore_target_rejects_traversal() -> None:
    """The trusted volume destination re-roots the label suffix under system_dir and
    refuses any escape (audit H#4 — never the archive's self-declared host_path)."""
    from agmind.ops.backup import volume_restore_target

    sysdir = Path("/var/lib/agmind")
    assert volume_restore_target("volume/qdrant", sysdir) == sysdir / "qdrant"
    assert volume_restore_target("volume/milvus/etcd", sysdir) == sysdir / "milvus" / "etcd"
    assert volume_restore_target("volume/../../root/.ssh", sysdir) is None
    assert volume_restore_target("volume//etc/shadow", sysdir) is None
    assert volume_restore_target("env", sysdir) is None


def test_restore_backup_surfaces_dbdump_failure(tmp_path: Path) -> None:
    """Review HIGH restore-dbdump-failure-reported-success: a non-zero DB restore must land
    in RestoreResult.failed, NOT extracted (the operator must not believe the load succeeded)."""
    import subprocess

    from agmind.ops.backup import create_backup, restore_backup

    db = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")

    def ok_dump(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- dump\n", stderr=b"")

    out = tmp_path / "b.tar.gz"
    create_backup(out, sources=[], data_sources=[db], data_run=ok_dump)

    def failing_restore(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1)  # psql/mysql returned non-zero

    res = restore_backup(out, sources=[], data_run=failing_restore)
    assert "dbdump/postgres" in res.failed
    assert "dbdump/postgres" not in res.extracted


def test_restore_backup_labels_scope_data_members(tmp_path: Path) -> None:
    """Review MEDIUM restore-label-no-data-scope: --label must scope the DATA loop too —
    `restore --label env` on an --include-data archive must NOT replay the DB dump."""
    import subprocess

    from agmind.ops.backup import BackupSource, create_backup, restore_backup

    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n", encoding="utf-8")
    db = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")

    def ok(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- dump\n", stderr=b"")

    out = tmp_path / "b.tar.gz"
    create_backup(out, sources=[BackupSource("env", env_file)], data_sources=[db], data_run=ok)

    called: list[list[str]] = []

    def restore_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        called.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    res = restore_backup(
        out,
        destinations={"env": tmp_path / "out.env"},
        sources=[BackupSource("env", tmp_path / "out.env")],
        data_run=restore_run,
        labels=["env"],
    )
    assert "dbdump/postgres" not in res.extracted and "dbdump/postgres" not in res.failed
    assert called == [], "the DB restore command must not run for a non-selected label"


# ---- dump_to_gzip ----


def test_dump_to_gzip_runs_command_and_compresses_stdout() -> None:
    import gzip
    import subprocess

    from agmind.ops.backup_data import dump_to_gzip

    src = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=b"-- PostgreSQL database dump\n", stderr=b""
        )

    payload = dump_to_gzip(src, run=fake_run)
    assert gzip.decompress(payload) == b"-- PostgreSQL database dump\n"
    assert seen["cmd"] == src.dump_command()


def test_dump_to_gzip_raises_on_nonzero_exit() -> None:
    import subprocess

    from agmind.ops.backup_data import dump_to_gzip

    src = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"connection refused")

    with pytest.raises(OSError, match="dump failed"):
        dump_to_gzip(src, run=fake_run)


# ---- restore_db_command (inverse of dump) ----


def test_restore_db_command_postgres_pipes_sql_into_psql() -> None:
    from agmind.ops.backup_data import restore_db_command

    src = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")
    cmd = restore_db_command(src)
    assert cmd == ["docker", "exec", "-i", "agmind-postgres", "psql", "-U", "dify", "-d", "dify"]


def test_restore_db_command_mysql_uses_env_password() -> None:
    from agmind.ops.backup_data import restore_db_command

    src = DbDumpSource(
        "dbdump/mysql", "agmind-mysql", "mysql", "root", "rag_flow", password="rootpw"
    )
    cmd = restore_db_command(src)
    assert "-e" in cmd and "MYSQL_PWD=rootpw" in cmd
    assert "mysql" in cmd and "rag_flow" in cmd
    assert "-prootpw" not in cmd


# ---- create_backup data-tier integration ----


def test_create_backup_captures_db_dump_and_volume(tmp_path: Path) -> None:
    import gzip
    import subprocess
    import tarfile

    from agmind.ops.backup import create_backup, read_metadata

    voldir = tmp_path / "qdrant"
    voldir.mkdir()
    (voldir / "seg.dat").write_text("vec", encoding="utf-8")
    db = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")
    vol = DataVolumeSource("volume/qdrant", voldir)

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- dump\nINSERT 1;\n", stderr=b"")

    out = tmp_path / "backup.tar.gz"
    create_backup(out, sources=[], data_sources=[db, vol], data_run=fake_run)
    assert out.exists()

    meta = read_metadata(out)
    data = {m["label"]: m for m in meta["data"]}
    assert data["dbdump/postgres"]["kind"] == "dbdump"
    assert data["dbdump/postgres"]["sha256"]
    assert data["volume/qdrant"]["kind"] == "volume"

    with tarfile.open(out) as t:
        names = t.getnames()
        assert "dbdump_postgres.sql.gz" in names
        assert any("volume_qdrant" in n for n in names)
        member = t.extractfile("dbdump_postgres.sql.gz")
        assert member is not None
        assert gzip.decompress(member.read()).startswith(b"-- dump")


def test_restore_backup_restores_volume_and_invokes_db_restore(tmp_path: Path) -> None:
    import subprocess

    from agmind.ops.backup import create_backup, restore_backup

    voldir = tmp_path / "qdrant"
    voldir.mkdir()
    (voldir / "seg.dat").write_text("vec", encoding="utf-8")
    db = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")
    vol = DataVolumeSource("volume/qdrant", voldir)

    def fake_dump(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- dump\nINSERT 1;\n", stderr=b"")

    out = tmp_path / "b.tar.gz"
    create_backup(out, sources=[], data_sources=[db, vol], data_run=fake_dump)

    restore_target = tmp_path / "restored-qdrant"
    seen: dict[str, object] = {}

    def fake_restore(
        cmd: list[str], input: bytes | None = None, check: bool = False, **kw: object
    ) -> subprocess.CompletedProcess[bytes]:
        seen["cmd"] = cmd
        seen["sql"] = input
        return subprocess.CompletedProcess(cmd, 0)

    res = restore_backup(
        out, destinations={"volume/qdrant": restore_target}, sources=[], data_run=fake_restore
    )

    assert (restore_target / "seg.dat").read_text(encoding="utf-8") == "vec"
    assert seen["cmd"][:4] == ["docker", "exec", "-i", "agmind-postgres"]  # type: ignore[index]
    assert b"INSERT 1;" in seen["sql"]  # type: ignore[operator]
    assert "volume/qdrant" in res.extracted and "dbdump/postgres" in res.extracted


def test_cmd_backup_include_data_enumerates_and_passes_data_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import ops_cmd
    from agmind.ops.backup import BackupResult

    install = tmp_path / "opt"
    install.mkdir()
    (install / ".env").write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _d: ["qdrant"])

    captured: dict[str, object] = {}

    def fake_create_backup(
        output_path: Path, sudo_password: str | None = None, data_sources: object = None
    ) -> BackupResult:
        captured["data_sources"] = data_sources
        return BackupResult(
            output_path=output_path, bytes_written=1, sources_included=(), sources_missing=()
        )

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    rc = ops_cmd.cmd_backup(tmp_path / "b.tar.gz", include_data=True, install_dir=install)
    assert rc == 0
    labels = [s.label for s in captured["data_sources"]]  # type: ignore[union-attr]
    assert "volume/qdrant" in labels  # real qdrant descriptor → /var/lib/agmind/qdrant volume


def test_cmd_backup_include_data_without_sudo_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review LOW backup-include-data-no-sudo: warn (not fail) before the slow archive when
    --include-data has no sudo password — root-owned data dirs may abort the whole backup."""
    from agmind.cli import ops_cmd
    from agmind.ops.backup import BackupResult

    install = tmp_path / "opt"
    install.mkdir()
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _d: [])
    monkeypatch.setattr(
        ops_cmd,
        "create_backup",
        lambda **kw: BackupResult(kw["output_path"], 1, (), ()),
    )
    rc = ops_cmd.cmd_backup(
        tmp_path / "b.tar.gz", include_data=True, ask_sudo_password=False, install_dir=install
    )
    assert rc == 0  # WARN, not fail-fast (world-readable data may still work)
    assert "sudo" in capsys.readouterr().err.lower()


def test_cmd_restore_routes_volume_member_to_system_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review HIGH restore-volume-data-unreachable: cmd_restore must route volume members to
    the trusted system_dir destination (by_label has no volume/* entry → they were silently
    dropped while printing '✓ restored')."""
    from agmind.cli import ops_cmd
    from agmind.ops.backup import create_backup

    voldir = tmp_path / "src-qdrant"
    voldir.mkdir()
    (voldir / "seg.dat").write_text("vec", encoding="utf-8")

    out = tmp_path / "b.tar.gz"
    create_backup(out, sources=[], data_sources=[DataVolumeSource("volume/qdrant", voldir)])

    install = tmp_path / "opt"
    install.mkdir()
    system = tmp_path / "system"
    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _d: [])

    rc = ops_cmd.cmd_restore(out, yes=True, install_dir=install, system_dir=system)
    assert rc == 0
    assert (system / "qdrant" / "seg.dat").read_text(encoding="utf-8") == "vec"


def test_cmd_restore_returns_nonzero_when_member_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """cmd_restore must exit non-zero (not print '✓') when restore_backup reports failures."""
    from agmind.cli import ops_cmd
    from agmind.ops.backup import RestoreResult

    install = tmp_path / "opt"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    out = tmp_path / "b.tar.gz"
    from agmind.ops.backup import BackupSource, create_backup

    create_backup(out, sources=[BackupSource("compose", install / "docker-compose.yml")])

    monkeypatch.setattr(ops_cmd, "_running_compose_services", lambda _d: [])
    monkeypatch.setattr(ops_cmd, "verify_backup", lambda _p: [])
    monkeypatch.setattr(
        ops_cmd,
        "restore_backup",
        lambda **kw: RestoreResult(extracted=(), metadata={}, failed=("dbdump/postgres",)),
    )
    rc = ops_cmd.cmd_restore(out, yes=True, install_dir=install, system_dir=tmp_path / "sys")
    assert rc == 1
    assert "fail" in capsys.readouterr().err.lower()


# ---- backup verify (integrity) ----


def test_verify_backup_ok_then_detects_corruption(tmp_path: Path) -> None:
    import subprocess

    from agmind.ops.backup import create_backup, verify_backup

    db = DbDumpSource("dbdump/postgres", "agmind-postgres", "postgres", "dify", "dify")

    def fake_dump(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"-- dump\nINSERT 1;\n", stderr=b"")

    out = tmp_path / "b.tar.gz"
    create_backup(out, sources=[], data_sources=[db], data_run=fake_dump)
    assert verify_backup(out) == []  # intact archive verifies clean

    blob = bytearray(out.read_bytes())
    blob[len(blob) // 2] ^= 0xFF  # flip a byte → corruption
    out.write_bytes(bytes(blob))
    assert verify_backup(out)  # corruption detected (sha256 mismatch or archive error)


def test_verify_backup_missing_file() -> None:
    from agmind.ops.backup import verify_backup

    issues = verify_backup(Path("/nonexistent/backup.tar.gz"))
    assert issues
