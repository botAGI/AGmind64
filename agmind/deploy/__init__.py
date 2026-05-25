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
from agmind.deploy.gc import (
    GcReport,
    format_gc_report,
    gc_all,
    gc_containers,
    gc_images,
    gc_models,
    gc_networks,
    gc_volumes,
)
from agmind.deploy.proxmox_inventory import (
    DEFAULT_INVENTORY_PATH,
    ProxmoxInventoryError,
    inventory_from_tofu_outputs,
    load_tofu_output_json,
    render_inventory_yaml,
    write_inventory_from_tofu_outputs,
)
from agmind.deploy.runner import DeployResult, deploy, rollback
from agmind.deploy.snapshot import Snapshot, SnapshotManager
from agmind.deploy.target_checks import (
    DeploymentCheckIssue,
    DeploymentCheckReport,
    format_deployment_check_report,
    validate_deploy_target_report,
    validate_deploy_targets,
    validate_kubernetes_proof_workflow_report,
)
from agmind.deploy.targets import (
    DEFAULT_DEPLOY_TARGETS_DIR,
    ConfiguratorKind,
    DeploymentConfigurator,
    DeploymentProvisioner,
    DeploymentRuntime,
    DeploymentTarget,
    DeploymentVerification,
    ProvisionerKind,
    RuntimeKind,
    SecretsProfile,
    StorageProfile,
    TargetStatus,
    load_deploy_targets,
)

__all__ = [
    "ComposeDiff",
    "ConfiguratorKind",
    "DEFAULT_DEPLOY_TARGETS_DIR",
    "DEFAULT_INVENTORY_PATH",
    "DeployResult",
    "DeploymentConfigurator",
    "DeploymentCheckIssue",
    "DeploymentCheckReport",
    "DeploymentProvisioner",
    "DeploymentRuntime",
    "DeploymentTarget",
    "DeploymentVerification",
    "GcReport",
    "ProvisionerKind",
    "ProxmoxInventoryError",
    "RuntimeKind",
    "SecretsProfile",
    "Snapshot",
    "SnapshotManager",
    "StorageProfile",
    "TargetStatus",
    "compute_diff",
    "deploy",
    "format_diff",
    "format_deployment_check_report",
    "format_gc_report",
    "gc_all",
    "gc_containers",
    "gc_images",
    "gc_models",
    "gc_networks",
    "gc_volumes",
    "inventory_from_tofu_outputs",
    "load_deploy_targets",
    "load_tofu_output_json",
    "render_inventory_yaml",
    "rollback",
    "validate_deploy_targets",
    "validate_deploy_target_report",
    "validate_kubernetes_proof_workflow_report",
    "write_inventory_from_tofu_outputs",
]
