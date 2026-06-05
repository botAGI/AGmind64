"""Read-only security posture scan of the DEPLOYED AGmind artifacts.

Audits operator drift in the install dir (hand-edited ``docker-compose.yml``,
weak operator ``.env`` values, world-readable secret files) — the runtime
counterpart to the build-time catalog contract
(``tests/security/test_privilege_port_secret_contract.py``), which already gates
the descriptors. Findings carry a 5-level severity; the CLI exit code gates on a
``--block`` threshold.

Compose is parsed STRUCTURALLY (yaml.safe_load), never by raw-text grep — so a
``--server.http.listen-addr=0.0.0.0:12345`` *command arg* is not mistaken for an
exposed published port. Secret VALUES are never emitted (SC2 invariant): findings
report the key and the reason only.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agmind.install.secrets_audit import (
    _SECRET_KEY_RE,
    _WEAK_EXACT,
    _WEAK_SUBSTRINGS,
    find_weak_secret_envs,
)

SEVERITY_LEVELS: tuple[str, ...] = ("info", "low", "medium", "high", "critical")
_RANK = {level: i for i, level in enumerate(SEVERITY_LEVELS)}

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_DOCKER_SOCK = "/var/run/docker.sock"
# The one intentional edge exposure, consistent with the catalog contract.
_EDGE_ALLOW = {("traefik", "80"), ("traefik", "443")}
_MIN_SECRET_LEN = 12


@dataclass(frozen=True)
class SecurityFinding:
    """One posture finding. ``detail``/``fix`` never contain a secret value."""

    check: str
    severity: str
    target: str
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "target": self.target,
            "detail": self.detail,
            "fix": self.fix,
        }


def max_severity(findings: Sequence[SecurityFinding]) -> str:
    """Highest severity among ``findings`` (``"info"`` when empty)."""
    return max((f.severity for f in findings), key=lambda s: _RANK[s], default="info")


def gate_exit(findings: Sequence[SecurityFinding], block: str = "high") -> int:
    """1 when any finding is >= ``block`` severity, else 0."""
    threshold = _RANK[block]
    return 1 if any(_RANK[f.severity] >= threshold for f in findings) else 0


def _port_bind(spec: Any) -> tuple[str | None, str | None]:
    """Return (host_bind, host_port) for a compose port spec, or (None, None) if not published.

    Strips IPv6 brackets. ``host_bind`` is "" for the implicit-all-interfaces
    ``host:container`` form.
    """
    if isinstance(spec, dict):
        if spec.get("published") in (None, ""):
            return (None, None)
        host_ip = str(spec.get("host_ip", "")).strip("[]")
        return (host_ip, str(spec.get("published")))
    text = str(spec).split("/", 1)[0]  # drop /tcp,/udp
    parts = text.rsplit(":", 2)
    if len(parts) == 1:
        return (None, None)  # container-only port, not published
    if len(parts) == 2:
        return ("", parts[0])  # host:container → implicit all-interfaces
    return (parts[0].strip("[]"), parts[1])  # ip:host:container


def scan_compose(text: str) -> list[SecurityFinding]:
    """Flag exposed published ports, privileged containers, and docker.sock mounts."""
    findings: list[SecurityFinding] = []
    doc = yaml.safe_load(text) or {}
    services = doc.get("services") or {}
    if not isinstance(services, dict):
        return findings

    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        for spec in svc.get("ports") or []:
            bind, host_port = _port_bind(spec)
            if bind is None:
                continue
            if bind in _LOOPBACK:
                continue
            if (str(name), str(host_port)) in _EDGE_ALLOW:
                continue
            findings.append(
                SecurityFinding(
                    "exposed-port",
                    "high",
                    f"{name}:{host_port}",
                    f"published on {bind or 'all interfaces'} (not loopback)",
                    "bind to 127.0.0.1 or front with the reverse proxy",
                )
            )

        if svc.get("privileged") is True:
            findings.append(
                SecurityFinding(
                    "privileged",
                    "critical",
                    str(name),
                    "runs as a privileged container",
                    "drop privileged; grant only the specific cap_add it needs",
                )
            )

        for vol in svc.get("volumes") or []:
            vtext = vol if isinstance(vol, str) else ""
            if _DOCKER_SOCK in vtext:
                read_only = vtext.rstrip().endswith(":ro")
                findings.append(
                    SecurityFinding(
                        "docker-sock",
                        "medium" if read_only else "high",
                        str(name),
                        f"mounts the Docker socket ({'ro' if read_only else 'rw'})",
                        "use :ro and gate the container behind auth, or drop the mount",
                    )
                )
    return findings


def scan_env(env: Mapping[str, str], descriptors: Mapping[str, Any]) -> list[SecurityFinding]:
    """Flag weak/default secrets — from descriptor defaults AND operator .env values.

    Never emits the value: only the key and the reason.
    """
    findings: list[SecurityFinding] = []
    for message in find_weak_secret_envs(descriptors, env):
        findings.append(
            SecurityFinding(
                "weak-secret", "high", message.split(" ", 1)[0], "resolves to a weak/default secret"
            )
        )
    for key, value in env.items():
        if not value or not _SECRET_KEY_RE.search(key):
            continue
        low = value.lower()
        if any(tok in low for tok in _WEAK_SUBSTRINGS) or low in _WEAK_EXACT:
            findings.append(
                SecurityFinding("weak-secret", "high", key, "default/placeholder value")
            )
        elif len(value) < _MIN_SECRET_LEN:
            findings.append(
                SecurityFinding(
                    "weak-secret", "medium", key, f"shorter than {_MIN_SECRET_LEN} characters"
                )
            )
    return findings


def scan_file_perms(paths: Sequence[Path]) -> list[SecurityFinding]:
    """Flag secret files readable by group/other (should be 0600)."""
    findings: list[SecurityFinding] = []
    for path in paths:
        try:
            if not path.exists():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            # Can't inspect (e.g. a secret in a root-owned 0700 dir, not traversable by the
            # non-root audit user) — skip, don't crash the whole `agmind security audit`. The
            # file being unreachable to this user is itself a sign the perms are tight.
            continue
        if mode & 0o007:
            severity = "high"
        elif mode & 0o070:
            severity = "medium"
        else:
            continue
        findings.append(
            SecurityFinding(
                "file-perms",
                severity,
                str(path),
                f"mode {mode:04o} is group/other-readable",
                f"chmod 600 {path}",
            )
        )
    return findings


# The Authelia documentation EXAMPLE password hash (login admin / password "authelia").
# Its salt segment is a stable fingerprint regardless of the surrounding params.
_AUTHELIA_EXAMPLE_HASH_SALT = "BpLnfgDsc2WD8F2q"


def scan_authelia_users_db(path: Path) -> list[SecurityFinding]:
    """Flag the Authelia file backend still carrying the upstream EXAMPLE password hash.

    The weak-default ``.env`` scan cannot catch this because the credential lives in a
    file (users_database.yml), not an env var (audit M#24).
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if _AUTHELIA_EXAMPLE_HASH_SALT not in text:
        return []
    return [
        SecurityFinding(
            "authelia-default-password",
            "high",
            "authelia",
            f"{path} still contains the upstream Authelia EXAMPLE password hash — anyone "
            "can sign in to the SSO as admin/authelia. Reinstall so a strong password is "
            "generated + hashed, or replace the hash with `authelia crypto hash generate argon2`.",
            "reinstall (generates a strong admin password) or set a real password hash",
        )
    ]


def audit_install(
    install_dir: Path,
    *,
    live: bool = False,
    config_dir: Path | None = None,
    data_dir: Path | None = None,
) -> tuple[list[SecurityFinding], bool]:
    """Audit the deployed artifacts under ``install_dir``.

    Returns ``(findings, installed)``. ``installed`` is False when no
    ``docker-compose.yml`` is present (the CLI maps that to exit 2).
    """
    install_dir = Path(install_dir)
    data_root = Path(data_dir) if data_dir is not None else Path("/var/lib/agmind")
    compose_path = install_dir / "docker-compose.yml"
    env_path = install_dir / ".env"
    if not compose_path.is_file():
        return ([], False)

    compose_text = compose_path.read_text(encoding="utf-8")
    findings = scan_compose(compose_text)

    env: dict[str, str] = {}
    env_readable = False
    if env_path.is_file():
        from agmind.core.env import parse_env_file

        try:
            env = parse_env_file(env_path)
            env_readable = True
        except OSError as exc:
            # .env is root:root 0600 on a real deploy; a non-root `agmind security audit` can't
            # read it. Degrade gracefully (skip the env-value scan) with an info finding instead
            # of crashing the whole audit (surfaced live — same class as scan_file_perms).
            findings.append(
                SecurityFinding(
                    "env-unreadable",
                    # warning (not info): the secret-VALUE scan is SKIPPED entirely as non-root, so
                    # weak/duplicate secrets would go undetected — surface that the posture scan is
                    # incomplete rather than burying it (live-audit 2026-06-05 env-scan-noops).
                    "warning",
                    str(env_path),
                    f"could not read .env to scan secret values ({exc}); the secret-strength scan "
                    "was SKIPPED — re-run as the install owner or with `sudo` for the full scan",
                    "",
                )
            )
    # Scope the descriptor weak-default check to the DEPLOYED services only — this
    # is an audit of the operator's artifacts, NOT a re-validation of the whole
    # catalog (that is the build-time contract test's job). Checking undeployed
    # descriptors against a partial .env would false-flag services that aren't here.
    deployed = set((yaml.safe_load(compose_text) or {}).get("services") or {})
    descriptors: Mapping[str, Any] = {}
    try:
        from agmind.services.renderer import load_descriptors

        descriptors = {n: d for n, d in load_descriptors().items() if n in deployed}
    except Exception:  # noqa: BLE001 - descriptor load is best-effort for the env scan
        descriptors = {}
    # Only scan when .env was actually read. With an unreadable .env, the descriptor-default
    # check resolves `${GENERATED_VAR:-changeme}` to its WEAK default and false-flags it HIGH —
    # but the installer always generates that VAR, so the default is dead (the build-time
    # contract test gates descriptor defaults anyway). The env-unreadable info finding above
    # already records that the value scan was skipped.
    if env_readable:
        findings += scan_env(env, descriptors)
    cfg_dir = config_dir if config_dir is not None else Path("/etc/agmind")
    # Perms-scan EVERY 0600 secret artifact, not just .env — the scan's point is to catch
    # operator drift (a later chmod, a loosened restore). credentials.txt, the Cloudflare DNS
    # token, and the Alertmanager notifier secrets are all 0600 at write time (review MEDIUM
    # security-scan-omits-secret-files). version.env is intentionally world-readable (non-secret)
    # so it is NOT in this list; users_database.yml is 0644 by design (Authelia reads it) and is
    # checked for the default password by scan_authelia_users_db instead.
    findings += scan_file_perms(
        [
            env_path,
            install_dir / "credentials.txt",
            data_root / "secrets" / "cf_dns_api_token",
            cfg_dir / "alertmanager" / "tg_bot_token",
            cfg_dir / "alertmanager" / "webhook_url",
            cfg_dir / "alertmanager" / "smtp_password",
        ]
    )
    findings += scan_authelia_users_db(cfg_dir / "authelia" / "users_database.yml")

    if live:
        # Honest signal instead of a fake "live verified" info-finding: live container
        # inspection is not implemented (review LOW security-live-stub). The CLI fail-fasts
        # before calling, so this only guards a direct library caller.
        raise NotImplementedError(
            "live container inspection is not yet implemented; call audit_install without live=True"
        )
    return (findings, True)
