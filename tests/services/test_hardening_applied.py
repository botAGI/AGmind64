"""Live-audit 2026-06-05 + SEC-2 (security-live 2026-06-08): every service blocks runtime
privilege escalation (no_new_privileges) and the untrusted-code sandbox bounds its PIDs.
Regression guard so the stack-wide hardening baseline is not silently dropped from a descriptor."""

from __future__ import annotations

import pytest

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

# Internet-facing / app-tier services that must block setuid/file-cap privilege escalation.
# Data-tier DBs are intentionally excluded (their entrypoints' gosu-drop is NOT affected by
# no-new-privileges, but they are kept conservative pending a live boot per-image).
_MUST_NO_NEW_PRIVILEGES = frozenset(
    {
        "openwebui",
        "n8n",
        "ragflow",
        "dify-web",
        "dify-api",
        "dify-worker",
        "dify-plugin-daemon",
        "dify-sandbox",
        "grafana",
        # ops-tier docker-socket holders (defense-in-depth)
        "portainer",
    }
)

# SEC-2 exclusions: services that genuinely need to GAIN privileges at runtime, so
# no_new_privileges is intentionally omitted (each documented in its descriptor).
#   netdata: runs setuid-root collector plugins (apps.plugin/ndsudo/debugfs.plugin) — nnp
#            would void the setuid bit and break deep host-metric collection.
_NNP_EXCLUSIONS = frozenset({"netdata"})


def _effective_no_new_privileges(d: ServiceDescriptor) -> bool:
    """True if the descriptor blocks privilege escalation — via the no_new_privileges
    field OR a literal ``no-new-privileges:true`` already in security_opt (ssrf-proxy)."""
    if d.no_new_privileges:
        return True
    return any(opt.replace("=", ":").lower() == "no-new-privileges:true" for opt in d.security_opt)


def test_app_tier_sets_no_new_privileges() -> None:
    descriptors = load_descriptors()
    for name in _MUST_NO_NEW_PRIVILEGES:
        assert descriptors[name].no_new_privileges is True, (
            f"{name}: no_new_privileges must stay True (hardening regression)"
        )


def test_no_new_privileges_is_the_catalog_baseline() -> None:
    """SEC-2: every service blocks runtime privilege escalation except the documented
    _NNP_EXCLUSIONS. Adding a new descriptor without no_new_privileges (and not in the
    exclusion set) fails this gate — the secure-by-default posture is enforced, not opt-in."""
    descriptors = load_descriptors()
    missing = sorted(
        name
        for name, d in descriptors.items()
        if name not in _NNP_EXCLUSIONS and not _effective_no_new_privileges(d)
    )
    assert not missing, (
        "These services lack no_new_privileges and are not in _NNP_EXCLUSIONS "
        f"(add the field or document the exclusion): {missing}"
    )


def test_dify_sandbox_bounds_pids() -> None:
    """The untrusted-code sandbox must cap PIDs (fork-bomb / PID-exhaustion guard)."""
    descriptors = load_descriptors()
    assert descriptors["dify-sandbox"].pids_limit == 512
