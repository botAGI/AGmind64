"""Server-side Kubernetes dry-run proof for deployment targets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agmind.core.files import write_text_atomic
from agmind.core.proc import CommandResult
from agmind.deploy.targets import DeploymentTarget, load_deploy_targets
from agmind.services.kubernetes_checks import (
    _empty_warning_summary,
    validate_kubernetes_render_targets,
)
from agmind.services.kubernetes_renderer import render_to_string
from agmind.services.renderer import DEFAULT_SERVICES_DIR

DryRunStatus = Literal["passed", "failed", "skipped"]
CommandRunner = Callable[[tuple[str, ...], Path], CommandResult]
AMD_GPU_RESOURCE_NAME = "amd.com/gpu"


@dataclass(frozen=True)
class KubernetesGpuPreflightReport:
    """Cluster GPU resource evidence for one Kubernetes deployment target."""

    status: DryRunStatus
    command: tuple[str, ...]
    resource_name: str = AMD_GPU_RESOURCE_NAME
    allocatable: int = 0
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": list(self.command),
            "resource_name": self.resource_name,
            "allocatable": self.allocatable,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class KubernetesDryRunWarningRecord:
    """Actionable render warning evidence included in dry-run artifacts."""

    service: str
    code: str
    severity: str
    message: str
    remediation: str
    expected: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "remediation": self.remediation,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class KubernetesDryRunTargetReport:
    """Server dry-run evidence for one Kubernetes deployment target."""

    target_id: str
    status: DryRunStatus
    command: tuple[str, ...]
    warning_summary: dict[str, int]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    manifest_path: Path | None = None
    report_path: Path | None = None
    manifest_bytes: int | None = None
    manifest_sha256: str = ""
    gpu_preflight: KubernetesGpuPreflightReport | None = None
    warnings: tuple[KubernetesDryRunWarningRecord, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "command": list(self.command),
            "warning_summary": dict(self.warning_summary),
            "warnings": [warning.to_json() for warning in self.warnings],
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "manifest_path": str(self.manifest_path) if self.manifest_path else "",
            "report_path": str(self.report_path) if self.report_path else "",
            "manifest_bytes": self.manifest_bytes,
            "manifest_sha256": self.manifest_sha256,
            "gpu_preflight": self.gpu_preflight.to_json() if self.gpu_preflight else {},
        }


@dataclass(frozen=True)
class KubernetesDryRunReport:
    """Aggregate server dry-run evidence."""

    targets: tuple[KubernetesDryRunTargetReport, ...]
    target_ids: tuple[str, ...] = ()
    require_cluster: bool = False
    require_amd_gpu: bool = False
    kubectl: str = "kubectl"
    kube_context: str = ""
    namespace: str = "agmind"
    artifact_dir: Path | None = None
    proof_command: tuple[str, ...] = ()
    run_metadata: Mapping[str, Any] | None = None
    run_metadata_path: Path | None = None

    @property
    def ok(self) -> bool:
        if any(target.status == "failed" for target in self.targets):
            return False
        if self.require_cluster and any(target.status == "skipped" for target in self.targets):
            return False
        return True

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target_ids": list(self.target_ids),
            "require_cluster": self.require_cluster,
            "require_amd_gpu": self.require_amd_gpu,
            "kubectl": self.kubectl,
            "kube_context": self.kube_context,
            "namespace": self.namespace,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir else "",
            "summary_path": str(self.artifact_dir / "summary.json") if self.artifact_dir else "",
            "checksum_path": str(self.artifact_dir / "checksums.txt") if self.artifact_dir else "",
            "proof_command": list(self.proof_command),
            "proof_command_path": (
                str(self.artifact_dir / "proof-command.txt") if self.artifact_dir else ""
            ),
            "run_metadata_path": str(self.run_metadata_path) if self.run_metadata_path else "",
            "run_metadata": dict(self.run_metadata or {}),
            "targets": [target.to_json() for target in self.targets],
        }


@dataclass(frozen=True)
class KubernetesArtifactVerificationFile:
    """Checksum verification result for one persisted dry-run artifact."""

    path: str
    expected_sha256: str
    actual_sha256: str
    ok: bool
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass(frozen=True)
class KubernetesDryRunArtifactVerificationReport:
    """Integrity verification result for a dry-run artifact directory."""

    artifact_dir: Path
    summary_path: Path
    checksum_path: Path
    files: tuple[KubernetesArtifactVerificationFile, ...]
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and all(file.ok for file in self.files)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "artifact_dir": str(self.artifact_dir),
            "summary_path": str(self.summary_path),
            "checksum_path": str(self.checksum_path),
            "files": [file.to_json() for file in self.files],
            "errors": list(self.errors),
        }


def run_kubernetes_server_dry_run(
    targets: Mapping[str, DeploymentTarget] | None = None,
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    kubectl: str | None = "kubectl",
    kube_context: str = "",
    namespace: str = "agmind",
    require_cluster: bool = False,
    require_amd_gpu: bool = False,
    target_ids: Sequence[str] = (),
    runner: CommandRunner | None = None,
    artifact_dir: Path | None = None,
) -> KubernetesDryRunReport:
    """Render Kubernetes targets and run server-side kubectl dry-run."""
    target_map = targets if targets is not None else load_deploy_targets()
    selected_targets = _select_kubernetes_targets(target_map, target_ids=target_ids)
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    reports: list[KubernetesDryRunTargetReport] = []
    for target in selected_targets:
        reports.append(
            _run_one_target(
                target,
                services_dir=services_dir,
                kubectl=kubectl,
                kube_context=kube_context,
                namespace=namespace,
                require_amd_gpu=require_amd_gpu,
                runner=runner,
                artifact_dir=artifact_dir,
            )
        )
    run_metadata = _collect_run_metadata()
    report = KubernetesDryRunReport(
        targets=tuple(reports),
        target_ids=tuple(target.id for target in selected_targets),
        require_cluster=require_cluster,
        require_amd_gpu=require_amd_gpu,
        kubectl=kubectl or "",
        kube_context=kube_context,
        namespace=namespace,
        artifact_dir=artifact_dir,
        run_metadata=run_metadata,
        run_metadata_path=artifact_dir / "run-metadata.json" if artifact_dir else None,
        proof_command=_build_proof_command(
            target_ids=tuple(target.id for target in selected_targets),
            require_cluster=require_cluster,
            require_amd_gpu=require_amd_gpu,
            artifact_dir=artifact_dir,
            namespace=namespace,
            kubectl=kubectl or "",
            kube_context=kube_context,
        ),
    )
    if artifact_dir is not None:
        if report.run_metadata_path is not None:
            _write_json(report.run_metadata_path, dict(run_metadata))
        proof_command_path = artifact_dir / "proof-command.txt"
        write_text_atomic(proof_command_path, shlex.join(report.proof_command) + "\n")
        summary_path = artifact_dir / "summary.json"
        _write_json(summary_path, report.to_json())
        _write_artifact_checksums(
            report,
            artifact_dir=artifact_dir,
            summary_path=summary_path,
            run_metadata_path=report.run_metadata_path,
            checksum_path=artifact_dir / "checksums.txt",
        )
    return report


def verify_kubernetes_dry_run_artifacts(
    artifact_dir: Path,
) -> KubernetesDryRunArtifactVerificationReport:
    """Verify persisted dry-run evidence files inside an artifact directory."""
    summary_path = artifact_dir / "summary.json"
    checksum_path = artifact_dir / "checksums.txt"
    errors: list[str] = []
    files: list[KubernetesArtifactVerificationFile] = []

    if not summary_path.exists():
        errors.append(f"missing artifact: {summary_path.name}")
    if not checksum_path.exists():
        errors.append(f"missing artifact: {checksum_path.name}")

    if checksum_path.exists():
        checksum_files, checksum_errors = _verify_checksum_file(
            artifact_dir,
            checksum_path=checksum_path,
        )
        files.extend(checksum_files)
        errors.extend(checksum_errors)

    summary_payload: dict[str, Any] | None = None
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid summary.json: {exc}")
        else:
            if isinstance(loaded, dict):
                summary_payload = loaded
            else:
                errors.append("invalid summary.json: expected object")

    if summary_payload is not None:
        checksummed_paths = {file.path for file in files}
        required_paths = _required_artifact_paths(summary_payload)
        errors.extend(_verify_summary_consistency(summary_payload))
        errors.extend(
            _verify_required_artifact_presence(
                artifact_dir,
                required_paths=required_paths,
            )
        )
        errors.extend(
            _verify_required_checksum_entries(
                artifact_dir,
                required_paths=required_paths,
                checksummed_paths=checksummed_paths,
            )
        )
        errors.extend(_verify_proof_command_artifact(artifact_dir, summary_payload))
        errors.extend(_verify_run_metadata_artifact(artifact_dir, summary_payload))
        errors.extend(_verify_target_report_artifacts(artifact_dir, summary_payload))
        errors.extend(_verify_target_manifest_metadata(artifact_dir, summary_payload))

    return KubernetesDryRunArtifactVerificationReport(
        artifact_dir=artifact_dir,
        summary_path=summary_path,
        checksum_path=checksum_path,
        files=tuple(files),
        errors=tuple(errors),
    )


def format_kubernetes_dry_run_report(report: KubernetesDryRunReport) -> str:
    """Render an operator-readable dry-run report."""
    lines: list[str] = []
    for target in report.targets:
        command = " ".join(target.command) if target.command else "<not run>"
        summary = ", ".join(
            f"{severity}={target.warning_summary.get(severity, 0)}"
            for severity in _empty_warning_summary()
        )
        lines.append(
            f"{target.target_id}: {target.status.upper()} (warnings: {summary}, command: {command})"
        )
        if target.gpu_preflight is not None:
            preflight = target.gpu_preflight
            lines.append(
                "  gpu preflight: "
                f"{preflight.status.upper()} "
                f"({preflight.resource_name}={preflight.allocatable}, "
                f"command: {' '.join(preflight.command)})"
            )
        if target.returncode is not None:
            lines.append(f"  returncode: {target.returncode}")
        if target.stdout:
            lines.append(f"  stdout: {target.stdout}")
        if target.stderr:
            lines.append(f"  stderr: {target.stderr}")
    if report.ok:
        lines.append(f"kubernetes server dry-run OK: {len(report.targets)} targets")
    else:
        failed = sum(1 for target in report.targets if target.status == "failed")
        skipped = sum(1 for target in report.targets if target.status == "skipped")
        lines.append(
            "kubernetes server dry-run FAILED: "
            f"{failed} failed, {skipped} skipped, require_cluster={report.require_cluster}"
        )
    return "\n".join(lines)


def format_kubernetes_dry_run_artifact_verification(
    report: KubernetesDryRunArtifactVerificationReport,
) -> str:
    """Render an operator-readable artifact verification report."""
    if report.ok:
        return f"kubernetes dry-run artifact bundle OK: {len(report.files)} files"
    lines = [f"kubernetes dry-run artifact bundle FAILED: {len(report.errors)} errors"]
    for error in report.errors:
        lines.append(f"  ERROR: {error}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-compatible entry point for Kubernetes server dry-run evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--require-cluster",
        action="store_true",
        help="Fail if kubectl or cluster access is unavailable",
    )
    parser.add_argument("--kubectl", default="kubectl", help="kubectl binary path/name")
    parser.add_argument("--context", default="", help="Optional kubectl context")
    parser.add_argument("--namespace", default="agmind", help="Rendered namespace")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Deployment target id to dry-run; repeat for multiple targets",
    )
    parser.add_argument(
        "--require-amd-gpu",
        action="store_true",
        help=f"Require cluster nodes to expose allocatable {AMD_GPU_RESOURCE_NAME}",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Directory for rendered manifests and JSON evidence",
    )
    parser.add_argument(
        "--verify-artifact-dir",
        type=Path,
        default=None,
        help="Verify an existing dry-run artifact directory instead of running kubectl",
    )
    args = parser.parse_args(tuple(argv if argv is not None else sys.argv[1:]))

    if args.verify_artifact_dir is not None:
        verification_report = verify_kubernetes_dry_run_artifacts(args.verify_artifact_dir)
        if args.json:
            print(json.dumps(verification_report.to_json(), indent=2, ensure_ascii=False))
        else:
            print(format_kubernetes_dry_run_artifact_verification(verification_report))
        return 0 if verification_report.ok else 1

    try:
        report = run_kubernetes_server_dry_run(
            kubectl=args.kubectl,
            kube_context=args.context,
            namespace=args.namespace,
            require_cluster=args.require_cluster,
            require_amd_gpu=args.require_amd_gpu,
            target_ids=tuple(args.target),
            artifact_dir=args.artifact_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_kubernetes_dry_run_report(report))
    return 0 if report.ok else 1


def _select_kubernetes_targets(
    targets: Mapping[str, DeploymentTarget],
    *,
    target_ids: Sequence[str],
) -> tuple[DeploymentTarget, ...]:
    if not target_ids:
        return tuple(target for target in targets.values() if target.runtime.kind == "kubernetes")

    unknown = tuple(target_id for target_id in target_ids if target_id not in targets)
    if unknown:
        raise ValueError(f"unknown deployment target(s): {', '.join(unknown)}")

    selected: list[DeploymentTarget] = []
    seen: set[str] = set()
    for target_id in target_ids:
        if target_id in seen:
            continue
        seen.add(target_id)
        target = targets[target_id]
        if target.runtime.kind != "kubernetes":
            raise ValueError(f"deployment target is not Kubernetes-backed: {target_id}")
        selected.append(target)
    return tuple(selected)


def _run_one_target(
    target: DeploymentTarget,
    *,
    services_dir: Path,
    kubectl: str | None,
    kube_context: str,
    namespace: str,
    require_amd_gpu: bool,
    runner: CommandRunner | None,
    artifact_dir: Path | None,
) -> KubernetesDryRunTargetReport:
    warning_summary, warning_records = _warning_evidence_for_target(
        target,
        services_dir=services_dir,
    )
    command = _build_kubectl_command(kubectl or "kubectl", kube_context=kube_context)
    manifest_path = _manifest_path(target, artifact_dir=artifact_dir)
    report_path = _report_path(target, artifact_dir=artifact_dir)
    manifest_bytes: int | None = None
    manifest_sha256 = ""
    try:
        rendered = render_to_string(
            profiles=list(target.runtime.profiles),
            exclude_services=list(target.runtime.excluded_services),
            services_dir=services_dir,
            namespace=namespace,
        )
    except Exception as exc:
        report = KubernetesDryRunTargetReport(
            target_id=target.id,
            status="failed",
            command=command,
            warning_summary=warning_summary,
            warnings=warning_records,
            stderr=f"render failed: {exc}",
            manifest_path=manifest_path,
            report_path=report_path,
        )
        _write_target_report(report)
        return report

    if manifest_path is not None:
        write_text_atomic(manifest_path, rendered)
        manifest_bytes, manifest_sha256 = _artifact_size_and_sha256(manifest_path)

    if not _kubectl_available(kubectl, runner=runner):
        skipped_gpu_preflight = (
            KubernetesGpuPreflightReport(
                status="skipped",
                command=_build_gpu_preflight_command(
                    kubectl or "kubectl", kube_context=kube_context
                ),
                stderr=f"kubectl not found: {kubectl or '<unset>'}",
            )
            if require_amd_gpu
            else None
        )
        report = KubernetesDryRunTargetReport(
            target_id=target.id,
            status="skipped",
            command=command,
            warning_summary=warning_summary,
            warnings=warning_records,
            stderr=f"kubectl not found: {kubectl or '<unset>'}",
            manifest_path=manifest_path,
            report_path=report_path,
            manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256,
            gpu_preflight=skipped_gpu_preflight,
        )
        _write_target_report(report)
        return report

    gpu_preflight: KubernetesGpuPreflightReport | None = None
    if require_amd_gpu:
        gpu_preflight = _run_amd_gpu_preflight(
            kubectl or "kubectl",
            kube_context=kube_context,
            runner=runner,
            manifest_path=manifest_path,
        )
        if gpu_preflight.status != "passed":
            report = KubernetesDryRunTargetReport(
                target_id=target.id,
                status=gpu_preflight.status,
                command=command,
                warning_summary=warning_summary,
                warnings=warning_records,
                returncode=gpu_preflight.returncode,
                stdout=gpu_preflight.stdout,
                stderr=gpu_preflight.stderr,
                manifest_path=manifest_path,
                report_path=report_path,
                manifest_bytes=manifest_bytes,
                manifest_sha256=manifest_sha256,
                gpu_preflight=gpu_preflight,
            )
            _write_target_report(report)
            return report

    if manifest_path is not None:
        command = (*command, str(manifest_path))
        result = (runner or _subprocess_runner)(command, manifest_path)
    else:
        with tempfile.TemporaryDirectory(prefix="agmind-k8s-dry-run-") as temp_dir:
            manifest = Path(temp_dir) / f"{target.id}.yaml"
            manifest.write_text(rendered, encoding="utf-8")
            command = (*command, str(manifest))
            result = (runner or _subprocess_runner)(command, manifest)

    if result.returncode == 0:
        status: DryRunStatus = "passed"
    elif _cluster_unavailable(result.stderr):
        status = "skipped"
    else:
        status = "failed"
    report = KubernetesDryRunTargetReport(
        target_id=target.id,
        status=status,
        command=command,
        warning_summary=warning_summary,
        warnings=warning_records,
        returncode=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        manifest_path=manifest_path,
        report_path=report_path,
        manifest_bytes=manifest_bytes,
        manifest_sha256=manifest_sha256,
        gpu_preflight=gpu_preflight,
    )
    _write_target_report(report)
    return report


def _warning_evidence_for_target(
    target: DeploymentTarget,
    *,
    services_dir: Path,
) -> tuple[dict[str, int], tuple[KubernetesDryRunWarningRecord, ...]]:
    report = validate_kubernetes_render_targets(
        {target.id: target},
        services_dir=services_dir,
        strict=False,
    )
    if not report.targets:
        return _empty_warning_summary(), ()
    target_report = report.targets[0]
    expected_codes = frozenset(target.verification.expected_warning_codes)
    expected_warnings = frozenset(
        (warning.service, warning.code) for warning in target.verification.expected_warnings
    )
    warning_records = tuple(
        KubernetesDryRunWarningRecord(
            service=warning.service,
            code=warning.code,
            severity=warning.severity,
            message=warning.message,
            remediation=warning.remediation,
            expected=warning.code in expected_codes
            or (warning.service, warning.code) in expected_warnings,
        )
        for warning in target_report.warnings
    )
    return dict(target_report.warning_summary), warning_records


def _build_kubectl_command(kubectl: str, *, kube_context: str) -> tuple[str, ...]:
    if kube_context:
        return (kubectl, "--context", kube_context, "apply", "--dry-run=server", "-f")
    return (kubectl, "apply", "--dry-run=server", "-f")


def _build_gpu_preflight_command(kubectl: str, *, kube_context: str) -> tuple[str, ...]:
    if kube_context:
        return (kubectl, "--context", kube_context, "get", "nodes", "-o", "json")
    return (kubectl, "get", "nodes", "-o", "json")


def _build_proof_command(
    *,
    target_ids: Sequence[str],
    require_cluster: bool,
    require_amd_gpu: bool,
    artifact_dir: Path | None,
    namespace: str,
    kubectl: str,
    kube_context: str,
) -> tuple[str, ...]:
    command: list[str] = ["scripts/proof/kubernetes_dry_run.py"]
    for target_id in target_ids:
        command.extend(("--target", target_id))
    if require_cluster:
        command.append("--require-cluster")
    if require_amd_gpu:
        command.append("--require-amd-gpu")
    if artifact_dir is not None:
        command.extend(("--artifact-dir", str(artifact_dir)))
    command.extend(("--namespace", namespace))
    command.extend(("--kubectl", kubectl))
    if kube_context:
        command.extend(("--context", kube_context))
    return tuple(command)


def _run_amd_gpu_preflight(
    kubectl: str,
    *,
    kube_context: str,
    runner: CommandRunner | None,
    manifest_path: Path | None,
) -> KubernetesGpuPreflightReport:
    command = _build_gpu_preflight_command(kubectl, kube_context=kube_context)
    probe_path = manifest_path or Path(".")
    result = (runner or _subprocess_runner)(command, probe_path)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        status: DryRunStatus = "skipped" if _cluster_unavailable(stderr) else "failed"
        return KubernetesGpuPreflightReport(
            status=status,
            command=command,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    try:
        allocatable = _amd_gpu_allocatable_from_nodes_json(stdout)
    except ValueError as exc:
        return KubernetesGpuPreflightReport(
            status="failed",
            command=command,
            returncode=result.returncode,
            stdout=stdout,
            stderr=str(exc),
        )
    if allocatable <= 0:
        return KubernetesGpuPreflightReport(
            status="failed",
            command=command,
            allocatable=allocatable,
            returncode=result.returncode,
            stdout=stdout,
            stderr=f"required allocatable {AMD_GPU_RESOURCE_NAME} was not found on any node",
        )
    return KubernetesGpuPreflightReport(
        status="passed",
        command=command,
        allocatable=allocatable,
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _amd_gpu_allocatable_from_nodes_json(payload: str) -> int:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid kubectl nodes JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid kubectl nodes JSON: expected object")
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("invalid kubectl nodes JSON: expected items list")
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status", {})
        if not isinstance(status, dict):
            continue
        allocatable = status.get("allocatable", {})
        if not isinstance(allocatable, dict):
            continue
        total += _resource_quantity_as_int(allocatable.get(AMD_GPU_RESOURCE_NAME))
    return total


def _resource_quantity_as_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return 0


def _kubectl_available(kubectl: str | None, *, runner: CommandRunner | None) -> bool:
    if runner is not None:
        return kubectl is not None
    if not kubectl:
        return False
    if "/" in kubectl:
        return Path(kubectl).exists()
    return shutil.which(kubectl) is not None


def _subprocess_runner(command: tuple[str, ...], manifest: Path) -> CommandResult:
    del manifest
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return CommandResult(
            returncode=1,
            stdout="",
            stderr=f"kubectl execution failed: {exc}",
        )
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _cluster_unavailable(stderr: str) -> bool:
    lowered = stderr.lower()
    markers = (
        "no configuration has been provided",
        "connection to the server",
        "connection refused",
        "couldn't get current server api group list",
        "i/o timeout",
        "context deadline exceeded",
        "no such host",
    )
    return any(marker in lowered for marker in markers)


def _manifest_path(target: DeploymentTarget, *, artifact_dir: Path | None) -> Path | None:
    if artifact_dir is None:
        return None
    return artifact_dir / f"{target.id}.yaml"


def _report_path(target: DeploymentTarget, *, artifact_dir: Path | None) -> Path | None:
    if artifact_dir is None:
        return None
    return artifact_dir / f"{target.id}.dry-run.json"


def _write_target_report(report: KubernetesDryRunTargetReport) -> None:
    if report.report_path is None:
        return
    _write_json(report.report_path, report.to_json())


def _artifact_size_and_sha256(path: Path) -> tuple[int, str]:
    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


def _write_artifact_checksums(
    report: KubernetesDryRunReport,
    *,
    artifact_dir: Path,
    summary_path: Path,
    run_metadata_path: Path | None,
    checksum_path: Path,
) -> None:
    paths: list[Path] = []
    for target in report.targets:
        if target.manifest_path is not None and target.manifest_path.exists():
            paths.append(target.manifest_path)
        if target.report_path is not None and target.report_path.exists():
            paths.append(target.report_path)
    proof_command_path = artifact_dir / "proof-command.txt"
    if proof_command_path.exists():
        paths.append(proof_command_path)
    if run_metadata_path is not None and run_metadata_path.exists():
        paths.append(run_metadata_path)
    if summary_path.exists():
        paths.append(summary_path)
    lines = []
    for path in sorted(paths, key=lambda item: item.relative_to(artifact_dir).as_posix()):
        relative_path = path.relative_to(artifact_dir).as_posix()
        _, digest = _artifact_size_and_sha256(path)
        lines.append(f"{digest}  {relative_path}")
    write_text_atomic(checksum_path, "\n".join(lines) + "\n")


def _verify_checksum_file(
    artifact_dir: Path,
    *,
    checksum_path: Path,
) -> tuple[list[KubernetesArtifactVerificationFile], list[str]]:
    files: list[KubernetesArtifactVerificationFile] = []
    errors: list[str] = []
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            errors.append(f"invalid checksums.txt line {line_number}: expected '<sha256>  <path>'")
            continue
        expected_sha256, relative_path = parts[0], parts[1].strip()
        member_path, path_error = _checksum_member_path(artifact_dir, relative_path)
        if path_error:
            files.append(
                KubernetesArtifactVerificationFile(
                    path=relative_path,
                    expected_sha256=expected_sha256,
                    actual_sha256="",
                    ok=False,
                    error=path_error,
                )
            )
            errors.append(path_error)
            continue
        if member_path is None:
            continue
        if not member_path.exists():
            error = f"missing checksummed artifact: {relative_path}"
            files.append(
                KubernetesArtifactVerificationFile(
                    path=relative_path,
                    expected_sha256=expected_sha256,
                    actual_sha256="",
                    ok=False,
                    error=error,
                )
            )
            errors.append(error)
            continue
        _, actual_sha256 = _artifact_size_and_sha256(member_path)
        ok = actual_sha256 == expected_sha256
        error = "" if ok else f"checksum mismatch: {relative_path}"
        files.append(
            KubernetesArtifactVerificationFile(
                path=relative_path,
                expected_sha256=expected_sha256,
                actual_sha256=actual_sha256,
                ok=ok,
                error=error,
            )
        )
        if error:
            errors.append(error)
    return files, errors


def _checksum_member_path(
    artifact_dir: Path,
    relative_path: str,
) -> tuple[Path | None, str]:
    parsed = Path(relative_path)
    if parsed.is_absolute() or ".." in parsed.parts:
        return None, f"checksum path escapes artifact directory: {relative_path}"
    return artifact_dir / parsed, ""


def _verify_proof_command_artifact(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proof_command_path_text = summary_payload.get("proof_command_path", "")
    proof_command = summary_payload.get("proof_command")
    if not isinstance(proof_command_path_text, str) or not proof_command_path_text:
        return ["invalid summary.json: missing proof_command_path"]
    proof_command_parts: list[str] | None = None
    if isinstance(proof_command, list) and proof_command:
        parts = [part for part in proof_command if isinstance(part, str)]
        if len(parts) == len(proof_command):
            proof_command_parts = parts
    if proof_command_parts is None:
        errors.append("invalid summary.json: expected proof_command string list")

    proof_command_path = artifact_dir / Path(proof_command_path_text).name
    if not proof_command_path.exists():
        errors.append(f"missing proof command artifact: {proof_command_path.name}")
        return errors

    if proof_command_parts is not None:
        expected = shlex.join(proof_command_parts) + "\n"
        actual = proof_command_path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append("proof-command.txt does not match summary.json proof_command")
    return errors


def _collect_run_metadata() -> dict[str, Any]:
    github_actions = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
    return {
        "source": "github-actions" if github_actions else "local",
        "github_actions": github_actions,
        "generated_at_utc": _utc_now(),
        "github_workflow": _env("GITHUB_WORKFLOW"),
        "github_run_id": _env("GITHUB_RUN_ID"),
        "github_run_attempt": _env("GITHUB_RUN_ATTEMPT"),
        "github_job": _env("GITHUB_JOB"),
        "github_ref": _env("GITHUB_REF"),
        "github_sha": _env("GITHUB_SHA"),
        "github_actor": _env("GITHUB_ACTOR"),
        "github_repository": _env("GITHUB_REPOSITORY"),
        "github_event_name": _env("GITHUB_EVENT_NAME"),
        "runner_name": _env("RUNNER_NAME"),
        "runner_os": _env("RUNNER_OS"),
        "runner_arch": _env("RUNNER_ARCH"),
    }


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _verify_run_metadata_artifact(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    metadata_path_text = summary_payload.get("run_metadata_path", "")
    metadata_payload = summary_payload.get("run_metadata")
    if not isinstance(metadata_path_text, str) or not metadata_path_text:
        return ["invalid summary.json: missing run_metadata_path"]
    if not isinstance(metadata_payload, dict):
        errors.append("invalid summary.json: expected run_metadata object")

    metadata_path = artifact_dir / Path(metadata_path_text).name
    if not metadata_path.exists():
        errors.append(f"missing run metadata artifact: {metadata_path.name}")
        return errors

    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid run-metadata.json: {exc}")
        return errors
    if not isinstance(loaded, dict):
        errors.append("invalid run-metadata.json: expected object")
        return errors
    if isinstance(metadata_payload, dict) and loaded != metadata_payload:
        errors.append("run-metadata.json does not match summary.json run_metadata")
    return errors


def _verify_target_report_artifacts(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    targets = summary_payload.get("targets", [])
    if not isinstance(targets, list):
        return errors
    for target in targets:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("target_id", "<unknown>"))
        report_path_text = target.get("report_path", "")
        if not isinstance(report_path_text, str) or not report_path_text:
            continue
        report_path = artifact_dir / Path(report_path_text).name
        if not report_path.exists():
            errors.append(f"{target_id}: missing target report artifact: {report_path.name}")
            continue
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{target_id}: invalid target report artifact {report_path.name}: {exc}")
            continue
        if not isinstance(loaded, dict):
            errors.append(
                f"{target_id}: invalid target report artifact {report_path.name}: expected object"
            )
            continue
        if loaded != target:
            errors.append(f"{target_id}: target report artifact does not match summary.json target")
    return errors


def _verify_summary_consistency(summary_payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    recorded_ok = summary_payload.get("ok")
    if not isinstance(recorded_ok, bool):
        errors.append("invalid summary.json: expected ok boolean")
    require_cluster = summary_payload.get("require_cluster", False)
    if not isinstance(require_cluster, bool):
        errors.append("invalid summary.json: expected require_cluster boolean")
        require_cluster = False
    targets = summary_payload.get("targets", [])
    if not isinstance(targets, list):
        errors.append("invalid summary.json: expected targets list")
        return errors

    target_ids = summary_payload.get("target_ids", [])
    if not isinstance(target_ids, list) or not all(
        isinstance(target_id, str) for target_id in target_ids
    ):
        errors.append("invalid summary.json: expected target_ids string list")
    derived_target_ids = [
        target.get("target_id", "")
        for target in targets
        if isinstance(target, dict) and isinstance(target.get("target_id", ""), str)
    ]
    if (
        isinstance(target_ids, list)
        and all(isinstance(target_id, str) for target_id in target_ids)
        and target_ids != derived_target_ids
    ):
        errors.append("summary.json target_ids do not match target records")

    proof_command = summary_payload.get("proof_command")
    if isinstance(proof_command, list) and all(isinstance(part, str) for part in proof_command):
        proof_target_ids = _proof_command_target_ids(proof_command)
        if (
            isinstance(target_ids, list)
            and all(isinstance(target_id, str) for target_id in target_ids)
            and proof_target_ids != target_ids
        ):
            errors.append("summary.json proof_command targets do not match target_ids")
        if ("--require-cluster" in proof_command) != require_cluster:
            errors.append("summary.json proof_command require_cluster flag does not match summary")

    statuses = [target.get("status", "") for target in targets if isinstance(target, dict)]
    derived_ok = "failed" not in statuses
    if require_cluster and "skipped" in statuses:
        derived_ok = False
    if isinstance(recorded_ok, bool) and recorded_ok != derived_ok:
        errors.append("summary.json ok does not match target statuses")
    return errors


def _proof_command_target_ids(proof_command: Sequence[str]) -> list[str]:
    target_ids: list[str] = []
    for index, part in enumerate(proof_command):
        if part == "--target" and index + 1 < len(proof_command):
            target_ids.append(proof_command[index + 1])
    return target_ids


def _required_artifact_paths(summary_payload: dict[str, Any]) -> set[str]:
    required = {"summary.json"}
    for key in ("proof_command_path", "run_metadata_path"):
        path_text = summary_payload.get(key, "")
        if isinstance(path_text, str) and path_text:
            required.add(Path(path_text).name)

    targets = summary_payload.get("targets", [])
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue
            for key in ("manifest_path", "report_path"):
                path_text = target.get(key, "")
                if isinstance(path_text, str) and path_text:
                    required.add(Path(path_text).name)
    return required


def _verify_required_artifact_presence(
    artifact_dir: Path,
    *,
    required_paths: set[str],
) -> list[str]:
    return [
        f"missing required artifact: {relative_path}"
        for relative_path in sorted(required_paths)
        if not (artifact_dir / relative_path).exists()
    ]


def _verify_required_checksum_entries(
    artifact_dir: Path,
    *,
    required_paths: set[str],
    checksummed_paths: set[str],
) -> list[str]:
    return [
        f"missing checksum entry for required artifact: {relative_path}"
        for relative_path in sorted(required_paths)
        if relative_path not in checksummed_paths and (artifact_dir / relative_path).exists()
    ]


def _verify_target_manifest_metadata(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    targets = summary_payload.get("targets", [])
    if not isinstance(targets, list):
        return ["invalid summary.json: expected targets list"]
    for target in targets:
        if not isinstance(target, dict):
            errors.append("invalid summary.json: expected target objects")
            continue
        target_id = str(target.get("target_id", "<unknown>"))
        manifest_path_text = target.get("manifest_path", "")
        if not isinstance(manifest_path_text, str) or not manifest_path_text:
            continue
        manifest_path = artifact_dir / Path(manifest_path_text).name
        if not manifest_path.exists():
            errors.append(f"{target_id}: missing manifest artifact: {manifest_path.name}")
            continue
        actual_bytes, actual_sha256 = _artifact_size_and_sha256(manifest_path)
        expected_bytes = target.get("manifest_bytes")
        expected_sha256 = target.get("manifest_sha256", "")
        if expected_bytes != actual_bytes:
            errors.append(
                f"{target_id}: manifest_bytes mismatch for {manifest_path.name}: "
                f"expected {expected_bytes}, got {actual_bytes}"
            )
        if expected_sha256 != actual_sha256:
            errors.append(f"{target_id}: manifest_sha256 mismatch for {manifest_path.name}")
    return errors


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


__all__ = [
    "CommandResult",
    "KubernetesArtifactVerificationFile",
    "KubernetesDryRunArtifactVerificationReport",
    "KubernetesDryRunWarningRecord",
    "KubernetesGpuPreflightReport",
    "KubernetesDryRunReport",
    "KubernetesDryRunTargetReport",
    "format_kubernetes_dry_run_artifact_verification",
    "format_kubernetes_dry_run_report",
    "main",
    "run_kubernetes_server_dry_run",
    "verify_kubernetes_dry_run_artifacts",
]
