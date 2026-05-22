"""MigrationRunner — discover, apply, rollback migrations (Phase L.D)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

from agmind.log import logger
from agmind.migrations.base import Migration, MigrationContext
from agmind.migrations.state import DEFAULT_USER_STATE_DIR, SCHEMA_FILENAME, SchemaState

log = logger(__name__)

DEFAULT_SYSTEM_STATE_DIR = Path("/var/lib/agmind")
VERSIONS_PACKAGE = "agmind.migrations.versions"


class MigrationRunner:
    """Discover migrations + apply pending + rollback applied.

    Default: ищет миграции в `agmind.migrations.versions.*`. Для тестов можно
    передать `migrations=[FakeM(1), FakeM(2)]` напрямую — discovery будет пропущена.
    """

    def __init__(
        self,
        user_state_dir: Path = DEFAULT_USER_STATE_DIR,
        system_state_dir: Path = DEFAULT_SYSTEM_STATE_DIR,
        migrations: list[Migration] | None = None,
    ) -> None:
        self.user_state_dir = Path(user_state_dir)
        self.system_state_dir = Path(system_state_dir)
        self._schema_path = self.user_state_dir / SCHEMA_FILENAME
        self._state = SchemaState.load(self._schema_path)
        discovered = migrations if migrations is not None else self.discover()
        self._migrations: list[Migration] = sorted(discovered, key=lambda m: m.version)
        self._validate_unique_versions()
        self._ctx = MigrationContext(
            user_state_dir=self.user_state_dir,
            system_state_dir=self.system_state_dir,
            log=log,
        )

    def _validate_unique_versions(self) -> None:
        seen: dict[int, str] = {}
        for m in self._migrations:
            if m.version <= 0:
                raise ValueError(
                    f"migration {m.name} has invalid version {m.version} (must be > 0)"
                )
            if m.version in seen:
                raise ValueError(
                    f"duplicate migration version {m.version}: {seen[m.version]} vs {m.name}"
                )
            seen[m.version] = m.name

    @staticmethod
    def discover() -> list[Migration]:
        """Find все Migration subclasses в agmind.migrations.versions."""
        out: list[Migration] = []
        try:
            pkg = importlib.import_module(VERSIONS_PACKAGE)
        except ImportError:
            log.warning("versions package %s not importable", VERSIONS_PACKAGE)
            return out
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod_name = f"{VERSIONS_PACKAGE}.{mod_info.name}"
            try:
                mod = importlib.import_module(mod_name)
            except ImportError as exc:
                log.warning("failed to import %s: %s", mod_name, exc)
                continue
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    obj is not Migration
                    and issubclass(obj, Migration)
                    and obj.__module__ == mod_name
                ):
                    out.append(obj())
        return out

    @property
    def current_version(self) -> int:
        return self._state.schema_version

    @property
    def all_migrations(self) -> list[Migration]:
        return list(self._migrations)

    def pending(self) -> list[Migration]:
        """Migrations не применённые ещё (по applied list, не по version)."""
        return [m for m in self._migrations if not self._state.is_applied(m.version)]

    def applied(self) -> list[Migration]:
        return [m for m in self._migrations if self._state.is_applied(m.version)]

    def up(self, target: int | None = None) -> list[Migration]:
        """Apply pending migrations up to target (inclusive). target=None — all."""
        applied_now: list[Migration] = []
        for m in self.pending():
            if target is not None and m.version > target:
                break
            log.info("applying %s — %s", m.name, m.description)
            m.up(self._ctx)
            self._state.record(m)
            applied_now.append(m)
        if applied_now:
            self._state.save(self._schema_path)
        return applied_now

    def down(self, steps: int = 1, target: int | None = None) -> list[Migration]:
        """Rollback applied migrations. Steps от current_version вниз, или до target.

        Если target указан — откатывает всё с version > target.
        Иначе — последние N (по version desc).
        """
        if steps < 0:
            raise ValueError("steps must be >= 0")
        applied_desc = sorted(self.applied(), key=lambda m: m.version, reverse=True)
        if target is not None:
            to_rollback = [m for m in applied_desc if m.version > target]
        else:
            to_rollback = applied_desc[:steps]
        rolled: list[Migration] = []
        for m in to_rollback:
            log.info("rolling back %s", m.name)
            m.down(self._ctx)
            self._state.unrecord(m.version)
            rolled.append(m)
        if rolled:
            self._state.save(self._schema_path)
        return rolled
