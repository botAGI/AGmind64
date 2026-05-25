"""Validation and smoke helpers for the Proxmox VE Prometheus exporter."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

PLACEHOLDER_TOKEN = "<PVE_TOKEN_VALUE>"


class ProxmoxExporterConfigError(ValueError):
    """Raised when a Proxmox exporter config is structurally invalid."""


@dataclass(frozen=True)
class ValidationResult:
    """Validation result that never carries secret values."""

    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def validate_pve_config(path: Path) -> ValidationResult:
    """Validate an AGmind-managed prometheus-pve-exporter config file."""
    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        return ValidationResult(False, (f"{path} does not exist",), ())
    if path.is_dir():
        return ValidationResult(False, (f"{path} is a directory, expected a YAML file",), ())

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return ValidationResult(False, (f"{path} is not valid YAML: {exc.__class__.__name__}",), ())

    if not isinstance(raw, dict):
        return ValidationResult(False, ("config root must be a mapping",), ())

    default = raw.get("default")
    if not isinstance(default, dict):
        return ValidationResult(False, ("default module is missing or empty",), ())

    if "password" in default:
        errors.append("password auth is not accepted for AGmind-managed Proxmox exporter configs")

    required = ("user", "token_name", "token_value")
    missing = [
        key
        for key in required
        if not isinstance(default.get(key), str) or not str(default.get(key)).strip()
    ]
    if missing:
        errors.append(f"missing required token fields: {', '.join(missing)}")

    token_value = default.get("token_value")
    if isinstance(token_value, str) and token_value.strip() == PLACEHOLDER_TOKEN:
        errors.append("token_value is still the placeholder value")

    verify_ssl = default.get("verify_ssl", True)
    if verify_ssl is False:
        warnings.append("verify_ssl is false; prefer trusted Proxmox certificates")
    elif not isinstance(verify_ssl, bool):
        errors.append("verify_ssl must be a boolean when present")

    return ValidationResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))


def render_validation_summary(result: ValidationResult) -> str:
    """Render validation output for operators without leaking secrets."""
    if result.ok:
        lines = ["Proxmox exporter config OK"]
        lines.extend(f"WARN: {warning}" for warning in result.warnings)
        return "\n".join(lines)

    lines = ["Proxmox exporter config FAILED"]
    lines.extend(f"ERROR: {error}" for error in result.errors)
    lines.extend(f"WARN: {warning}" for warning in result.warnings)
    return "\n".join(lines)


def probe_exporter(
    *,
    endpoint: str,
    target: str | None = None,
    module: str = "default",
    timeout: float = 5.0,
) -> ValidationResult:
    """Probe a running exporter endpoint and verify Prometheus-like output."""
    base = endpoint.rstrip("/")
    query: dict[str, str] = {"module": module}
    if target:
        query["target"] = target
    url = f"{base}/pve?{urllib.parse.urlencode(query)}"

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "agmind-proxmox-check"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        return ValidationResult(False, (f"exporter probe failed: {exc.__class__.__name__}",), ())

    if "# HELP" not in body and "pve_" not in body:
        return ValidationResult(
            False, ("exporter response does not look like Prometheus metrics",), ()
        )
    return ValidationResult(True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for config validation and optional exporter smoke."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Path to pve.yml")
    parser.add_argument("--endpoint", help="Optional exporter endpoint, e.g. http://127.0.0.1:9221")
    parser.add_argument("--target", help="Optional Proxmox host/IP for remote scrape mode")
    parser.add_argument("--module", default="default", help="Exporter config module")
    parser.add_argument("--timeout", type=float, default=5.0, help="Probe timeout in seconds")
    args = parser.parse_args(argv)

    result = validate_pve_config(args.config)
    output = render_validation_summary(result)
    if result.ok:
        print(output)
    else:
        print(output, file=sys.stderr)
        return 2

    if args.endpoint:
        probe = probe_exporter(
            endpoint=args.endpoint,
            target=args.target,
            module=args.module,
            timeout=args.timeout,
        )
        probe_output = render_validation_summary(probe).replace(
            "Proxmox exporter config",
            "Proxmox exporter probe",
        )
        if probe.ok:
            print(probe_output)
        else:
            print(probe_output, file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
