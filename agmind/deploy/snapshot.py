"""Snapshot manager (Phase L.B): save/restore deployment state перед каждым apply.

Структура snapshot directory:
    /var/lib/agmind/snapshots/2026-05-19T18:30:42Z/
    ├── compose.yml         — текущий рендеренный compose
    ├── meta.json           — timestamp, agmind_version, profile, reason
    ├── env.snapshot        — /opt/agmind/.env (если есть)
    └── descriptors/*.yaml  — copy templates/services/*.yaml на момент snapshot

Retention: 10 последних snapshots auto-prune'ятся (см. SnapshotManager.prune_old).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agmind.log import logger

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


@dataclass
class SnapshotManager:
    """Manage deployment snapshots — save/list/restore/prune."""

    snapshots_dir: Path = DEFAULT_SNAPSHOTS_DIR
    retention: int = DEFAULT_RETENTION
    _last_saved: Snapshot | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.snapshots_dir = Path(self.snapshots_dir)

    def save(
        self,
        compose_text: str,
        profile: str,
        reason: str = "",
        descriptors_dir: Path | None = None,
        env_file: Path | None = None,
        agmind_version: str = "",
    ) -> Snapshot:
        """Create new snapshot from current state.

        Args:
            compose_text: содержимое текущего docker-compose.yml
            profile: active profile string
            reason: human-readable reason
            descriptors_dir: путь к templates/services/ для copy
            env_file: путь к .env файлу для copy
            agmind_version: version string
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        snap_path = self.snapshots_dir / ts
        snap_path.mkdir(parents=True, exist_ok=True)

        # compose.yml
        snap_path.joinpath("compose.yml").write_text(compose_text, encoding="utf-8")

        # descriptors copy
        if descriptors_dir is not None and descriptors_dir.exists():
            target = snap_path / "descriptors"
            shutil.copytree(descriptors_dir, target, dirs_exist_ok=True)

        # env snapshot
        if env_file is not None and env_file.exists():
            shutil.copy2(env_file, snap_path / "env.snapshot")

        # meta
        meta = {
            "id": ts,
            "timestamp": datetime.now(UTC).isoformat(),
            "profile": profile,
            "reason": reason,
            "agmind_version": agmind_version,
        }
        snap_path.joinpath("meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

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
                log.error("failed to prune %s: %s", snap.id, exc)
        return removed
