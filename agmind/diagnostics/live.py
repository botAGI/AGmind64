"""Doctor overhaul — live config merge, safe auto-fix, and support bundle.

Pure / testable logic that the thin ``agmind doctor`` CLI delegates to. Three
orthogonal modes layer on top of the existing :func:`agmind.diagnostics.doctor.
run_preflight`:

- ``--live``  : fold a live :class:`agmind.config.validation.ConfigValidationReport`
  into the preflight :class:`DoctorReport`.
- ``--fix``   : run ONLY the idempotent permission-class fixes (``sudo chmod`` /
  ``sudo chown``); every other suggested fix (deploy/install/gc) is PRINTED, never
  run.
- ``--bundle``: write a sanitized ``tar.gz`` support archive. The ``.env`` member is
  redacted to ``KEY=***`` lines; no raw secret file ever lands in the archive.

REUSES :func:`validate_config` and the redaction discipline of
``agmind.install.steps._redact_install_secrets`` (here specialised to a whole
``.env`` file). NEVER echoes a secret VALUE anywhere.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agmind.config.validation import ConfigFinding, ConfigValidationReport, validate_config
from agmind.diagnostics.doctor import CheckResult, DoctorReport, run_preflight

# ---------------------------------------------------------------------------
# severity ↔ status mapping
# ---------------------------------------------------------------------------

# ConfigFinding.severity ("error"/"warning"/"info") → CheckResult.status.
_SEVERITY_TO_STATUS = {"error": "fail", "warning": "warn", "info": "skip"}

# Re-sort priority for a merged DoctorReport (error class first).
_STATUS_ORDER = {"fail": 0, "warn": 1, "ok": 2, "skip": 3}


# ---------------------------------------------------------------------------
# --fix allow-list (the ONLY auto-runnable class)
# ---------------------------------------------------------------------------

# Idempotent permission-only fixes. The id MUST be in this set AND the fix_cmd
# MUST start with one of the perm-class prefixes — both gates, so a future
# finding that reuses an id but ships a destructive fix_cmd can never auto-run.
_SAFE_FIX_IDS = frozenset({"env-file-mode", "secret-file-unreadable"})
_SAFE_FIX_PREFIXES = ("sudo chmod ", "sudo chown ")


def _is_safe_auto_fix(finding: ConfigFinding) -> bool:
    """True only for idempotent permission-only fixes (chmod/chown)."""
    return (
        finding.fixable
        and finding.id in _SAFE_FIX_IDS
        and finding.fix_cmd.startswith(_SAFE_FIX_PREFIXES)
    )


# ---------------------------------------------------------------------------
# fold ConfigFinding → DoctorReport
# ---------------------------------------------------------------------------


def finding_to_check(finding: ConfigFinding) -> CheckResult:
    """Map a :class:`ConfigFinding` onto a :class:`CheckResult`."""
    return CheckResult(
        name=finding.id,
        status=_SEVERITY_TO_STATUS.get(finding.severity, "skip"),
        message=finding.message,
        fix_hint=finding.fix_cmd,
    )


def merge_live_findings(report: DoctorReport, findings: tuple[ConfigFinding, ...]) -> DoctorReport:
    """Fold live findings into ``report`` and re-sort by status severity.

    Returns a NEW report; the input is not mutated. ``has_failures`` /
    ``has_warnings`` therefore reflect the union of preflight + live findings.
    """
    checks = list(report.checks) + [finding_to_check(f) for f in findings]
    checks.sort(key=lambda c: (_STATUS_ORDER.get(c.status, 99), c.name))
    return DoctorReport(checks=checks)


# ---------------------------------------------------------------------------
# --fix execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixOutcome:
    """One applied (or attempted) safe fix."""

    finding: ConfigFinding
    detail: str = ""


@dataclass
class FixResult:
    """Result of :func:`apply_safe_fixes`."""

    fixed: list[FixOutcome] = field(default_factory=list)
    failed: list[FixOutcome] = field(default_factory=list)
    unfixable: list[ConfigFinding] = field(default_factory=list)


def _build_fix_argv(fix_cmd: str, sudo_password: str | None) -> list[str]:
    """Turn a ``sudo chmod ...`` fix_cmd string into an argv list.

    When a sudo password is supplied we re-wrap as ``sudo -S -p "" -- <rest>`` so the
    password can be fed on stdin (mirrors backup.py ``_run_sudo_no_output``). With no
    password the command runs as-is (relies on NOPASSWD sudo or already-root).
    """
    parts = fix_cmd.split()
    if sudo_password is not None and parts and parts[0] == "sudo":
        return ["sudo", "-S", "-p", "", "--", *parts[1:]]
    return parts


def _run_fix(fix_cmd: str, sudo_password: str | None) -> tuple[bool, str]:
    """Run one safe fix. Returns ``(success, stderr_detail)``."""
    argv = _build_fix_argv(fix_cmd, sudo_password)
    if not argv:
        return False, "empty command"
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "check": False,
    }
    if sudo_password is not None and argv[:1] == ["sudo"]:
        kwargs["input"] = f"{sudo_password}\n"
    try:
        result = subprocess.run(argv, **kwargs)  # type: ignore[call-overload]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or "").strip() or f"exit {result.returncode}"
    return True, ""


def apply_safe_fixes(
    findings: tuple[ConfigFinding, ...], *, sudo_password: str | None
) -> FixResult:
    """Apply ONLY the permission-class safe fixes; collect the rest as unfixable.

    A finding is auto-run iff :func:`_is_safe_auto_fix`. Every other finding that
    carries a ``fix_cmd`` (deploy/install/gc/...) is returned in ``unfixable`` for
    the operator to run by hand — this function NEVER invokes them.
    """
    result = FixResult()
    for finding in findings:
        if _is_safe_auto_fix(finding):
            ok, detail = _run_fix(finding.fix_cmd, sudo_password)
            if ok:
                result.fixed.append(FixOutcome(finding=finding))
            else:
                result.failed.append(FixOutcome(finding=finding, detail=detail))
        elif finding.fixable and finding.fix_cmd:
            result.unfixable.append(finding)
    return result


# ---------------------------------------------------------------------------
# --bundle (support tar.gz)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleResult:
    """Outcome of :func:`create_support_bundle`."""

    output_path: Path
    bytes_written: int
    issues: list[str] = field(default_factory=list)


def _redact_env_text(text: str) -> str:
    """Replace every ``KEY=VALUE`` line with ``KEY=***`` (comments/blank kept).

    Whole-file specialisation of the install ``_redact_install_secrets`` discipline:
    the .env is ALL secrets, so we redact every value rather than a known set.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0]
        out.append(f"{key}=***")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _add_text_member(tar: tarfile.TarFile, arcname: str, payload: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(payload)
    info.mode = mode
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(payload))


def _collect_docker_ps(install_dir: Path) -> bytes:
    """`docker ps -a` as JSON-lines (container states; no env VALUES). Best effort."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"docker ps unavailable: {exc}".encode()
    return proc.stdout or proc.stderr or b""


def _collect_docker_logs(install_dir: Path) -> bytes:
    """`docker compose logs --tail 100` (no secret VALUES echoed). Best effort."""
    try:
        proc = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--tail", "100"],
            cwd=str(install_dir),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"docker compose logs unavailable: {exc}".encode()
    return proc.stdout or proc.stderr or b""


def create_support_bundle(
    output_path: Path,
    *,
    install_dir: Path,
    include_logs: bool = True,
) -> BundleResult:
    """Write a sanitized ``tar.gz`` support bundle.

    Members (all non-secret):
      - ``agmind-bundle.json``  : metadata (created_at, install_dir, issues)
      - ``env_redacted.txt``    : .env with every value → ``***``
      - ``docker_compose.yml``  : rendered compose (already secret-free)
      - ``version.env``         : software versions (optional, world-readable)
      - ``docker_ps_a.json``    : container states
      - ``docker_logs_tail.txt``: last 100 log lines (only with ``include_logs``)
      - ``config_validate.json``: live ConfigValidationReport payload
      - ``doctor_report.json``  : preflight DoctorReport

    The raw ``.env`` / secret files are NEVER added. The output must not already
    exist. The bundle still builds when individual sources are missing; each gap is
    recorded in ``issues``.
    """
    output_path = Path(output_path)
    install_dir = Path(install_dir)
    if output_path.exists():
        raise FileExistsError(f"bundle output already exists: {output_path}")

    issues: list[str] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    tmp_path.unlink(missing_ok=True)

    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            # --- redacted .env ---
            env_path = install_dir / ".env"
            try:
                raw = env_path.read_text(encoding="utf-8")
                _add_text_member(tar, "env_redacted.txt", _redact_env_text(raw).encode("utf-8"))
            except OSError as exc:
                issues.append(f"env: {exc.__class__.__name__}")

            # --- compose (safe) ---
            compose_path = install_dir / "docker-compose.yml"
            try:
                _add_text_member(tar, "docker_compose.yml", compose_path.read_bytes())
            except OSError as exc:
                issues.append(f"compose: {exc.__class__.__name__}")

            # --- version.env (safe, optional) ---
            version_path = install_dir / "version.env"
            if version_path.exists():
                try:
                    _add_text_member(tar, "version.env", version_path.read_bytes())
                except OSError as exc:
                    issues.append(f"version: {exc.__class__.__name__}")

            # --- docker ps / logs (best effort) ---
            _add_text_member(tar, "docker_ps_a.json", _collect_docker_ps(install_dir))
            if include_logs:
                _add_text_member(tar, "docker_logs_tail.txt", _collect_docker_logs(install_dir))

            # --- live config validation ---
            try:
                report = validate_config(install_dir, check_drift=True, strict=False)
                _add_text_member(
                    tar,
                    "config_validate.json",
                    json.dumps(report.to_payload(), indent=2, ensure_ascii=False).encode("utf-8"),
                )
            except Exception as exc:  # noqa: BLE001 — never let validation abort a bundle
                issues.append(f"validate: {exc.__class__.__name__}")

            # --- preflight doctor ---
            try:
                preflight = run_preflight()
                _add_text_member(
                    tar,
                    "doctor_report.json",
                    json.dumps(preflight.to_dict(), indent=2, ensure_ascii=False).encode("utf-8"),
                )
            except Exception as exc:  # noqa: BLE001
                issues.append(f"doctor: {exc.__class__.__name__}")

            # --- metadata ---
            metadata = {
                "created_at": datetime.now(UTC).isoformat(),
                "install_dir": str(install_dir),
                "include_logs": include_logs,
                "issues": issues,
            }
            _add_text_member(
                tar,
                "agmind-bundle.json",
                json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
            )

        tmp_path.chmod(0o600)
        tmp_path.replace(output_path)
        output_path.chmod(0o600)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return BundleResult(
        output_path=output_path,
        bytes_written=output_path.stat().st_size,
        issues=issues,
    )


# Re-export so the CLI can monkeypatch a single seam in tests.
__all__ = [
    "BundleResult",
    "ConfigFinding",
    "ConfigValidationReport",
    "FixResult",
    "apply_safe_fixes",
    "create_support_bundle",
    "finding_to_check",
    "merge_live_findings",
    "validate_config",
]
