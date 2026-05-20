"""AGmind state schema migration system (Phase L.D).

Persistent state живёт в:
- `~/.local/share/agmind/` — per-user (setup-state.json, secrets, schema.json)
- `/var/lib/agmind/` — system-wide (snapshots, models)

Когда формат любого state файла меняется между релизами, добавляется новая
Migration в `agmind.migrations.versions`. `MigrationRunner` discover'ит их через
`pkgutil.iter_modules`, отслеживает применённое в `schema.json` и предоставляет
up/down operations.

Public API:
    Migration         — ABC для одной миграции
    MigrationContext  — что передаётся в up()/down()
    MigrationRunner   — discover + apply + rollback
    SchemaState       — persisted schema_version + applied list
"""

from agmind.migrations.base import Migration, MigrationContext
from agmind.migrations.runner import MigrationRunner
from agmind.migrations.state import AppliedMigration, SchemaState

__all__ = [
    "AppliedMigration",
    "Migration",
    "MigrationContext",
    "MigrationRunner",
    "SchemaState",
]
