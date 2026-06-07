"""Runtime validator for a LIVE AGmind deployment.

This is NOT a pre-install plan check — it inspects the artifacts a real,
already-deployed stack leaves on disk and the containers it leaves running:

- ``<install_dir>/.env``          — the resolved runtime secrets/config
- ``<install_dir>/docker-compose.yml`` — the rendered selection (authoritative
  set of deployed services + the ``${VAR:?}`` required-var references)
- ``/var/lib/agmind/secrets/<f>`` — the 0600 DB secret files the non-root
  database images read (the komodo-mongo "Permission denied" crash class)
- ``agmind-<service>`` containers — pinned-digest ↔ running-digest drift

Each problem becomes a :class:`ConfigFinding`. The report's ``ok`` flips to
False on any ``error`` (and, under ``strict``, on any ``warning``).

Design mirrors :class:`agmind.services.compatibility.CompatIssue` /
``CompatReport`` and the thin ``cmd_validate`` pattern of
``agmind.cli.targets_cmd``.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agmind.core.env import parse_env_file
from agmind.install.secret_keys import DB_SECRET_FILE_READER_UID, DB_SECRET_FILES
from agmind.services.renderer import load_descriptors

# ``${VAR}``, ``${VAR:-default}``, ``${VAR:?message}`` — compose interpolation
# forms. SAME pattern as agmind.install.secrets_audit._VAR_RE; replicated here so
# this module owns its scanning without a cross-module private import. Used for
# both the required-var scan (the ``${VAR:?...}`` form) and the unresolved-value
# scan inside a .env value.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*)|:\?[^}]*)?\}")


def _required_vars(text: str) -> list[str]:
    """Return var names referenced via the REQUIRED ``${VAR:?...}`` form in ``text``."""
    out: list[str] = []
    for match in _VAR_RE.finditer(text):
        if match.group(1) is not None and ":?" in match.group(0):
            out.append(match.group(1))
    return out


_DEFAULT_SECRETS_DIR = Path("/var/lib/agmind/secrets")
_CONTAINER_PREFIX = "agmind-"

_NOT_RUNNING = "__not_running__"


@dataclass(frozen=True)
class ConfigFinding:
    """One detected runtime-config problem."""

    id: str  # stable slug, e.g. "env-required-var-missing"
    severity: str  # "error" | "warning" | "info"
    message: str
    evidence: str = ""
    fixable: bool = False
    fix_cmd: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "fixable": self.fixable,
            "fix_cmd": self.fix_cmd,
        }


@dataclass(frozen=True)
class ConfigValidationReport:
    """Result of :func:`validate_config`."""

    findings: tuple[ConfigFinding, ...]
    strict: bool = False

    @property
    def ok(self) -> bool:
        """True when there are no errors (and, under strict, no warnings)."""
        if any(f.severity == "error" for f in self.findings):
            return False
        if self.strict and any(f.severity == "warning" for f in self.findings):
            return False
        return True

    def by_severity(self, severity: str) -> tuple[ConfigFinding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": len(self.by_severity("error")),
            "warning_count": len(self.by_severity("warning")),
            "info_count": len(self.by_severity("info")),
            "findings": [f.to_payload() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Pure helpers (trivially unit-testable without root / without docker)
# ---------------------------------------------------------------------------


def _uid_can_read(mode: int, file_uid: int, file_gid: int, reader_uid: int) -> bool:
    """Return True if ``reader_uid`` is permitted to read a file with these stats.

    Pure predicate so the komodo-mongo readability check is testable without
    root. uid 0 (root) can always read. Otherwise the reader needs at least one
    of: owner-read (and is the owner), group-read (and is the group), or
    world-read.
    """
    perms = mode & 0o777
    if reader_uid == 0:
        return True
    if file_uid == reader_uid and (perms & 0o400):
        return True
    if file_gid == reader_uid and (perms & 0o040):
        return True
    if perms & 0o004:
        return True
    return False


def _load_compose(compose_path: Path) -> dict[str, Any]:
    """Parse the rendered compose file → dict (``{}`` if not a mapping)."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _selected_services(compose: dict[str, Any]) -> set[str]:
    services = compose.get("services")
    if isinstance(services, dict):
        return set(services.keys())
    return set()


def _secret_source_path(
    compose: dict[str, Any],
    service: str,
    secret_filename: str,
) -> Path:
    """Resolve the HOST path of a secret bind-mount for ``service``.

    Looks for a ``src:/run/secrets/<filename>[:ro]`` volume on the service;
    falls back to ``/var/lib/agmind/secrets/<filename>`` when not found.
    """
    services = compose.get("services")
    if isinstance(services, dict):
        svc = services.get(service)
        if isinstance(svc, dict):
            volumes = svc.get("volumes")
            if isinstance(volumes, list):
                for vol in volumes:
                    if not isinstance(vol, str):
                        continue
                    parts = vol.split(":")
                    if len(parts) >= 2 and parts[1].endswith(f"/run/secrets/{secret_filename}"):
                        return Path(parts[0])
    return _DEFAULT_SECRETS_DIR / secret_filename


def _bare_digest(value: str) -> str:
    """Normalize ``[repo@]sha256:<hex>`` (or bare hex) → bare lower-case hex."""
    text = value.strip()
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if text.startswith("sha256:"):
        text = text[len("sha256:") :]
    return text.lower()


def _running_image_digests(selected: set[str]) -> dict[str, str] | None:
    """Map each selected service → the bare running-image digest.

    For each ``svc`` runs ``docker inspect agmind-<svc>`` and reads
    ``.RepoDigests[0]``. A service whose container is absent maps to the
    ``_NOT_RUNNING`` sentinel; a container with empty RepoDigests maps to ``""``.
    Returns ``None`` when the docker binary is unavailable (→ drift skipped).
    """
    digests: dict[str, str] = {}
    for svc in sorted(selected):
        try:
            proc = subprocess.run(
                [
                    "docker",
                    "inspect",
                    f"{_CONTAINER_PREFIX}{svc}",
                    "--format",
                    "{{index .RepoDigests 0}}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            return None
        except (subprocess.SubprocessError, OSError):
            digests[svc] = _NOT_RUNNING
            continue
        if proc.returncode != 0:
            # No such container (or other inspect failure) → treat as not running.
            digests[svc] = _NOT_RUNNING
            continue
        repo_digest = proc.stdout.strip()
        if not repo_digest or "@" not in repo_digest:
            digests[svc] = ""  # running but RepoDigests empty / undeterminable
            continue
        digests[svc] = _bare_digest(repo_digest)
    return digests


def _running_agmind_containers() -> list[str]:
    """Return running ``agmind-*`` container names (``[]`` if docker absent)."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if proc.returncode != 0:
        return []
    return [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith(_CONTAINER_PREFIX)
    ]


# ---------------------------------------------------------------------------
# Individual check groups
# ---------------------------------------------------------------------------


def _check_env_health(
    env_path: Path,
    env: dict[str, str],
    compose: dict[str, Any] | None,
    selected: set[str],
    descriptors: dict[str, Any],
) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []

    # A1 — .env file mode must be 0600.
    try:
        mode = os.stat(env_path).st_mode & 0o777
    except OSError:
        mode = None
    if mode is not None and mode != 0o600:
        findings.append(
            ConfigFinding(
                id="env-file-mode",
                severity="error",
                message=f"{env_path} must be mode 0600 (secret file)",
                evidence=f"mode {mode:04o}",
                fixable=True,
                fix_cmd=f"sudo chmod 600 {env_path}",
            )
        )

    # A2 — every ${VAR:?...} referenced by the rendered compose must be present
    # (and non-empty) in .env. Authoritative required-key set for THIS deployment
    # (catches "secret generated but never written to .env"). We scan the
    # RE-SERIALIZED compose, not the raw file text, so a YAML COMMENT carrying a
    # ${X:?} form can't false-positive (safe_dump drops comments while preserving
    # command/args/DSN string VALUES — that coverage must NOT regress).
    if compose is not None:
        serialized = yaml.safe_dump(compose, default_flow_style=False, sort_keys=True)
        referenced: dict[str, str] = {}
        for var in _required_vars(serialized):
            referenced.setdefault(var, "")
        for var in referenced:
            svc = _service_referencing(compose, var)
            if svc:
                referenced[var] = svc
        for var in sorted(referenced):
            if not env.get(var):
                where = referenced[var]
                evidence = f"required by compose service '{where}'" if where else ""
                findings.append(
                    ConfigFinding(
                        id="env-required-var-missing",
                        severity="error",
                        message=(f"required variable {var} is missing or empty in {env_path.name}"),
                        evidence=evidence,
                    )
                )

    # A3 — a .env VALUE that still carries a literal ${...} placeholder.
    for key in sorted(env):
        if _VAR_RE.search(env[key]):
            findings.append(
                ConfigFinding(
                    id="env-unresolved-placeholder",
                    severity="error",
                    message=(
                        f"{key} still contains an unresolved ${{...}} placeholder in "
                        f"{env_path.name}"
                    ),
                    evidence=key,  # NEVER echo the value
                )
            )

    # A7 — a selected descriptor's secret env resolves to a weak/well-known
    # default (the dify ``changeme-*`` class). Never echoes the secret VALUE.
    from agmind.install.secrets_audit import find_weak_secret_envs

    selected_descriptors = {n: descriptors[n] for n in selected if n in descriptors}
    for msg in find_weak_secret_envs(selected_descriptors, env):
        findings.append(
            ConfigFinding(id="env-weak-default-secret", severity="error", message=msg, evidence="")
        )

    return findings


def _service_referencing(compose: dict[str, Any], var: str) -> str:
    """Best-effort: the first compose service whose serialized config mentions ``var``."""
    services = compose.get("services")
    if not isinstance(services, dict):
        return ""
    needle = "${" + var
    for name in sorted(services):
        try:
            blob = yaml.safe_dump(services[name])
        except yaml.YAMLError:
            continue
        if needle in blob:
            return str(name)
    return ""


def _check_secret_files(
    compose: dict[str, Any],
    selected: set[str],
) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    for svc, fname, _env_key in DB_SECRET_FILES:
        if svc not in selected:
            continue
        path = _secret_source_path(compose, svc, fname)
        reader_uid = DB_SECRET_FILE_READER_UID.get(fname)
        # Single stat: distinguishes missing (FileNotFoundError) from a present-but-
        # locked-down file. NOTE: a bare ``path.exists()`` itself raises
        # PermissionError when the secrets DIR is 0700 and we are not its owner —
        # so we must stat-with-catch, never ``.exists()``, on the live host.
        try:
            st = os.stat(path)
        except FileNotFoundError:
            findings.append(
                ConfigFinding(
                    id="secret-file-missing",
                    severity="error",
                    message=f"secret file for {svc} is missing",
                    evidence=str(path),
                    fixable=True,
                    fix_cmd="agmind install",
                )
            )
            continue
        except OSError as exc:
            # Can't stat (e.g. parent dir 0700, not owner). Only a hard finding when a
            # non-root reader_uid is required — otherwise we can't prove a problem.
            if reader_uid is not None:
                findings.append(
                    ConfigFinding(
                        id="secret-file-unreadable",
                        severity="error",
                        message=f"secret file for {svc} cannot be stat'd (run with sudo)",
                        evidence=f"{path}: {exc.__class__.__name__}",
                    )
                )
            continue
        if reader_uid is None:
            continue
        if not _uid_can_read(st.st_mode, st.st_uid, st.st_gid, reader_uid):
            findings.append(
                ConfigFinding(
                    id="secret-file-unreadable",
                    severity="error",
                    message=(
                        f"secret file for {svc} is not readable by its runtime uid "
                        f"{reader_uid} (image drops to non-root before reading it)"
                    ),
                    evidence=f"mode {stat.S_IMODE(st.st_mode):04o} owner {st.st_uid}:{st.st_gid}",
                    fixable=True,
                    fix_cmd=f"sudo chown {reader_uid}:{reader_uid} {path}",
                )
            )
    return findings


def _check_drift(
    selected: set[str],
    descriptors: dict[str, Any],
    running_digests: dict[str, str] | None,
) -> list[ConfigFinding]:
    findings: list[ConfigFinding] = []
    if running_digests is None:
        return [
            ConfigFinding(
                id="drift-skipped",
                severity="info",
                message="drift check skipped: docker is not available",
            )
        ]

    for svc in sorted(selected):
        running = running_digests.get(svc, _NOT_RUNNING)
        if running == _NOT_RUNNING:
            findings.append(
                ConfigFinding(
                    id="drift-not-running",
                    severity="warning",
                    message=f"service {svc} is in the compose selection but not running",
                    evidence=f"{_CONTAINER_PREFIX}{svc}",
                    fixable=True,
                    fix_cmd="agmind deploy --apply",
                )
            )
            continue
        if running == "":
            findings.append(
                ConfigFinding(
                    id="drift-digest-undeterminable",
                    severity="info",
                    message=f"running container for {svc} has no RepoDigests",
                    evidence=f"{_CONTAINER_PREFIX}{svc}",
                )
            )
            continue
        descriptor = descriptors.get(svc)
        pinned = _bare_digest(descriptor.digest or "") if descriptor is not None else ""
        if pinned and pinned != running:
            findings.append(
                ConfigFinding(
                    id="drift-digest-mismatch",
                    severity="error",
                    message=f"service {svc} runs a different image than pinned",
                    evidence=f"pinned {pinned[:19]} vs running {running[:19]}",
                    fixable=True,
                    fix_cmd="agmind deploy --apply",
                )
            )

    # B3 — running agmind-* container whose service is NOT in the selection.
    for name in _running_agmind_containers():
        svc = name[len(_CONTAINER_PREFIX) :]
        if svc and svc not in selected:
            findings.append(
                ConfigFinding(
                    id="drift-orphan",
                    severity="warning",
                    message=f"running container {name} is not in the compose selection",
                    evidence=name,
                    fixable=True,
                    fix_cmd="agmind gc",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_config(
    install_dir: Path,
    *,
    check_drift: bool = True,
    strict: bool = False,
) -> ConfigValidationReport:
    """Validate a live deployment rooted at ``install_dir``.

    Args:
        install_dir: directory holding the deployed ``.env`` + ``docker-compose.yml``.
        check_drift: when False (or docker absent), skip the pinned↔running checks.
        strict: when True, ``warning`` findings also flip ``report.ok`` to False.
    """
    install_dir = Path(install_dir)
    env_path = install_dir / ".env"
    compose_path = install_dir / "docker-compose.yml"
    findings: list[ConfigFinding] = []

    # ---- preamble: parse .env (root-owned-0600 live-crash class) ----
    env: dict[str, str] = {}
    env_ok = True
    if not env_path.exists():
        env_ok = False
        findings.append(
            ConfigFinding(
                id="env-file-missing",
                severity="error",
                message=f"{env_path} not found — is this a deployed install dir?",
                evidence=str(env_path),
            )
        )
    else:
        try:
            env = parse_env_file(env_path)
        except (PermissionError, OSError) as exc:
            env_ok = False
            findings.append(
                ConfigFinding(
                    id="env-file-unreadable",
                    severity="error",
                    message=f"{env_path} is unreadable (likely root-owned 0600 — run with sudo)",
                    evidence=f"{exc.__class__.__name__}",
                    fixable=True,
                    fix_cmd=f"sudo agmind config validate --install-dir {install_dir}",
                )
            )

    # ---- preamble: load descriptors + rendered compose ----
    descriptors = load_descriptors()
    compose: dict[str, Any] | None = None
    selected: set[str] = set()
    if not compose_path.exists():
        findings.append(
            ConfigFinding(
                id="compose-missing",
                severity="error",
                message=f"{compose_path} not found — stack is not rendered/deployed",
                evidence=str(compose_path),
            )
        )
    else:
        try:
            compose = _load_compose(compose_path)
        except (PermissionError, OSError) as exc:
            findings.append(
                ConfigFinding(
                    id="compose-missing",
                    severity="error",
                    message=f"{compose_path} is unreadable",
                    evidence=f"{exc.__class__.__name__}",
                )
            )
            compose = None
        else:
            selected = _selected_services(compose)

    # ---- (A) .env health (only if .env was readable) ----
    if env_ok:
        findings.extend(_check_env_health(env_path, env, compose, selected, descriptors))

    # ---- (A8) secret files (needs the compose selection) ----
    if compose is not None:
        findings.extend(_check_secret_files(compose, selected))

    # ---- (B) pinned↔running drift ----
    if not check_drift:
        pass  # explicitly suppressed by the caller; no finding emitted
    elif compose is None:
        pass  # no selection to diff against
    else:
        running = _running_image_digests(selected)
        findings.extend(_check_drift(selected, descriptors, running))

    findings = _sort_findings(findings)
    return ConfigValidationReport(findings=tuple(findings), strict=strict)


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _sort_findings(findings: list[ConfigFinding]) -> list[ConfigFinding]:
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.id, f.evidence, f.message),
    )


# Re-export the regex so callers/tests can confirm the required-var contract.
__all__ = [
    "ConfigFinding",
    "ConfigValidationReport",
    "validate_config",
]
