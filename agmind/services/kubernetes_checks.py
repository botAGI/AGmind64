"""Validation helpers for Kubernetes render targets."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agmind.deploy.targets import DeploymentExpectedWarning, DeploymentTarget, load_deploy_targets
from agmind.services.kubernetes_renderer import (
    KubernetesRenderWarning,
    render_kubernetes,
    to_yaml,
)
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    load_descriptors,
    select_services,
    unknown_profiles,
)


@dataclass(frozen=True)
class KubernetesTargetRenderReport:
    """Render validation result for one Kubernetes deployment target."""

    target_id: str
    renderer: str
    profiles: tuple[str, ...]
    ok: bool
    object_count: int
    deployment_count: int
    service_count: int
    warning_count: int
    warning_summary: dict[str, int]
    warnings: tuple[KubernetesRenderWarning, ...] = ()
    expected_warnings: tuple[KubernetesRenderWarning, ...] = ()
    unexpected_warnings: tuple[KubernetesRenderWarning, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def expected_warning_count(self) -> int:
        return len(self.expected_warnings)

    @property
    def expected_warning_summary(self) -> dict[str, int]:
        return _summarize_warnings(self.expected_warnings)

    @property
    def unexpected_warning_count(self) -> int:
        return len(self.unexpected_warnings)

    @property
    def unexpected_warning_summary(self) -> dict[str, int]:
        return _summarize_warnings(self.unexpected_warnings)

    def to_json(self) -> dict[str, Any]:
        expected_keys = {_warning_key(warning) for warning in self.expected_warnings}
        return {
            "target_id": self.target_id,
            "renderer": self.renderer,
            "profiles": list(self.profiles),
            "ok": self.ok,
            "object_count": self.object_count,
            "deployment_count": self.deployment_count,
            "service_count": self.service_count,
            "warning_count": self.warning_count,
            "warning_summary": dict(self.warning_summary),
            "expected_warning_count": self.expected_warning_count,
            "expected_warning_summary": dict(self.expected_warning_summary),
            "unexpected_warning_count": self.unexpected_warning_count,
            "unexpected_warning_summary": dict(self.unexpected_warning_summary),
            "warnings": [
                _warning_to_json(warning, expected=_warning_key(warning) in expected_keys)
                for warning in self.warnings
            ],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class KubernetesRenderCheckReport:
    """Aggregate Kubernetes render validation result."""

    targets: tuple[KubernetesTargetRenderReport, ...]

    @property
    def ok(self) -> bool:
        return all(target.ok for target in self.targets)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "warning_summary": _merge_warning_summaries(
                target.warning_summary for target in self.targets
            ),
            "expected_warning_summary": _merge_warning_summaries(
                target.expected_warning_summary for target in self.targets
            ),
            "unexpected_warning_summary": _merge_warning_summaries(
                target.unexpected_warning_summary for target in self.targets
            ),
            "targets": [target.to_json() for target in self.targets],
        }


def validate_kubernetes_render_targets(
    targets: Mapping[str, DeploymentTarget] | None = None,
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    strict: bool = False,
) -> KubernetesRenderCheckReport:
    """Render Kubernetes targets and validate the emitted manifest stream."""
    target_map = targets if targets is not None else load_deploy_targets()
    reports: list[KubernetesTargetRenderReport] = []
    for target in target_map.values():
        if target.runtime.kind != "kubernetes":
            continue
        if target.runtime.renderer != "agmind render kubernetes":
            reports.append(
                KubernetesTargetRenderReport(
                    target_id=target.id,
                    renderer=target.runtime.renderer,
                    profiles=target.runtime.profiles,
                    ok=False,
                    object_count=0,
                    deployment_count=0,
                    service_count=0,
                    warning_count=0,
                    warning_summary=_empty_warning_summary(),
                    warnings=(),
                    errors=(
                        f"{target.id}: unsupported Kubernetes renderer {target.runtime.renderer}",
                    ),
                )
            )
            continue
        reports.append(_validate_one_target(target, services_dir=services_dir, strict=strict))
    return KubernetesRenderCheckReport(targets=tuple(reports))


def format_kubernetes_render_report(report: KubernetesRenderCheckReport) -> str:
    """Render a human-readable Kubernetes render check report."""
    lines: list[str] = []
    for target in report.targets:
        status = "OK" if target.ok else "FAILED"
        profile_text = ",".join(target.profiles) or "<none>"
        lines.append(
            f"{target.target_id}: {status} "
            f"({target.object_count} objects, {target.deployment_count} deployments, "
            f"{target.service_count} services, profiles={profile_text})"
        )
        if target.unexpected_warning_count:
            summary = _format_warning_summary(target.unexpected_warning_summary)
            lines.append(f"  warnings: {target.unexpected_warning_count} ({summary})")
            blockers = _format_code_breakdown(target.unexpected_warnings, severity="blocker")
            if blockers:
                lines.append(f"  blockers: {blockers}")
        if target.expected_warning_count:
            summary = _format_warning_summary(target.expected_warning_summary)
            lines.append(f"  expected warnings: {target.expected_warning_count} ({summary})")
        for error in target.errors:
            lines.append(f"  ERROR: {error}")
    if report.ok:
        lines.append(f"kubernetes render OK: {len(report.targets)} targets")
    else:
        failed = sum(1 for target in report.targets if not target.ok)
        lines.append(f"kubernetes render FAILED: {failed}/{len(report.targets)} targets failed")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-compatible entry point for Kubernetes render validation."""
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    strict = "--strict" in args
    report = validate_kubernetes_render_targets(strict=strict)
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_kubernetes_render_report(report))
    return 0 if report.ok else 1


def _validate_one_target(
    target: DeploymentTarget,
    *,
    services_dir: Path,
    strict: bool,
) -> KubernetesTargetRenderReport:
    try:
        rendered, warnings = _render_target_to_yaml_and_warnings(
            target,
            services_dir=services_dir,
        )
        objects = tuple(doc for doc in yaml.safe_load_all(rendered) if doc)
    except Exception as exc:  # noqa: BLE001
        return KubernetesTargetRenderReport(
            target_id=target.id,
            renderer=target.runtime.renderer,
            profiles=target.runtime.profiles,
            ok=False,
            object_count=0,
            deployment_count=0,
            service_count=0,
            warning_count=0,
            warning_summary=_empty_warning_summary(),
            warnings=(),
            errors=(f"{target.id}: render failed: {exc}",),
        )

    errors: list[str] = []
    deployment_count = _count_kind(objects, "Deployment")
    service_count = _count_kind(objects, "Service")
    if not objects:
        errors.append(f"{target.id}: rendered manifest stream is empty")
    if deployment_count == 0:
        errors.append(f"{target.id}: rendered manifest stream has no Deployments")
    blocker_count = _summarize_warnings(warnings)["blocker"]
    if target.status != "research" and blocker_count:
        errors.append(
            f"{target.id}: {blocker_count} blocker warnings require research status "
            "or Kubernetes-native remediation"
        )
    expected_warnings = _expected_warnings(
        warnings,
        expected_codes=target.verification.expected_warning_codes,
        expected_warnings=target.verification.expected_warnings,
    )
    unexpected_warnings = _unexpected_warnings(
        warnings,
        expected_codes=target.verification.expected_warning_codes,
        expected_warnings=target.verification.expected_warnings,
    )
    if strict and unexpected_warnings:
        warning_label = "warning" if len(unexpected_warnings) == 1 else "warnings"
        errors.append(
            "strict mode rejects "
            f"{len(unexpected_warnings)} unexpected portability {warning_label}: "
            f"{_format_warning_code_breakdown(unexpected_warnings)}"
        )

    return KubernetesTargetRenderReport(
        target_id=target.id,
        renderer=target.runtime.renderer,
        profiles=target.runtime.profiles,
        ok=not errors,
        object_count=len(objects),
        deployment_count=deployment_count,
        service_count=service_count,
        warning_count=len(warnings),
        warning_summary=_summarize_warnings(warnings),
        warnings=warnings,
        expected_warnings=expected_warnings,
        unexpected_warnings=unexpected_warnings,
        errors=tuple(errors),
    )


def _count_kind(objects: tuple[object, ...], kind: str) -> int:
    return sum(1 for item in objects if isinstance(item, dict) and item.get("kind") == kind)


def _render_target_to_yaml_and_warnings(
    target: DeploymentTarget,
    *,
    services_dir: Path,
) -> tuple[str, tuple[KubernetesRenderWarning, ...]]:
    descriptors = load_descriptors(services_dir)
    missing_profiles = unknown_profiles(descriptors, list(target.runtime.profiles))
    if missing_profiles:
        raise ValueError(f"Unknown profiles requested: {', '.join(missing_profiles)}")
    selected = select_services(descriptors, profiles=list(target.runtime.profiles))
    excluded = frozenset(target.runtime.excluded_services)
    if excluded:
        missing = sorted(excluded.difference(descriptors))
        if missing:
            raise ValueError(f"Unknown excluded services: {', '.join(missing)}")
        selected = {
            name: descriptor for name, descriptor in selected.items() if name not in excluded
        }
    if not selected:
        raise ValueError(f"No services match: profiles={list(target.runtime.profiles)}")
    result = render_kubernetes(list(selected.values()), namespace="agmind")
    return to_yaml(result), result.warnings


def _empty_warning_summary() -> dict[str, int]:
    return {"info": 0, "warning": 0, "blocker": 0}


def _summarize_warnings(warnings: tuple[KubernetesRenderWarning, ...]) -> dict[str, int]:
    summary = _empty_warning_summary()
    for warning in warnings:
        summary[warning.severity] += 1
    return summary


def _merge_warning_summaries(summaries: Iterable[dict[str, int]]) -> dict[str, int]:
    merged = _empty_warning_summary()
    for summary in summaries:
        for severity in merged:
            merged[severity] += int(summary.get(severity, 0))
    return merged


def _format_warning_summary(summary: dict[str, int]) -> str:
    return ", ".join(
        f"{severity}={summary.get(severity, 0)}" for severity in _empty_warning_summary()
    )


def _warning_to_json(warning: KubernetesRenderWarning, *, expected: bool) -> dict[str, str | bool]:
    return {
        "service": warning.service,
        "code": warning.code,
        "severity": warning.severity,
        "message": warning.message,
        "remediation": warning.remediation,
        "expected": expected,
    }


def _format_code_breakdown(
    warnings: tuple[KubernetesRenderWarning, ...],
    *,
    severity: str,
) -> str:
    counts: dict[str, int] = {}
    for warning in warnings:
        if warning.severity != severity:
            continue
        counts[warning.code] = counts.get(warning.code, 0) + 1
    return ", ".join(f"{code}={counts[code]}" for code in sorted(counts))


def _unexpected_warnings(
    warnings: tuple[KubernetesRenderWarning, ...],
    *,
    expected_codes: tuple[str, ...],
    expected_warnings: tuple[DeploymentExpectedWarning, ...],
) -> tuple[KubernetesRenderWarning, ...]:
    return tuple(
        warning
        for warning in warnings
        if not _warning_is_expected(
            warning,
            expected_codes=expected_codes,
            expected_warnings=expected_warnings,
        )
    )


def _expected_warnings(
    warnings: tuple[KubernetesRenderWarning, ...],
    *,
    expected_codes: tuple[str, ...],
    expected_warnings: tuple[DeploymentExpectedWarning, ...],
) -> tuple[KubernetesRenderWarning, ...]:
    return tuple(
        warning
        for warning in warnings
        if _warning_is_expected(
            warning,
            expected_codes=expected_codes,
            expected_warnings=expected_warnings,
        )
    )


def _warning_is_expected(
    warning: KubernetesRenderWarning,
    *,
    expected_codes: tuple[str, ...],
    expected_warnings: tuple[DeploymentExpectedWarning, ...],
) -> bool:
    expected_code_set = frozenset(expected_codes)
    expected_pairs = frozenset((warning.service, warning.code) for warning in expected_warnings)
    return warning.code in expected_code_set or _warning_key(warning) in expected_pairs


def _warning_key(warning: KubernetesRenderWarning) -> tuple[str, str]:
    return (warning.service, warning.code)


def _format_warning_code_breakdown(warnings: tuple[KubernetesRenderWarning, ...]) -> str:
    counts: dict[str, int] = {}
    for warning in warnings:
        counts[warning.code] = counts.get(warning.code, 0) + 1
    return ", ".join(f"{code}={counts[code]}" for code in sorted(counts))


__all__ = [
    "KubernetesRenderCheckReport",
    "KubernetesTargetRenderReport",
    "format_kubernetes_render_report",
    "main",
    "validate_kubernetes_render_targets",
]
