"""Validation helpers for deployment target contracts."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agmind.deploy.targets import DeploymentTarget
from agmind.services.renderer import load_descriptors, unknown_profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
KUBERNETES_DRY_RUN_SCRIPT = "scripts/proof/kubernetes_dry_run.py"
DEFAULT_KUBERNETES_PROOF_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"


@dataclass(frozen=True)
class DeploymentCheckIssue:
    """One deploy-target/proof validation issue."""

    severity: str
    kind: str
    message: str
    target_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "target_id": self.target_id,
            "message": self.message,
        }


@dataclass(frozen=True)
class DeploymentCheckReport:
    """Structured deploy-target/proof validation report."""

    target_count: int
    issues: tuple[DeploymentCheckIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[DeploymentCheckIssue, ...]:
        return self.by_severity("error")

    @property
    def warnings(self) -> tuple[DeploymentCheckIssue, ...]:
        return self.by_severity("warning")

    @property
    def infos(self) -> tuple[DeploymentCheckIssue, ...]:
        return self.by_severity("info")

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def info_count(self) -> int:
        return len(self.infos)

    def by_severity(self, severity: str) -> tuple[DeploymentCheckIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == severity)

    def messages(self, *, severity: str = "error") -> list[str]:
        return [issue.message for issue in self.by_severity(severity)]

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target_count": self.target_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "issues": [issue.to_json() for issue in self.issues],
        }


def _is_enforced_target(target: DeploymentTarget) -> bool:
    return target.status in {"supported", "experimental"}


def _path_exists(path: str, repo_root: Path) -> bool:
    return (repo_root / path).exists()


def _repository_service_names(repo_root: Path) -> frozenset[str]:
    descriptors = load_descriptors(repo_root / "templates" / "services")
    return frozenset(descriptors)


def _repository_service_descriptors(repo_root: Path) -> dict[str, Any]:
    return load_descriptors(repo_root / "templates" / "services")


def validate_deploy_targets(
    targets: Mapping[str, DeploymentTarget],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    """Validate deploy target references that should exist in the repository."""
    return validate_deploy_target_report(targets, repo_root=repo_root).messages()


def validate_deploy_target_report(
    targets: Mapping[str, DeploymentTarget],
    *,
    repo_root: Path = REPO_ROOT,
) -> DeploymentCheckReport:
    """Validate deploy target references and return structured issue metadata."""
    issues: list[DeploymentCheckIssue] = []
    service_names: frozenset[str] | None = None
    service_descriptors: dict[str, Any] | None = None
    supported = [target for target in targets.values() if target.status == "supported"]
    if not supported:
        issues.append(
            _issue(
                "error",
                "missing_supported_target",
                "deploy targets: at least one supported target is required",
            )
        )

    for target in targets.values():
        if service_descriptors is None:
            service_descriptors = _repository_service_descriptors(repo_root)
        missing_profiles = (
            unknown_profiles(
                service_descriptors,
                list(target.runtime.profiles),
            )
            if service_descriptors
            else []
        )
        if missing_profiles:
            issues.append(
                _issue(
                    "error",
                    "missing_runtime_profile",
                    f"{target.id}: runtime profiles reference unknown profile(s): "
                    f"{', '.join(missing_profiles)}",
                    target.id,
                )
            )

        if _is_enforced_target(target) and not target.verification.commands:
            issues.append(
                _issue(
                    "error",
                    "missing_verification_commands",
                    f"{target.id}: supported/experimental targets need verification commands",
                    target.id,
                )
            )

        if _is_enforced_target(target) and target.runtime.renderer.startswith("future-"):
            issues.append(
                _issue(
                    "error",
                    "future_renderer",
                    f"{target.id}: supported/experimental target uses future renderer",
                    target.id,
                )
            )

        if (
            _is_enforced_target(target)
            and target.provisioner.kind != "none"
            and target.provisioner.module
            and not _path_exists(target.provisioner.module, repo_root)
        ):
            issues.append(
                _issue(
                    "error",
                    "missing_provisioner_module",
                    f"{target.id}: provisioner module not found: {target.provisioner.module}",
                    target.id,
                )
            )

        if _is_enforced_target(target):
            for playbook in target.configurator.playbooks:
                if not _path_exists(playbook, repo_root):
                    issues.append(
                        _issue(
                            "error",
                            "missing_configurator_playbook",
                            f"{target.id}: configurator playbook not found: {playbook}",
                            target.id,
                        )
                    )

        if target.runtime.kind == "kubernetes":
            if target.runtime.excluded_services:
                if service_names is None:
                    service_names = _repository_service_names(repo_root)
                missing = tuple(
                    sorted(set(target.runtime.excluded_services).difference(service_names))
                )
                if missing:
                    issues.append(
                        _issue(
                            "error",
                            "missing_runtime_excluded_service",
                            f"{target.id}: runtime excluded_services reference unknown "
                            f"service(s): {', '.join(missing)}",
                            target.id,
                        )
                    )
            issues.extend(_validate_kubernetes_proof_artifacts(target))

    return DeploymentCheckReport(target_count=len(targets), issues=_sort_issues(issues))


def validate_kubernetes_proof_workflow(
    targets: Mapping[str, DeploymentTarget],
    *,
    workflow_path: Path = DEFAULT_KUBERNETES_PROOF_WORKFLOW,
) -> list[str]:
    """Validate that the manual Kubernetes proof workflow matches target contracts."""
    return validate_kubernetes_proof_workflow_report(
        targets,
        workflow_path=workflow_path,
    ).messages()


def validate_kubernetes_proof_workflow_report(
    targets: Mapping[str, DeploymentTarget],
    *,
    workflow_path: Path = DEFAULT_KUBERNETES_PROOF_WORKFLOW,
) -> DeploymentCheckReport:
    """Validate the manual Kubernetes proof workflow with structured issues."""
    if not workflow_path.exists():
        return DeploymentCheckReport(
            target_count=0,
            issues=(
                _issue(
                    "error",
                    "missing_workflow",
                    f"kubernetes proof workflow not found: {workflow_path}",
                ),
            ),
        )

    workflow = workflow_path.read_text(encoding="utf-8")
    issues: list[DeploymentCheckIssue] = []
    if "workflow_dispatch:" not in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_not_manual",
                "kubernetes proof workflow must be manual workflow_dispatch only",
            )
        )
    if "pull_request:" in workflow or "push:" in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_has_automatic_trigger",
                "kubernetes proof workflow must not run on push or pull_request",
            )
        )
    if "runs-on: [self-hosted, linux, x64, k3s]" not in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_missing_k3s_runner_label",
                "kubernetes proof workflow must require the k3s self-hosted runner label",
            )
        )
    if "actions/setup-python" in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_uses_setup_python",
                "kubernetes proof workflow must use runner-local python/uv",
            )
        )
    if ".venv/bin/python scripts/checks/kubernetes_render_check.py --strict" not in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_missing_strict_render_check",
                "kubernetes proof workflow must run strict Kubernetes render validation",
            )
        )
    if "actions/upload-artifact@" not in workflow:
        issues.append(
            _issue(
                "error",
                "workflow_missing_artifact_upload",
                "kubernetes proof workflow must upload proof artifacts",
            )
        )

    proof_targets = 0
    for target in targets.values():
        if target.runtime.kind != "kubernetes":
            continue
        for command in target.verification.commands:
            parts = _parse_command(command)
            if not _is_kubernetes_cluster_proof_command(parts):
                continue
            proof_targets += 1
            issues.extend(_validate_workflow_target_contract(target, parts, workflow))

    if proof_targets == 0:
        issues.append(
            _issue(
                "error",
                "workflow_missing_cluster_proof_target",
                "kubernetes proof workflow found no Kubernetes --require-cluster target",
            )
        )
    return DeploymentCheckReport(target_count=proof_targets, issues=_sort_issues(issues))


def format_deployment_check_report(report: DeploymentCheckReport, *, ok_label: str) -> str:
    """Render a compact text report for script/governance output."""
    if report.ok:
        return f"{ok_label} OK: {report.target_count} targets"

    lines = [f"{ok_label} validation failed:"]
    for issue in report.issues:
        prefix = issue.severity.upper()
        lines.append(f"- {issue.message} ({prefix}:{issue.kind})")
    return "\n".join(lines)


def _issue(
    severity: str,
    kind: str,
    message: str,
    target_id: str | None = None,
) -> DeploymentCheckIssue:
    return DeploymentCheckIssue(
        severity=severity,
        kind=kind,
        target_id=target_id,
        message=message,
    )


def _sort_issues(issues: list[DeploymentCheckIssue]) -> tuple[DeploymentCheckIssue, ...]:
    severity_order = {"error": 0, "warning": 1, "info": 2}
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                severity_order.get(issue.severity, 99),
                issue.target_id or "",
                issue.kind,
                issue.message,
            ),
        )
    )


def _validate_kubernetes_proof_artifacts(target: DeploymentTarget) -> list[DeploymentCheckIssue]:
    errors: list[DeploymentCheckIssue] = []
    proof_dirs: list[str] = []
    parsed_commands = tuple(_parse_command(command) for command in target.verification.commands)

    for parts in parsed_commands:
        if not _is_kubernetes_cluster_proof_command(parts):
            continue
        target_ids = _option_values(parts, "--target")
        if target.id not in target_ids:
            errors.append(
                _issue(
                    "error",
                    "missing_kubernetes_target_option",
                    f"{target.id}: Kubernetes proof command must include --target {target.id}",
                    target.id,
                )
            )
        artifact_dir = _option_value(parts, "--artifact-dir")
        if not artifact_dir:
            errors.append(
                _issue(
                    "error",
                    "missing_kubernetes_artifact_dir",
                    f"{target.id}: Kubernetes proof command must include --artifact-dir",
                    target.id,
                )
            )
            continue
        proof_dirs.append(artifact_dir)

    for artifact_dir in proof_dirs:
        expected_verifier = f"{KUBERNETES_DRY_RUN_SCRIPT} --verify-artifact-dir {artifact_dir}"
        if not any(
            _is_matching_artifact_verifier(parts, artifact_dir) for parts in parsed_commands
        ):
            errors.append(
                _issue(
                    "error",
                    "missing_kubernetes_artifact_verifier",
                    f"{target.id}: Kubernetes proof artifact dir lacks verifier command: "
                    f"{expected_verifier}",
                    target.id,
                )
            )
        expected_artifacts = _expected_kubernetes_proof_artifacts(target, artifact_dir)
        missing_artifacts = sorted(
            set(expected_artifacts).difference(target.verification.artifacts)
        )
        if missing_artifacts:
            errors.append(
                _issue(
                    "error",
                    "missing_kubernetes_proof_artifacts",
                    f"{target.id}: Kubernetes proof artifacts missing: "
                    f"{', '.join(missing_artifacts)}",
                    target.id,
                )
            )
    return errors


def _validate_workflow_target_contract(
    target: DeploymentTarget,
    proof_command: tuple[str, ...],
    workflow: str,
) -> list[DeploymentCheckIssue]:
    errors: list[DeploymentCheckIssue] = []
    artifact_dir = _option_value(proof_command, "--artifact-dir")
    expected_fragments = (
        f"--target {target.id}",
        "--require-cluster",
        "--require-amd-gpu",
        f"--artifact-dir {artifact_dir}",
        f"--verify-artifact-dir {artifact_dir}",
    )
    for fragment in expected_fragments:
        if fragment not in workflow:
            errors.append(
                _issue(
                    "error",
                    "workflow_missing_command_fragment",
                    f"{target.id}: Kubernetes proof workflow missing command fragment: {fragment}",
                    target.id,
                )
            )
    for artifact in target.verification.artifacts:
        if not _workflow_uploads_artifact(workflow, artifact):
            errors.append(
                _issue(
                    "error",
                    "workflow_missing_artifact",
                    f"{target.id}: Kubernetes proof workflow missing artifact: {artifact}",
                    target.id,
                )
            )
    verifier_command = (
        f".venv/bin/python {KUBERNETES_DRY_RUN_SCRIPT} --json --verify-artifact-dir {artifact_dir}"
    )
    if not _workflow_step_has_if_always(workflow, verifier_command):
        errors.append(
            _issue(
                "error",
                "workflow_verifier_not_always",
                f"{target.id}: Kubernetes proof workflow verifier must run with if: always()",
                target.id,
            )
        )
    verifier_report = f"{artifact_dir}/verification.json"
    if not _workflow_step_writes_verifier_report(workflow, verifier_command, verifier_report):
        errors.append(
            _issue(
                "error",
                "workflow_missing_verifier_report",
                f"{target.id}: Kubernetes proof workflow verifier must write verification.json",
                target.id,
            )
        )
    if not _workflow_uploads_artifact(workflow, verifier_report):
        errors.append(
            _issue(
                "error",
                "workflow_missing_verifier_artifact",
                f"{target.id}: Kubernetes proof workflow missing verifier artifact: {verifier_report}",
                target.id,
            )
        )
    if not _workflow_has_bundle_diagnostic_step(workflow, artifact_dir):
        errors.append(
            _issue(
                "error",
                "workflow_missing_bundle_diagnostics",
                f"{target.id}: Kubernetes proof workflow must summarize proof bundle contents "
                "with if: always()",
                target.id,
            )
        )
    return errors


def _parse_command(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return ()


def _is_kubernetes_dry_run_command(parts: tuple[str, ...]) -> bool:
    return KUBERNETES_DRY_RUN_SCRIPT in parts


def _is_kubernetes_cluster_proof_command(parts: tuple[str, ...]) -> bool:
    return _is_kubernetes_dry_run_command(parts) and "--require-cluster" in parts


def _is_matching_artifact_verifier(parts: tuple[str, ...], artifact_dir: str) -> bool:
    return (
        _is_kubernetes_dry_run_command(parts)
        and _option_value(parts, "--verify-artifact-dir") == artifact_dir
    )


def _option_value(parts: tuple[str, ...], option: str) -> str:
    values = _option_values(parts, option)
    return values[-1] if values else ""


def _option_values(parts: tuple[str, ...], option: str) -> tuple[str, ...]:
    values: list[str] = []
    for index, part in enumerate(parts):
        if part == option:
            if index + 1 < len(parts):
                values.append(parts[index + 1])
            continue
        prefix = f"{option}="
        if part.startswith(prefix):
            values.append(part[len(prefix) :])
    return tuple(value for value in values if value)


def _workflow_step_has_if_always(workflow: str, command: str) -> bool:
    command_index = workflow.find(command)
    if command_index < 0:
        return False
    step_start = workflow.rfind("\n      - name:", 0, command_index)
    if step_start < 0:
        step_start = 0
    next_step_start = workflow.find("\n      - name:", command_index)
    if next_step_start < 0:
        next_step_start = len(workflow)
    step = workflow[step_start:next_step_start]
    return "if: always()" in step


def _workflow_has_bundle_diagnostic_step(workflow: str, artifact_dir: str) -> bool:
    list_command = f"find {artifact_dir} -maxdepth 1 -type f -print | sort"
    checksum_command = f"cat {artifact_dir}/checksums.txt"
    list_command_index = workflow.find(list_command)
    if list_command_index < 0:
        return False
    step_start = workflow.rfind("\n      - name:", 0, list_command_index)
    if step_start < 0:
        step_start = 0
    next_step_start = workflow.find("\n      - name:", list_command_index)
    if next_step_start < 0:
        next_step_start = len(workflow)
    step = workflow[step_start:next_step_start]
    return "if: always()" in step and checksum_command in step


def _workflow_step_writes_verifier_report(
    workflow: str,
    command: str,
    report_path: str,
) -> bool:
    command_index = workflow.find(command)
    if command_index < 0:
        return False
    step_start = workflow.rfind("\n      - name:", 0, command_index)
    if step_start < 0:
        step_start = 0
    next_step_start = workflow.find("\n      - name:", command_index)
    if next_step_start < 0:
        next_step_start = len(workflow)
    step = workflow[step_start:next_step_start]
    return f"tee {report_path}" in step and "PIPESTATUS[0]" in step


def _workflow_uploads_artifact(workflow: str, artifact: str) -> bool:
    upload_index = workflow.find("actions/upload-artifact@")
    if upload_index < 0:
        return False
    step_start = workflow.rfind("\n      - name:", 0, upload_index)
    if step_start < 0:
        step_start = 0
    next_step_start = workflow.find("\n      - name:", upload_index)
    if next_step_start < 0:
        next_step_start = len(workflow)
    step = workflow[step_start:next_step_start]
    return artifact in step


def _expected_kubernetes_proof_artifacts(
    target: DeploymentTarget,
    artifact_dir: str,
) -> tuple[str, ...]:
    return (
        f"{artifact_dir}/{target.id}.yaml",
        f"{artifact_dir}/{target.id}.dry-run.json",
        f"{artifact_dir}/proof-command.txt",
        f"{artifact_dir}/run-metadata.json",
        f"{artifact_dir}/summary.json",
        f"{artifact_dir}/checksums.txt",
    )
