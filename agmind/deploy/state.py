"""Deploy-state model — паспорт установки (D-01, Phase 13.B).

Single source of truth for what was actually applied by the last successful deploy:
`<install_dir>/deploy-state.json`, written after every successful `--apply` (install /
`agmind deploy --apply` / `agmind upgrade --apply`) and read by day-2 commands instead
of hardcoding profiles/domain. Non-secret by construction (0644, selection metadata
only — profiles/services/domain/edge_mode) and forward-compatible on read (unknown
fields written by a newer agmind version are dropped, not rejected).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agmind.core.files import write_text_atomic
from agmind.core.proc import sudo_argv, sudo_stdin_text

DEPLOY_STATE_FILENAME = "deploy-state.json"


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


def write_deploy_state(
    install_dir: Path,
    state: DeployState,
    sudo_password: str | None = None,
) -> None:
    """Write `deploy-state.json` atomically at 0644, with a sudo fallback.

    Tries `write_text_atomic` (core.files) first — the same primitive every other
    non-secret runtime file goes through. If `install_dir` is root-owned and the
    direct write raises `PermissionError`, falls back to the
    `sudo install -D -m 0644 <tmp> <path>` idiom (mirrors
    `agmind.deploy.runner._write_text_maybe_sudo`) when a `sudo_password` is supplied;
    otherwise the `PermissionError` is re-raised.
    """
    path = install_dir / DEPLOY_STATE_FILENAME
    content = state.model_dump_json(indent=2) + "\n"
    try:
        write_text_atomic(path, content, mode=0o644)
        return
    except PermissionError:
        if sudo_password is None:
            raise

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix=".agmind-deploy-state-", delete=False
    ) as handle:
        tmp_path = Path(handle.name)
        handle.write(content)
    try:
        result = subprocess.run(
            sudo_argv(["install", "-D", "-m", "0644", str(tmp_path), str(path)]),
            capture_output=True,
            text=True,
            check=False,
            input=sudo_stdin_text(sudo_password),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "sudo install failed").strip()
            raise OSError(f"cannot write {path} via sudo: {detail}")
    finally:
        tmp_path.unlink(missing_ok=True)


def load_deploy_state(install_dir: Path) -> DeployState | None:
    """Best-effort load of `deploy-state.json`.

    NEVER raises (mirrors `agmind.cli.install_state.load_prior_setup_state`): a missing
    or corrupt file just means "no known deploy state" — day-2 commands fall back to
    legacy state or an explicit `--profile`, they must not crash.
    """
    path = install_dir / DEPLOY_STATE_FILENAME
    if not path.exists():
        return None
    try:
        return DeployState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - never-raise contract (D-01)
        print(
            f"agmind: deploy state at {path} is unreadable ({exc}); proceeding without a "
            "known prior deploy state.",
            file=sys.stderr,
        )
        return None


__all__ = ["DeployState", "load_deploy_state", "write_deploy_state"]
