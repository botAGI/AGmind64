"""Persistent SchemaState (Phase L.D).

Хранится в `~/.local/share/agmind/schema.json`:

    {
      "schema_version": 2,
      "applied": [
        {"version": 1, "name": "v001_V001Initial", "applied_at": "2026-05-20T13:00:00+00:00"},
        {"version": 2, "name": "v002_AddServicesField", "applied_at": "2026-06-01T09:15:00+00:00"}
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agmind.migrations.base import Migration

DEFAULT_USER_STATE_DIR = Path.home() / ".local" / "share" / "agmind"
SCHEMA_FILENAME = "schema.json"


@dataclass(frozen=True)
class AppliedMigration:
    """Запись об одной применённой миграции."""

    version: int
    name: str
    applied_at: str  # ISO-8601


@dataclass
class SchemaState:
    """Текущий schema version + история применённых миграций."""

    schema_version: int = 0
    applied: list[AppliedMigration] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> SchemaState:
        """Read state из json. Если файла нет — возвращает empty state (v0)."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupted schema state at {path}: {exc}") from exc
        applied = [AppliedMigration(**a) for a in data.get("applied", [])]
        return cls(schema_version=int(data.get("schema_version", 0)), applied=applied)

    def save(self, path: Path) -> None:
        """Persist state в json. Создаёт parent dirs если их нет."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "applied": [asdict(a) for a in self.applied],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def record(self, migration: Migration) -> None:
        """Mark migration как applied. Обновляет schema_version если выше."""
        if self.is_applied(migration.version):
            return
        ts = datetime.now(UTC).isoformat()
        self.applied.append(
            AppliedMigration(version=migration.version, name=migration.name, applied_at=ts)
        )
        self.schema_version = max(migration.version, self.schema_version)

    def unrecord(self, version: int) -> None:
        """Remove migration из applied. Обновляет schema_version на highest remaining."""
        self.applied = [a for a in self.applied if a.version != version]
        self.schema_version = max((a.version for a in self.applied), default=0)

    def is_applied(self, version: int) -> bool:
        return any(a.version == version for a in self.applied)
