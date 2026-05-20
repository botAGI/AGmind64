"""Migration ABC + context (Phase L.D)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationContext:
    """Передаётся в Migration.up()/down(). Все мутации state идут через context."""

    user_state_dir: Path
    """Per-user state (~/.local/share/agmind/)."""

    system_state_dir: Path
    """System state (/var/lib/agmind/)."""

    log: logging.Logger
    """Logger для migration progress."""


class Migration(ABC):
    """Базовый класс одной миграции state schema.

    Поля класса:
        version     — целое > 0, уникальное, монотонно возрастающее
        description — одна строка, отображается в `agmind migrate status`

    Контракт:
        up()/down() ИДЕМПОТЕНТНЫ — повторный вызов после успеха не должен ломать.
        Любая failure из up() оставляет state в pre-up состоянии (best effort).
    """

    version: int = 0
    description: str = ""

    @abstractmethod
    def up(self, ctx: MigrationContext) -> None:
        """Apply this migration."""

    @abstractmethod
    def down(self, ctx: MigrationContext) -> None:
        """Revert this migration. Может быть no-op для безвозвратных операций."""

    @property
    def name(self) -> str:
        """Stable identifier — `v{version:03d}_{ClassName}`."""
        return f"v{self.version:03d}_{type(self).__name__}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<Migration {self.name}>"
