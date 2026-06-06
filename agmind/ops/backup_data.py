"""Data-tier backup enumeration.

`agmind backup` historically saved only config (compose/.env/descriptors). The *data* —
postgres/mysql contents and the /var/lib/agmind/* volume dirs — was excluded, so a restore
could not bring back a stack's state. This module enumerates, per deployed service:

- **DB dumps** for postgres/mysql — logical, transaction-consistent dumps via ``docker exec``
  (preferred over a raw tar of a running DB's data dir, which is crash-consistent at best).
  Postgres uses in-container local-socket trust (no password in argv — validated in research);
  mysql passes the root password via ``MYSQL_PWD`` env to ``docker exec``, never as a ``-p`` arg.
- **Volume dirs** for every other service with a writable ``/var/lib/agmind/<svc>`` bind — tarred
  by the caller (the existing sudo-tar path in agmind.ops.backup; host binds mean no
  ``docker run alpine`` is needed).

Enumeration is descriptor-driven and scoped to the deployed service closure.
"""

from __future__ import annotations

import gzip
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agmind.schemas import ServiceDescriptor

_DATA_PREFIX = "/var/lib/agmind/"

_Run = Callable[..., "subprocess.CompletedProcess[bytes]"]

# Services whose data is captured as a logical DB dump rather than a volume tar.
# mongo (komodo-mongo): a logical mongodump is consistent; a tar of a live WiredTiger dir is
# crash-consistent at best (live-audit 2026-06-05 hot-volume-tar-not-quiesced).
_DB_ENGINES = {"postgres": "postgres", "mysql": "mysql", "komodo-mongo": "mongo"}


@dataclass(frozen=True)
class DataVolumeSource:
    """A writable host data dir to tar into the backup."""

    label: str
    host_path: Path


@dataclass(frozen=True)
class DbDumpSource:
    """A logical DB dump captured via ``docker exec`` and stored as an archive member."""

    label: str
    container: str
    engine: str  # "postgres" | "mysql"
    user: str
    database: str
    password: str = ""
    globals_only: bool = False
    """postgres only: dump cluster-wide globals (roles/grants/tablespaces) via pg_dumpall
    --globals-only instead of a single database. Restored BEFORE the per-db dumps so roles the
    per-db GRANTs reference exist (live-audit MED postgres-backup-single-db-no-globals)."""

    def dump_command(self) -> list[str]:
        """Return the ``docker exec`` argv whose stdout is the dump (caller gzips + stores)."""
        if self.engine == "postgres":
            # in-container unix-socket connections use trust auth → no password in argv
            if self.globals_only:
                return [
                    "docker",
                    "exec",
                    self.container,
                    "pg_dumpall",
                    "-U",
                    self.user,
                    "--globals-only",
                ]
            return ["docker", "exec", self.container, "pg_dump", "-U", self.user, self.database]
        if self.engine == "mysql":
            cmd = ["docker", "exec"]
            if self.password:
                cmd += ["-e", f"MYSQL_PWD={self.password}"]  # via env, never -p<pw> in argv
            cmd += [
                self.container,
                "mysqldump",
                "--single-transaction",
                "--quick",
                "-u",
                self.user,
                self.database,
            ]
            return cmd
        if self.engine == "mongo":
            # password via -e env (never in argv), like mysql; --archive streams to stdout
            # (the caller gzips). Auth against the admin DB (the root user lives there).
            return [
                "docker",
                "exec",
                "-e",
                f"MONGO_PW={self.password}",
                self.container,
                "bash",
                "-c",
                f'mongodump --username {self.user} --password "$MONGO_PW" '
                "--authenticationDatabase admin --archive",
            ]
        raise ValueError(f"unknown db engine: {self.engine!r}")


def _host_data_path(volume_spec: str) -> Path | None:
    """Return the host path of a writable ``/var/lib/agmind/*`` bind, else None."""
    parts = volume_spec.split(":")
    if len(parts) < 2:
        return None
    host = parts[0]
    mode = parts[2] if len(parts) > 2 else ""
    if "ro" in mode.split(","):
        return None
    if not host.startswith(_DATA_PREFIX):
        return None
    return Path(host)


def data_sources(
    services: Sequence[str],
    descriptors: Mapping[str, ServiceDescriptor],
    env: Mapping[str, str],
) -> list[DataVolumeSource | DbDumpSource]:
    """Enumerate data-tier backup sources for the deployed service closure."""
    out: list[DataVolumeSource | DbDumpSource] = []
    for name in services:
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue
        engine = _DB_ENGINES.get(name)
        if engine == "postgres":
            user = descriptor.env.get("POSTGRES_USER", "postgres")
            password = env.get("POSTGRES_PASSWORD", "")
            # Globals FIRST (roles/grants/tablespaces). Without them a restore into a fresh
            # cluster cannot recreate the roles that the per-db dump's GRANTs reference, so the
            # restore is incomplete/fails (live-audit MED postgres-backup-single-db-no-globals).
            out.append(
                DbDumpSource(
                    label=f"dbdump/{name}-globals",
                    container=f"agmind-{name}",
                    engine="postgres",
                    user=user,
                    database="",
                    password=password,
                    globals_only=True,
                )
            )
            # Per-db dump of POSTGRES_DB (the app database). NOTE: a multi-database postgres
            # instance would need live enumeration of pg_database; the current stack runs a
            # single app DB so POSTGRES_DB is complete (the "postgres" maintenance DB is empty).
            out.append(
                DbDumpSource(
                    label=f"dbdump/{name}",
                    container=f"agmind-{name}",
                    engine="postgres",
                    user=user,
                    database=descriptor.env.get("POSTGRES_DB", user),
                    password=password,
                )
            )
            continue
        if engine == "mysql":
            out.append(
                DbDumpSource(
                    label=f"dbdump/{name}",
                    container=f"agmind-{name}",
                    engine="mysql",
                    user="root",
                    database=descriptor.env.get("MYSQL_DATABASE", ""),
                    password=env.get("MYSQL_ROOT_PASSWORD", ""),
                )
            )
            continue
        if engine == "mongo":
            # Logical mongodump instead of a crash-consistent volume tar of the live WiredTiger
            # dir (live-audit 2026-06-05 hot-volume-tar-not-quiesced). Root user lives in admin.
            out.append(
                DbDumpSource(
                    label=f"dbdump/{name}",
                    container=f"agmind-{name}",
                    engine="mongo",
                    user=env.get("KOMODO_DATABASE_USERNAME", "komodo"),
                    database="",
                    password=env.get("KOMODO_DATABASE_PASSWORD", ""),
                )
            )
            continue
        # Capture EVERY writable data bind, not just the first (a service may bind several,
        # e.g. milvus → etcd + minio). Label by the host path RELATIVE to /var/lib/agmind so
        # each distinct dir gets a unique, collision-free arcname/manifest/destination key, and
        # the restore destination is derivable from the label alone (audit H#4 — never trust the
        # archive's self-declared host_path). Identical binds (e.g. :/data and :/data:rw) dedupe.
        seen: set[Path] = set()
        for volume in descriptor.volumes:
            host_path = _host_data_path(volume)
            if host_path is None or host_path in seen:
                continue
            seen.add(host_path)
            rel = host_path.relative_to(_DATA_PREFIX).as_posix()
            out.append(DataVolumeSource(label=f"volume/{rel}", host_path=host_path))
    return out


def dump_to_gzip(source: DbDumpSource, *, run: _Run = subprocess.run) -> bytes:
    """Run the DB dump command and return its gzipped stdout. Raises OSError on failure."""
    proc = run(source.dump_command(), capture_output=True, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[:200]
        raise OSError(f"{source.label} dump failed (rc={proc.returncode}): {err}")
    return gzip.compress(proc.stdout)


def restore_db_command(source: DbDumpSource) -> list[str]:
    """Return the ``docker exec -i`` argv that loads a (gunzipped) dump on stdin into the DB."""
    if source.engine == "postgres":
        if source.globals_only:
            # cluster-level SQL (CREATE ROLE / ALTER ROLE / tablespaces) — no -d database
            return ["docker", "exec", "-i", source.container, "psql", "-U", source.user]
        return [
            "docker",
            "exec",
            "-i",
            source.container,
            "psql",
            "-U",
            source.user,
            "-d",
            source.database,
        ]
    if source.engine == "mysql":
        cmd = ["docker", "exec", "-i"]
        if source.password:
            cmd += ["-e", f"MYSQL_PWD={source.password}"]
        cmd += [source.container, "mysql", source.database]
        return cmd
    if source.engine == "mongo":
        # --archive reads the (gunzipped) dump on stdin; --drop replaces existing collections.
        return [
            "docker",
            "exec",
            "-i",
            "-e",
            f"MONGO_PW={source.password}",
            source.container,
            "bash",
            "-c",
            f'mongorestore --username {source.user} --password "$MONGO_PW" '
            "--authenticationDatabase admin --archive --drop",
        ]
    raise ValueError(f"unknown db engine: {source.engine!r}")
