"""Aggregate governance checks for component, deploy, tool, dependency, and render gates."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from agmind.core.paths import data_root

REPO_ROOT = data_root()

DEFAULT_CHECKS = (
    "docs-mirror",
    "components",
    "deploy-targets",
    "tool-candidates",
    "constraints",
    "topology",
    "kubernetes-render",
    "kubernetes-proof-workflow",
    "digest-pins",
)

CheckFn = Callable[[], int]


@dataclass(frozen=True)
class GovernanceCheckFunctions:
    """Executable forms for one governance check."""

    run: CheckFn
    run_json: CheckFn | None = None


@dataclass(frozen=True)
class GovernanceCheckResult:
    """One aggregate governance check result."""

    name: str
    returncode: int
    stdout: str
    stderr: str
    payload: dict[str, Any] | None = None
    payload_error: str = ""

    @property
    def ok(self) -> bool:
        return _result_passed(self)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": _result_passed(self),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "payload": self.payload,
            "payload_error": self.payload_error,
        }


@dataclass(frozen=True)
class GovernanceReport:
    """Aggregate governance validation report."""

    results: tuple[GovernanceCheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(_result_passed(result) for result in self.results)

    @property
    def summary(self) -> dict[str, Any]:
        payloads = {result.name: result.payload or {} for result in self.results}
        k8s_payload = payloads.get("kubernetes-render", {})
        k8s_warning_summary = _warning_summary(k8s_payload.get("warning_summary"))
        k8s_expected_warning_summary = _warning_summary(k8s_payload.get("expected_warning_summary"))
        k8s_unexpected_warning_summary = _warning_summary(
            k8s_payload.get("unexpected_warning_summary")
        )
        topology_payload = payloads.get("topology", {})
        check_health = _check_health(self.results)

        return {
            "check_count": len(self.results),
            "ok_count": sum(1 for result in self.results if _result_passed(result)),
            "failed_count": sum(1 for result in self.results if _result_failed(result)),
            "health_status": _health_status(
                errors=_total_errors(self.results),
                warnings=_total_warnings(self.results),
                infos=_total_infos(self.results),
            ),
            "status_counts": _status_counts(check_health),
            "payload_count": sum(1 for result in self.results if result.payload is not None),
            "payload_error_count": sum(1 for result in self.results if result.payload_error),
            "payload_error_checks": _checks_with_payload_errors(self.results),
            "component_contracts": _payload_int(payloads.get("components", {}), "contract_count"),
            "service_descriptors": _payload_int(payloads.get("components", {}), "service_count"),
            "deploy_targets": _payload_int(payloads.get("deploy-targets", {}), "target_count"),
            "tool_candidates": _payload_int(payloads.get("tool-candidates", {}), "candidate_count"),
            "constraint_planes": _payload_int(payloads.get("constraints", {}), "plane_count"),
            "constraint_package_rules": _payload_int(
                payloads.get("constraints", {}), "package_rule_count"
            ),
            "topology_profiles": _payload_int(topology_payload, "profile_count"),
            "topology_warnings": _payload_int(topology_payload, "warning_count"),
            "topology_infos": _payload_int(topology_payload, "info_count"),
            "topology_expected_infos": _payload_int(topology_payload, "expected_info_count"),
            "topology_unexpected_infos": _payload_int(topology_payload, "unexpected_info_count"),
            "kubernetes_targets": _kubernetes_target_count(k8s_payload),
            "kubernetes_warnings": _kubernetes_warning_count(k8s_warning_summary),
            "kubernetes_expected_warnings": _kubernetes_warning_count(k8s_expected_warning_summary),
            "kubernetes_unexpected_warnings": _kubernetes_warning_count(
                k8s_unexpected_warning_summary
            ),
            "kubernetes_warning_summary": k8s_warning_summary,
            "kubernetes_expected_warning_summary": k8s_expected_warning_summary,
            "kubernetes_unexpected_warning_summary": k8s_unexpected_warning_summary,
            "kubernetes_proof_targets": _payload_int(
                payloads.get("kubernetes-proof-workflow", {}),
                "target_count",
            ),
            "docs_mirror_headings": _payload_int(
                payloads.get("docs-mirror", {}),
                "heading_count",
            ),
            "docs_mirror_code_blocks": _payload_int(
                payloads.get("docs-mirror", {}),
                "code_block_count",
            ),
            "total_warnings": _total_warnings(self.results),
            "total_infos": _total_infos(self.results),
            "total_errors": _total_errors(self.results),
            "failed_checks": _checks_with_errors(self.results),
            "warning_checks": _checks_with_warnings(self.results),
            "info_checks": _checks_with_infos(self.results),
            "check_health": check_health,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "checks": [result.to_json() for result in self.results],
        }


def _payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _warning_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"info": 0, "warning": 0, "blocker": 0}
    return {
        "info": _payload_int(value, "info"),
        "warning": _payload_int(value, "warning"),
        "blocker": _payload_int(value, "blocker"),
    }


def _kubernetes_target_count(payload: dict[str, Any]) -> int:
    targets = payload.get("targets")
    if isinstance(targets, list):
        return len(targets)
    return _payload_int(payload, "target_count")


def _kubernetes_warning_count(summary: dict[str, int]) -> int:
    return summary["warning"] + summary["blocker"]


def _total_warnings(results: tuple[GovernanceCheckResult, ...]) -> int:
    return sum(_result_warning_count(result) for result in results)


def _total_infos(results: tuple[GovernanceCheckResult, ...]) -> int:
    return sum(_result_info_count(result) for result in results)


def _total_errors(results: tuple[GovernanceCheckResult, ...]) -> int:
    return sum(_result_error_count(result) for result in results)


def _result_warning_count(result: GovernanceCheckResult) -> int:
    if result.payload is None:
        return 0
    if result.name == "kubernetes-render":
        return _kubernetes_warning_count(
            _warning_summary(result.payload.get("unexpected_warning_summary"))
        )
    return _payload_int(result.payload, "warning_count")


def _result_info_count(result: GovernanceCheckResult) -> int:
    if result.payload is None:
        return 0
    if result.name == "kubernetes-render":
        warning_summary = _warning_summary(result.payload.get("unexpected_warning_summary"))
        return warning_summary["info"]
    if result.name == "topology":
        return _payload_int(result.payload, "unexpected_info_count")
    return _payload_int(result.payload, "info_count")


def _result_error_count(result: GovernanceCheckResult) -> int:
    process_errors = 0 if result.returncode == 0 else 1
    if result.payload is None:
        return process_errors
    return max(process_errors, _payload_int(result.payload, "error_count"))


def _result_failed(result: GovernanceCheckResult) -> bool:
    return result.returncode != 0 or _result_error_count(result) > 0


def _result_passed(result: GovernanceCheckResult) -> bool:
    return not _result_failed(result)


def _checks_with_errors(results: tuple[GovernanceCheckResult, ...]) -> list[str]:
    return [result.name for result in results if _result_failed(result)]


def _checks_with_warnings(results: tuple[GovernanceCheckResult, ...]) -> list[str]:
    return [result.name for result in results if _result_warning_count(result) > 0]


def _checks_with_infos(results: tuple[GovernanceCheckResult, ...]) -> list[str]:
    return [result.name for result in results if _result_info_count(result) > 0]


def _checks_with_payload_errors(results: tuple[GovernanceCheckResult, ...]) -> list[str]:
    return [result.name for result in results if result.payload_error]


def _health_status(*, errors: int, warnings: int, infos: int) -> str:
    if errors > 0:
        return "failed"
    if warnings > 0:
        return "warning"
    if infos > 0:
        return "info"
    return "ok"


def _check_health(results: tuple[GovernanceCheckResult, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        warnings = _result_warning_count(result)
        infos = _result_info_count(result)
        errors = _result_error_count(result)
        rows.append(
            {
                "name": result.name,
                "ok": _result_passed(result),
                "status": _health_status(errors=errors, warnings=warnings, infos=infos),
                "warnings": warnings,
                "infos": infos,
                "errors": errors,
            }
        )
    return rows


def _status_counts(check_health: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"failed": 0, "warning": 0, "info": 0, "ok": 0}
    for row in check_health:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def _load_check_functions() -> dict[str, GovernanceCheckFunctions]:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)

    from scripts.checks import (
        component_check,
        constraints_check,
        deploy_target_check,
        digest_check,
        docs_mirror_check,
        kubernetes_proof_workflow_check,
        kubernetes_render_check,
        tool_candidate_check,
        topology_check,
    )

    return {
        "docs-mirror": GovernanceCheckFunctions(
            run=lambda: docs_mirror_check.main(()),
            run_json=lambda: docs_mirror_check.main(("--json",)),
        ),
        "components": GovernanceCheckFunctions(
            run=lambda: component_check.main(()),
            run_json=lambda: component_check.main(("--json",)),
        ),
        "deploy-targets": GovernanceCheckFunctions(
            run=lambda: deploy_target_check.main(()),
            run_json=lambda: deploy_target_check.main(("--json",)),
        ),
        "tool-candidates": GovernanceCheckFunctions(
            run=lambda: tool_candidate_check.main(()),
            run_json=lambda: tool_candidate_check.main(("--json",)),
        ),
        "constraints": GovernanceCheckFunctions(
            run=lambda: constraints_check.main(()),
            run_json=lambda: constraints_check.main(("--json",)),
        ),
        "topology": GovernanceCheckFunctions(
            run=lambda: topology_check.main(()),
            run_json=lambda: topology_check.main(("--json",)),
        ),
        "kubernetes-render": GovernanceCheckFunctions(
            run=lambda: kubernetes_render_check.main(()),
            run_json=lambda: kubernetes_render_check.main(("--json",)),
        ),
        "kubernetes-proof-workflow": GovernanceCheckFunctions(
            run=lambda: kubernetes_proof_workflow_check.main(()),
            run_json=lambda: kubernetes_proof_workflow_check.main(("--json",)),
        ),
        "digest-pins": GovernanceCheckFunctions(
            run=lambda: digest_check.main(()),
            run_json=lambda: digest_check.main(("--json",)),
        ),
    }


def _run_one(name: str, fn: CheckFn) -> GovernanceCheckResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = fn()
    return GovernanceCheckResult(
        name=name,
        returncode=returncode,
        stdout=stdout.getvalue().strip(),
        stderr=stderr.getvalue().strip(),
    )


def _run_one_structured(name: str, functions: GovernanceCheckFunctions) -> GovernanceCheckResult:
    if functions.run_json is None:
        return _run_one(name, functions.run)

    text_result = _run_one(name, functions.run)
    json_result = _run_one(name, functions.run_json)
    payload = _parse_json_payload(json_result.stdout)
    payload_error = f"invalid structured JSON payload for {name}" if payload is None else ""
    returncode = text_result.returncode or json_result.returncode or (2 if payload is None else 0)
    stderr = "\n".join(
        item for item in (text_result.stderr, json_result.stderr, payload_error) if item
    )
    return GovernanceCheckResult(
        name=text_result.name,
        returncode=returncode,
        stdout=text_result.stdout,
        stderr=stderr,
        payload=payload,
        payload_error=payload_error,
    )


def _parse_json_payload(stdout: str) -> dict[str, Any] | None:
    if not stdout:
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def run_governance_checks(
    checks: Sequence[str] = DEFAULT_CHECKS,
    *,
    structured: bool = False,
) -> GovernanceReport:
    """Run selected governance gates and return their captured results."""
    functions = _load_check_functions()
    results: list[GovernanceCheckResult] = []
    for name in checks:
        if name not in functions:
            results.append(
                GovernanceCheckResult(
                    name=name,
                    returncode=2,
                    stdout="",
                    stderr=f"unknown governance check: {name}",
                )
            )
            continue
        check_functions = functions[name]
        if structured:
            results.append(_run_one_structured(name, check_functions))
        else:
            results.append(_run_one(name, check_functions.run))
    return GovernanceReport(results=tuple(results))


def format_governance_report(report: GovernanceReport) -> str:
    """Render an operator-readable governance report."""
    lines: list[str] = []
    for result in report.results:
        status = "FAILED" if _result_failed(result) else "OK"
        lines.append(f"{result.name}: {status}")
        details = (result.stderr or result.stdout) if _result_failed(result) else result.stdout
        if details:
            for line in details.splitlines():
                lines.append(f"  {line}")
    if report.ok:
        lines.append(f"governance OK: {len(report.results)} checks{_format_health_suffix(report)}")
    else:
        failed = sum(1 for result in report.results if _result_failed(result))
        lines.append(
            f"governance FAILED: {failed}/{len(report.results)} checks failed"
            f"{_format_health_suffix(report)}"
        )
    return "\n".join(lines)


def _format_health_suffix(report: GovernanceReport) -> str:
    if report.ok and not any(result.payload is not None for result in report.results):
        return ""

    summary = report.summary
    return (
        f" (status={summary['health_status']}, "
        f"warnings={summary['total_warnings']}, "
        f"infos={summary['total_infos']}, "
        f"errors={summary['total_errors']})"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-compatible entry point for the aggregate governance gate."""
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    report = run_governance_checks(structured=True)
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_governance_report(report))
    return 0 if report.ok else 1


__all__ = [
    "DEFAULT_CHECKS",
    "GovernanceCheckFunctions",
    "GovernanceCheckResult",
    "GovernanceReport",
    "format_governance_report",
    "main",
    "run_governance_checks",
]
