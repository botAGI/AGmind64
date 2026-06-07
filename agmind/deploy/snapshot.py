"""Snapshot manager (Phase L.B): save/restore deployment state перед каждым apply.

Структура snapshot directory:
    /var/lib/agmind/snapshots/2026-05-19T18-30-42.123456Z/
    ├── compose.yml         — текущий рендеренный compose
    ├── meta.json           — timestamp, agmind_version, profile, reason
    ├── env.snapshot        — /opt/agmind/.env (если есть)
    ├── version.env.snapshot — /opt/agmind/version.env (если есть)
    └── descriptors/*.yaml  — copy templates/services/*.yaml на момент snapshot

Retention: 10 последних snapshots auto-prune'ятся (см. SnapshotManager.prune_old).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agmind.core.files import write_text_atomic
from agmind.core.logging import logger
from agmind.core.proc import sudo_argv, sudo_stdin_text

log = logger(__name__)

DEFAULT_SNAPSHOTS_DIR = Path("/var/lib/agmind/snapshots")
DEFAULT_RETENTION = 10  # keep last N snapshots


@dataclass(frozen=True)
class Snapshot:
    """One deploy snapshot."""

    path: Path
    """Absolute path to snapshot directory."""

    timestamp: datetime
    """When snapshot was created (UTC)."""

    profile: str
    """agmind_profiles на момент snapshot (e.g. 'core,observability')."""

    reason: str = ""
    """Human-readable reason (e.g. 'pre-deploy 2026-05-19', 'manual')."""

    agmind_version: str = ""

    @property
    def id(self) -> str:
        """Unique ID = directory name (ISO timestamp)."""
        return self.path.name

    @property
    def compose_file(self) -> Path:
        return self.path / "compose.yml"

    @property
    def meta_file(self) -> Path:
        return self.path / "meta.json"

    @property
    def descriptors_dir(self) -> Path:
        return self.path / "descriptors"

    @property
    def env_file(self) -> Path:
        return self.path / "env.snapshot"

    @property
    def version_env_file(self) -> Path:
        return self.path / "version.env.snapshot"


@dataclass
class SnapshotManager:
    """Manage deployment snapshots — save/list/restore/prune."""

    snapshots_dir: Path = DEFAULT_SNAPSHOTS_DIR
    retention: int = DEFAULT_RETENTION
    sudo_password: str | None = None
    _last_saved: Snapshot | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.snapshots_dir = Path(self.snapshots_dir)

    def _run_sudo(self, cmd: list[str]) -> None:
        if self.sudo_password is None:
            raise PermissionError("sudo password not provided for snapshot store")
        result = subprocess.run(
            sudo_argv(cmd),
            capture_output=True,
            text=True,
            check=False,
            input=sudo_stdin_text(self.sudo_password),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"sudo {cmd[0]} failed").strip()
            raise OSError(detail)

    def _mkdir(self, path: Path) -> None:
        if self.sudo_password is not None:
            self._run_sudo(["install", "-d", "-m", "0755", str(path)])
            return
        path.mkdir(parents=True, exist_ok=True)

    def _write_text(self, path: Path, text: str, mode: str = "0644") -> None:
        if self.sudo_password is None:
            write_text_atomic(path, text, mode=int(mode, 8))
            return

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=".agmind-snapshot-",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)

        try:
            self._run_sudo(["install", "-D", "-m", mode, str(tmp_path), str(path)])
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def _copy_file(self, source: Path, target: Path, mode: str) -> None:
        if self.sudo_password is not None:
            self._run_sudo(["install", "-D", "-m", mode, str(source), str(target)])
            return
        shutil.copy2(source, target)
        target.chmod(int(mode, 8))

    def _copytree(self, source: Path, target: Path) -> None:
        if self.sudo_password is not None:
            self._run_sudo(["install", "-d", "-m", "0755", str(target)])
            self._run_sudo(["cp", "-R", "--no-preserve=ownership", f"{source}/.", str(target)])
            return
        shutil.copytree(source, target, dirs_exist_ok=True)

    def _remove_incomplete_snapshot(self, path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            if self.sudo_password is not None and path.is_relative_to(self.snapshots_dir):
                try:
                    self._run_sudo(["rm", "-rf", "--one-file-system", str(path)])
                    return
                except OSError as sudo_exc:
                    log.error(
                        "failed to remove incomplete snapshot %s via sudo: %s", path, sudo_exc
                    )
            else:
                log.error("failed to remove incomplete snapshot %s: %s", path, exc)

    def save(
        self,
        compose_text: str,
        profile: str,
        reason: str = "",
        descriptors_dir: Path | None = None,
        env_file: Path | None = None,
        version_env_file: Path | None = None,
        agmind_version: str = "",
    ) -> Snapshot:
        """Create new snapshot from current state.

        Args:
            compose_text: содержимое текущего docker-compose.yml
            profile: active profile string
            reason: human-readable reason
            descriptors_dir: путь к templates/services/ для copy
            env_file: путь к .env файлу для copy
            version_env_file: путь к version.env файлу для copy
            agmind_version: version string
        """
        now = datetime.now(UTC)
        ts = now.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        snap_path = self.snapshots_dir / ts
        self._mkdir(snap_path)

        try:
            # compose.yml
            self._write_text(snap_path.joinpath("compose.yml"), compose_text)

            # descriptors copy
            if descriptors_dir is not None and descriptors_dir.exists():
                target = snap_path / "descriptors"
                self._copytree(descriptors_dir, target)

            # env snapshot
            if env_file is not None and env_file.exists():
                self._copy_file(env_file, snap_path / "env.snapshot", "0600")
            if version_env_file is not None and version_env_file.exists():
                self._copy_file(version_env_file, snap_path / "version.env.snapshot", "0644")

            # meta
            meta = {
                "id": ts,
                "timestamp": now.isoformat(),
                "profile": profile,
                "reason": reason,
                "agmind_version": agmind_version,
            }
            self._write_text(
                snap_path.joinpath("meta.json"),
                json.dumps(meta, indent=2, ensure_ascii=False),
            )
        except Exception:
            self._remove_incomplete_snapshot(snap_path)
            raise

        snapshot = Snapshot(
            path=snap_path,
            timestamp=datetime.fromisoformat(meta["timestamp"]),
            profile=profile,
            reason=reason,
            agmind_version=agmind_version,
        )
        self._last_saved = snapshot
        log.info("snapshot saved: %s (profile=%s, reason=%s)", ts, profile, reason)

        # Auto-prune старых
        self.prune_old()
        return snapshot

    def list(self) -> list[Snapshot]:
        """Return all snapshots sorted by timestamp (newest first)."""
        if not self.snapshots_dir.exists():
            return []
        out: list[Snapshot] = []
        for path in sorted(self.snapshots_dir.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            meta_file = path / "meta.json"
            if not meta_file.exists():
                log.warning("snapshot %s missing meta.json — skipping", path.name)
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                out.append(
                    Snapshot(
                        path=path,
                        timestamp=datetime.fromisoformat(meta["timestamp"]),
                        profile=meta.get("profile", ""),
                        reason=meta.get("reason", ""),
                        agmind_version=meta.get("agmind_version", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("snapshot %s meta parse error: %s", path.name, exc)
        return out

    def latest(self) -> Snapshot | None:
        """Return most recent snapshot or None."""
        snapshots = self.list()
        return snapshots[0] if snapshots else None

    def get(self, snapshot_id: str) -> Snapshot | None:
        """Find snapshot by id (directory name)."""
        for snap in self.list():
            if snap.id == snapshot_id:
                return snap
        return None

    def prune_old(self) -> int:
        """Delete snapshots older than retention. Returns count removed."""
        snapshots = self.list()
        to_remove = snapshots[self.retention :]
        removed = 0
        for snap in to_remove:
            try:
                shutil.rmtree(snap.path)
                log.info("pruned snapshot %s", snap.id)
                removed += 1
            except OSError as exc:
                if self.sudo_password is not None and snap.path.is_relative_to(self.snapshots_dir):
                    try:
                        self._run_sudo(["rm", "-rf", "--one-file-system", str(snap.path)])
                        log.info("pruned snapshot %s via sudo", snap.id)
                        removed += 1
                        continue
                    except OSError as sudo_exc:
                        log.error("failed to prune %s via sudo: %s", snap.id, sudo_exc)
                        continue
                log.error("failed to prune %s: %s", snap.id, exc)
        return removed
