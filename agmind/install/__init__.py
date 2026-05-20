"""Phase N: end-to-end installer (single-command install flow).

Orchestrates: preflight → system bootstrap (Ansible, sudo) → image pull →
model pull → compose up + healthcheck → summary. Каждый step emits
events для TUI live progress view.

UX: `agmind install` запускает TUI wizard (re-use Phase J), после Apply
pushes InstallProgressScreen с live прогрессом. Sudo password собирается
один раз перед началом и передаётся в Ansible через pipe.

Архитектура:
    InstallOrchestrator: sequence steps, runs them under one
                         ProgressCallback contract
    InstallStep: ABC с .run(callback, ctx)
    ProgressEvent: typed event (log / step_start / step_done /
                   progress / error)

Этот модуль не зависит от Textual — UI слой подписывается на events
через callback. Это позволяет CLI-only режим `agmind install --no-tui`
тоже работать (хотя UX будет хуже).
"""

from agmind.install.orchestrator import (
    InstallConfig,
    InstallOrchestrator,
    InstallResult,
    InstallStep,
    InstallStepResult,
    ProgressEvent,
    ProgressKind,
)

__all__ = [
    "InstallConfig",
    "InstallOrchestrator",
    "InstallResult",
    "InstallStep",
    "InstallStepResult",
    "ProgressEvent",
    "ProgressKind",
]
