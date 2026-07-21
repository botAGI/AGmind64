"""Leaf helpers shared by every install-step submodule.

Imports nothing from ``agmind.install.steps`` itself, so the step submodules can
pull these in without an import cycle. Re-exported from the package ``__init__``
so ``from agmind.install.steps import _make_event`` keeps resolving.
"""

from __future__ import annotations

from agmind.core.proc import sudo_stdin_bytes
from agmind.install.orchestrator import InstallConfig, ProgressEvent, ProgressKind


def _make_event(
    step_id: str,
    kind: ProgressKind,
    text: str,
    pct: int | None = None,
) -> ProgressEvent:
    """Local import to avoid circular: создать ProgressEvent без import outside."""
    from agmind.install.orchestrator import ProgressEvent

    return ProgressEvent(step_id=step_id, kind=kind, text=text, progress_pct=pct)


def _sudo_stdin_payload(config: InstallConfig) -> bytes | None:
    if config.sudo_password is None:
        return None
    return sudo_stdin_bytes(config.sudo_password)
