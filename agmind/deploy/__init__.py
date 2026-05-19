"""AGmind deploy subsystem (Phase L.B).

Idempotent deploys с automatic snapshot + healthcheck wait + rollback при failure.
Решает user pain "ручные деплои + потеря времени на реинсталлы и чистку артефактов"
(см. memory feedback-tui-devops).

Modules:
    snapshot — save/restore deployment state (compose + descriptors + env + images)
    diff — compute changes между текущим и rendered compose
    runner — orchestration: snapshot → render → diff → apply → healthcheck → rollback

CLI: `agmind deploy --diff | --apply | --rollback` (agmind/cli/deploy_cmd.py).
"""

from __future__ import annotations

from agmind.deploy.diff import ComposeDiff, compute_diff, format_diff
from agmind.deploy.runner import DeployResult, deploy, rollback
from agmind.deploy.snapshot import Snapshot, SnapshotManager

__all__ = [
    "ComposeDiff",
    "DeployResult",
    "Snapshot",
    "SnapshotManager",
    "compute_diff",
    "deploy",
    "format_diff",
    "rollback",
]
