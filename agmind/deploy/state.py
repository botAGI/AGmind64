"""Deploy-state model — паспорт установки (D-01, Phase 13.B).

Single source of truth for what was actually applied by the last successful deploy:
`<install_dir>/deploy-state.json`, written after every successful `--apply` (install /
`agmind deploy --apply` / `agmind upgrade --apply`) and read by day-2 commands instead
of hardcoding profiles/domain. Non-secret by construction (0644, selection metadata
only — profiles/services/domain/edge_mode) and forward-compatible on read (unknown
fields written by a newer agmind version are dropped, not rejected).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DeployState(BaseModel):
    """Паспорт установки — что реально применено последним успешным apply (D-01).

    `extra="ignore"` gives forward-compat on read for free: a field added by a future
    agmind version is silently dropped instead of raising, so an older reader never
    crashes on a newer `deploy-state.json`. Carries NO secrets by construction —
    selection metadata only; do not add token/password fields here (those live in
    `.env`, mode 0600).
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    agmind_version: str
    profiles: list[str]
    requested_services: list[str]
    resolved_services: list[str]
    domain: str | None = None
    edge_mode: Literal["local", "lan", "public"]
    written_at: str
    config_hash: str = ""

    @classmethod
    def new(
        cls,
        *,
        agmind_version: str,
        profiles: list[str],
        requested_services: list[str],
        resolved_services: list[str],
        domain: str | None,
        edge_mode: Literal["local", "lan", "public"],
        config_hash: str = "",
    ) -> DeployState:
        """Build a state stamped with the current UTC time.

        Always `datetime.now(UTC)` — never `datetime.utcnow()` (naive, deprecated) or a
        local-time source — so `written_at` is an unambiguous UTC ISO-8601 string.
        """
        return cls(
            agmind_version=agmind_version,
            profiles=profiles,
            requested_services=requested_services,
            resolved_services=resolved_services,
            domain=domain,
            edge_mode=edge_mode,
            written_at=datetime.now(UTC).isoformat(),
            config_hash=config_hash,
        )


__all__ = ["DeployState"]
