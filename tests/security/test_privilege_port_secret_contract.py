"""Fail-closed catalog security contracts: port + privilege + secret + group_add + write-path.

Implements RESEARCH-ADVANCED-2026-05-31.md §2 contracts C1, C2, C4 plus catalog-wide
group_add→numeric-GID and write-path-under-writable-volume guards.

Mutation evidence (recorded in commit):
  C1 port:
    Added 0.0.0.0:5432:5432 to grafana.yaml ports → test_only_edge_binds_non_loopback
    FAILED naming grafana; mutation reverted.
  C2 cap_add:
    Added cap_add: [NET_ADMIN] to grafana.yaml → test_cap_add_allowlist FAILED naming
    grafana; mutation reverted.
  C2 seccomp:
    Added security_opt: [seccomp=unconfined] to grafana.yaml →
    test_security_opt_seccomp_unconfined_allowlist FAILED naming grafana; reverted.
  C2 apparmor:
    Added security_opt: [apparmor:unconfined] to grafana.yaml →
    test_security_opt_apparmor_unconfined_allowlist FAILED naming grafana; reverted.
  group_add:
    Added group_add: [badgroup] to grafana.yaml then ran the catalog-wide renderer test →
    test_group_add_renders_numeric_gid_for_all FAILED (rendered non-numeric 'badgroup');
    mutation reverted.
  write-path:
    Modified loki.yaml command to --ruler.storage.local.directory=/tmp/ruler →
    test_write_path_under_writable_volume FAILED (ruler dir /tmp/ruler not under /loki);
    mutation reverted.
  secret-mount:
    Modified traefik.yaml cf_dns_api_token volume to drop :ro →
    test_secret_bind_mounts_are_readonly FAILED naming traefik cf_dns_api_token; reverted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import agmind.services.renderer as renderer
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any

SERVICES_DIR = Path(__file__).resolve().parents[2] / "templates" / "services"

# ---- C1: only edge services may bind non-loopback ----
# EDGE_PUBLIC may bind host 80 / 443 (no IP prefix = 0.0.0.0 / any interface).
EDGE_PUBLIC = {"traefik", "nginx", "caddy"}
EDGE_PUBLIC_PORTS = {80, 443}

# ---- C2: cap_add allowlist (fail-closed) ----
# Only netdata may add Linux capabilities; no other service has any justification today.
CAP_ADD_ALLOW: dict[str, set[str]] = {
    "netdata": {"SYS_PTRACE", "SYS_ADMIN"},
}

# ---- C2: seccomp=unconfined allowlist (enumerated from catalog 2026-05-31) ----
# grep 'seccomp' templates/services/*.yaml → llama-llm (=unconfined) + milvus (:unconfined)
# Normalize both forms: seccomp=unconfined and seccomp:unconfined are equivalent.
SECCOMP_UNCONFINED_ALLOW = {"llama-llm", "milvus"}

# ---- C2: apparmor:unconfined allowlist ----
APPARMOR_UNCONFINED_ALLOW = {"netdata"}

# ---- C4: secret path patterns ----
SECRET_PATH_PREFIXES = (
    "/var/lib/agmind/secrets/",
    "/opt/agmind/.env",
    "/run/secrets/",  # runtime secret mount path (used by traefik)
)
# These patterns in the mount SOURCE path indicate a secret bind-mount:
SECRET_SRC_PATTERNS = (
    "/var/lib/agmind/secrets/",
    "/opt/agmind/.env",
)

# ---- group_add pass-through allowlist ----
# The docker group name passes through unchanged (it IS the group name in every image).
# GPU groups (render, video) are converted to numeric GIDs by the renderer.
GROUP_PASSTHROUGH_ALLOW = {"docker"}


def _all_descriptors() -> dict[str, ServiceDescriptor]:
    """Load every templates/services/*.yaml via ServiceDescriptor.model_validate."""
    result: dict[str, ServiceDescriptor] = {}
    for yaml_path in sorted(SERVICES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        descriptor = ServiceDescriptor.model_validate(raw)
        result[descriptor.name] = descriptor
    return result


# ---------------------------------------------------------------------------
# C1: host-port bind contract
# ---------------------------------------------------------------------------


def test_only_edge_binds_non_loopback() -> None:
    """C1: Only EDGE_PUBLIC services may bind non-loopback ports (80/443 only).

    A port entry without an IP prefix (e.g. '80:80', '443:443') binds on
    0.0.0.0 (all interfaces) and is visible on the LAN.  Only traefik/nginx/caddy
    are allowed to do this, and only on ports 80 and 443.

    Any other service with a non-loopback bind fails CI.  This prevents internal
    services from being accidentally exposed on the LAN after a copy-paste error.

    Mutation-verified: adding '0.0.0.0:5432:5432' to grafana.yaml caused this test
    to fail naming grafana (mutation reverted).
    """
    descriptors = _all_descriptors()
    violations: list[str] = []
    for name, d in descriptors.items():
        for port_str in d.ports:
            parts = port_str.split(":")
            if len(parts) == 3:
                bind_ip = parts[0]
                host_port = int(parts[1])
            elif len(parts) == 2:
                # No IP prefix → binds 0.0.0.0 (non-loopback)
                bind_ip = "0.0.0.0"
                host_port = int(parts[0])
            else:
                continue

            if bind_ip in ("127.0.0.1", "::1"):
                continue  # loopback — OK

            # Non-loopback: only EDGE_PUBLIC on 80/443
            if name not in EDGE_PUBLIC:
                violations.append(
                    f"{name}: non-loopback port {port_str!r} — "
                    f"only {sorted(EDGE_PUBLIC)} may bind non-loopback"
                )
            elif host_port not in EDGE_PUBLIC_PORTS:
                violations.append(
                    f"{name}: non-loopback port {port_str!r} — "
                    f"EDGE_PUBLIC may only bind ports {sorted(EDGE_PUBLIC_PORTS)}"
                )

    assert not violations, "C1 host-port contract violated:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ---------------------------------------------------------------------------
# C2: privilege flags allowlists (fail-closed)
# ---------------------------------------------------------------------------


def test_cap_add_allowlist() -> None:
    """C2: cap_add is restricted to the explicit per-service allowlist.

    Only netdata may add Linux capabilities (SYS_PTRACE + SYS_ADMIN for system
    metrics collection).  No other service has a documented justification today.

    A new service adding any cap_add entry fails CI until added to CAP_ADD_ALLOW
    with a documented security reason.

    Mutation-verified: adding cap_add: [NET_ADMIN] to grafana.yaml caused this test
    to fail naming grafana (mutation reverted).
    """
    descriptors = _all_descriptors()
    violations: list[str] = []
    for name, d in descriptors.items():
        if not d.cap_add:
            continue
        allowed_caps = CAP_ADD_ALLOW.get(name, set())
        for cap in d.cap_add:
            if cap not in allowed_caps:
                violations.append(
                    f"{name}: cap_add {cap!r} not in allowlist "
                    f"(CAP_ADD_ALLOW[{name!r}]={sorted(allowed_caps)!r})"
                )
    assert not violations, "C2 cap_add contract violated:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_security_opt_seccomp_unconfined_allowlist() -> None:
    """C2: seccomp=unconfined (or seccomp:unconfined) is restricted to the enumerated allowlist.

    Both '=' and ':' forms are treated as equivalent (Docker Compose normalizes them).
    Current allowlist: llama-llm (jemalloc/vulkan syscalls) + milvus (jemalloc/Knowhere).

    Mutation-verified: adding security_opt: [seccomp=unconfined] to grafana.yaml caused
    this test to fail naming grafana (mutation reverted).
    """
    descriptors = _all_descriptors()
    violations: list[str] = []
    for name, d in descriptors.items():
        for opt in d.security_opt:
            normalized = opt.replace("=", ":").lower()
            if normalized == "seccomp:unconfined":
                if name not in SECCOMP_UNCONFINED_ALLOW:
                    violations.append(
                        f"{name}: security_opt {opt!r} not in SECCOMP_UNCONFINED_ALLOW "
                        f"({sorted(SECCOMP_UNCONFINED_ALLOW)!r})"
                    )
    assert not violations, "C2 seccomp=unconfined contract violated:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_security_opt_apparmor_unconfined_allowlist() -> None:
    """C2: apparmor:unconfined is restricted to the enumerated allowlist.

    Current allowlist: netdata (system-metrics profiling).

    Mutation-verified: adding security_opt: [apparmor:unconfined] to grafana.yaml caused
    this test to fail naming grafana (mutation reverted).
    """
    descriptors = _all_descriptors()
    violations: list[str] = []
    for name, d in descriptors.items():
        for opt in d.security_opt:
            if opt.lower().startswith("apparmor:") and "unconfined" in opt.lower():
                if name not in APPARMOR_UNCONFINED_ALLOW:
                    violations.append(
                        f"{name}: security_opt {opt!r} not in APPARMOR_UNCONFINED_ALLOW "
                        f"({sorted(APPARMOR_UNCONFINED_ALLOW)!r})"
                    )
    assert not violations, "C2 apparmor:unconfined contract violated:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_no_privileged_anywhere() -> None:
    """C2: No service may use the 'privileged:' flag.

    ServiceDescriptor has no 'privileged' field (extra='forbid' would reject it),
    so this test verifies the raw YAML does not contain a 'privileged: true' line.
    A future schema addition of a privileged field would need to pass this gate.
    """
    violations: list[str] = []
    for yaml_path in sorted(SERVICES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("privileged"):
            violations.append(f"{yaml_path.stem}: privileged: true is not allowed")
    assert not violations, "C2 privileged flag found:\n" + "\n".join(f"  - {v}" for v in violations)


# ---------------------------------------------------------------------------
# group_add → numeric GID (catalog-wide closure)
# ---------------------------------------------------------------------------


class _Grp:
    def __init__(self, gid: int) -> None:
        self.gr_gid = gid


_DETERMINISTIC_GRP: dict[str, _Grp] = {
    "render": _Grp(992),
    "video": _Grp(44),
}


def _mock_getgrnam(name: str) -> _Grp:
    """Return a deterministic GID for known GPU groups; raise KeyError for others."""
    if name in _DETERMINISTIC_GRP:
        return _DETERMINISTIC_GRP[name]
    raise KeyError(name)


def test_group_add_renders_numeric_gid_for_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalog-wide: every descriptor with group_add renders to numeric GID strings.

    The renderer maps AMD-GPU group names (render, video) to host numeric GIDs.
    Non-GPU names pass through — but in real images without /etc/group entries
    a name would crash the container.  This test asserts:
      - Every rendered group_add entry is all-digit (numeric) OR in GROUP_PASSTHROUGH_ALLOW
        (currently: {'docker'} which IS the group name in every real image).

    This is the catalog-wide closure; it does not duplicate the three unit-level tests
    in tests/services/test_group_add_gid.py.

    Mutation-verified: adding group_add: [badgroup] to grafana.yaml (not in catalog today,
    injected the renderer returns non-numeric string 'badgroup') → this test FAILED naming
    grafana (mutation reverted).
    """
    monkeypatch.setattr(renderer.grp, "getgrnam", _mock_getgrnam)

    descriptors = _all_descriptors()
    violations: list[str] = []

    for name, d in descriptors.items():
        if not d.group_add:
            continue
        rendered_svc = renderer.descriptor_to_compose_service(d)
        rendered_groups: list[str] = rendered_svc.get("group_add", [])

        for entry in rendered_groups:
            is_numeric = entry.isdigit()
            is_allowed_passthrough = entry in GROUP_PASSTHROUGH_ALLOW
            if not is_numeric and not is_allowed_passthrough:
                violations.append(
                    f"{name}: rendered group_add entry {entry!r} is not numeric and "
                    f"not in GROUP_PASSTHROUGH_ALLOW ({sorted(GROUP_PASSTHROUGH_ALLOW)!r}) — "
                    f"would crash in a minimal image without /etc/group entry"
                )

    assert not violations, "group_add renders non-numeric GID in catalog:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ---------------------------------------------------------------------------
# write-path under writable-volume guard (loki-ruler regression class)
# ---------------------------------------------------------------------------


def _parse_container_path(volume_spec: str) -> str | None:
    """Extract the container-side path from a volume spec 'src:dst[:mode]'."""
    parts = volume_spec.split(":")
    if len(parts) < 2:
        return None
    dst = parts[1]
    # Exclude mode tokens like 'ro', 'rw'
    if dst in ("ro", "rw"):
        return None
    return dst


def test_write_path_under_writable_volume() -> None:
    """For loki: ruler + storage write paths must be under the /loki writable mount.

    The 2026-05-30 loki-ruler regression class: loki was configured with
    --ruler.storage.local.directory=/var/lib/loki/ruler which lived OUTSIDE the /loki
    mount, so ruler data was lost on container restart (written to ephemeral container FS).

    This test asserts the loki command flags for ruler and storage path targets are
    prefixes of a declared container volume mount path.

    NOTE: the fix was applied in the 2026-05-30 sweep (commit 6516469) — this test
    asserts the EXISTING-GREEN invariant and guards against regression.

    Mutation-verified: modifying loki.yaml command to include
    --ruler.storage.local.directory=/tmp/ruler caused this test to FAIL
    (ruler dir /tmp/ruler not under any loki volume mount); mutation reverted.
    """
    descriptors = _all_descriptors()
    loki = descriptors.get("loki")
    if loki is None:
        pytest.skip("loki descriptor not in catalog")

    # Collect container-side volume mount paths for loki
    container_mount_paths: list[str] = []
    for vol in loki.volumes:
        cp = _parse_container_path(vol)
        if cp:
            container_mount_paths.append(cp)

    if not loki.command:
        return  # no command → no write-path flags to check

    # Find --*.path=... or --*-directory=... style write-path flags in command
    write_path_flags: list[tuple[str, str]] = []
    for arg in loki.command:
        if "=" in arg:
            flag, _, value = arg.partition("=")
            flag_lower = flag.lower()
            if any(
                kw in flag_lower
                for kw in (
                    ".path",
                    ".directory",
                    "storage.path",
                    "ruler",
                    ".dir",
                )
            ):
                if value.startswith("/"):
                    write_path_flags.append((flag, value))

    # Assert each write path is a prefix of a declared container mount
    violations: list[str] = []
    for flag, write_path in write_path_flags:
        is_covered = any(
            write_path == mount_path or write_path.startswith(mount_path.rstrip("/") + "/")
            for mount_path in container_mount_paths
        )
        if not is_covered:
            violations.append(
                f"loki: {flag}={write_path!r} is not under any declared volume mount "
                f"(mounts: {container_mount_paths!r}) — data would be written to "
                f"ephemeral container FS and lost on restart (loki-ruler regression)"
            )

    assert not violations, "loki write-path outside writable volume:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ---------------------------------------------------------------------------
# C4: secret bind-mounts must be :ro
# ---------------------------------------------------------------------------


def test_secret_bind_mounts_are_readonly() -> None:
    """C4: Secret bind-mounts must be mounted read-only (:ro).

    Secret paths (/var/lib/agmind/secrets/, /run/secrets/) must be :ro.
    A writable secret mount leaks write access to the secret store, potentially
    allowing a compromised container to overwrite tokens/credentials for other services.

    Current catalog: traefik mounts /var/lib/agmind/secrets/cf_dns_api_token:ro.

    Mutation-verified: removing :ro from the traefik cf_dns_api_token volume mount
    caused this test to FAIL naming traefik with the cf_dns_api_token path; mutation
    reverted.
    """
    descriptors = _all_descriptors()
    violations: list[str] = []

    secret_volume_prefixes = (
        "/var/lib/agmind/secrets/",
        "/run/secrets/",
        "/opt/agmind/.env",
    )

    for name, d in descriptors.items():
        for vol in d.volumes:
            parts = vol.split(":")
            src = parts[0]
            # Check if source is a secret path
            is_secret = any(
                src.startswith(prefix) or src == prefix.rstrip("/")
                for prefix in secret_volume_prefixes
            )
            if not is_secret:
                continue
            # Determine mount mode
            mode = parts[-1] if len(parts) >= 3 and parts[-1] in ("ro", "rw") else "rw"
            if mode != "ro":
                violations.append(
                    f"{name}: secret mount {vol!r} must be :ro (source {src!r} is a secret path)"
                )

    assert not violations, "C4 secret-mount contract violated:\n" + "\n".join(
        f"  - {v}" for v in violations
    )
