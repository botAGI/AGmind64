"""Resolve a saved setup state for `agmind install --from-state`.

Domain logic extracted from the CLI handler (keeps handlers thin, I.13): load
the JSON state, expand selected profiles into their service closure, and
validate the selection. Operator-facing failures raise :class:`StateResolveError`
with a ready-to-print message and exit code; the CLI handler only maps that to
``typer.echo(..., err=True)`` + ``typer.Exit``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agmind.cli.tui.setup_wizard import SetupState, expand_selected_services_for_setup


class StateResolveError(Exception):
    """A --from-state load/validation failure with an operator message."""

    def __init__(self, message: str, *, code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def load_setup_state_from_file(from_state: Path) -> SetupState:
    """Load and validate a saved :class:`SetupState`, expanding profiles.

    Raises:
        StateResolveError: unreadable/unparseable file, unknown profiles or
            services, or an empty selection.
    """
    try:
        from_state.stat()
    except OSError as exc:
        raise StateResolveError(f"cannot read --from-state {from_state}: {exc}") from exc
    try:
        state = SetupState.from_json(from_state)
    except Exception as exc:  # noqa: BLE001 — surface any parse error as a clean message
        raise StateResolveError(f"cannot load --from-state {from_state}: {exc}") from exc

    from agmind.services.renderer import (
        load_descriptors,
        select_services,
        unknown_profiles,
    )

    if not state.services and state.profiles:
        descriptors = load_descriptors()
        missing_profiles = unknown_profiles(descriptors, state.profiles)
        if missing_profiles:
            raise StateResolveError(
                "unknown selected profiles in --from-state: " + ", ".join(missing_profiles)
            )
        selected_services = sorted(select_services(descriptors, profiles=state.profiles))
        if not selected_services:
            raise StateResolveError(
                "no services match profiles in --from-state: " + ", ".join(state.profiles)
            )
        state.services = selected_services
        state.profiles = []

    if state.services:
        descriptors = load_descriptors()
        missing_services = sorted(set(state.services).difference(descriptors))
        if missing_services:
            raise StateResolveError(
                "unknown selected services in --from-state: " + ", ".join(missing_services)
            )
        try:
            state.services = expand_selected_services_for_setup(list(state.services))
        except ValueError as exc:
            raise StateResolveError(f"invalid selected services in --from-state: {exc}") from exc

    if not state.services and not state.profiles:
        raise StateResolveError("no selected services in --from-state")

    return state


def load_prior_setup_state(state_path: Path) -> SetupState | None:
    """Best-effort load of the previously-saved setup state, for an interactive re-run.

    Unlike :func:`load_setup_state_from_file` (the explicit ``--from-state`` path), this
    NEVER raises: a missing or corrupt prior state just means "start from defaults". It
    is used to pre-select the previously-deployed services so re-running the installer to
    add/replace a component does not silently drop the running stack via
    ``docker compose up --remove-orphans``. Returns None if absent/unreadable.
    """
    if not state_path.exists():
        return None
    try:
        return SetupState.from_json(state_path)
    except Exception as exc:  # noqa: BLE001 — corrupt/old state → fall back to defaults
        # A PRESENT-but-corrupt state must not fall back SILENTLY: the whole point of this
        # pre-selection is to stop a re-deploy from --remove-orphans'ing the running stack, so
        # warn loudly before that happens (review MEDIUM install-state-corrupt-orphan-removal).
        print(
            f"agmind: prior setup state at {state_path} is unreadable ({exc}); proceeding from "
            "defaults — a re-deploy may --remove-orphans the previously deployed stack.",
            file=sys.stderr,
        )
        return None


__all__ = ["StateResolveError", "load_prior_setup_state", "load_setup_state_from_file"]
