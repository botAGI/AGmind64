"""Live-audit 2026-06-05: the internet-facing app tier blocks privilege escalation
(no_new_privileges) and the untrusted-code sandbox bounds its PIDs. Regression guard so
the hardening is not silently dropped from a descriptor."""

from __future__ import annotations

import pytest

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


def test_app_tier_sets_no_new_privileges() -> None:
    descriptors = load_descriptors()
    for name in _MUST_NO_NEW_PRIVILEGES:
        assert descriptors[name].no_new_privileges is True, (
            f"{name}: no_new_privileges must stay True (hardening regression)"
        )


def test_dify_sandbox_bounds_pids() -> None:
    """The untrusted-code sandbox must cap PIDs (fork-bomb / PID-exhaustion guard)."""
    descriptors = load_descriptors()
    assert descriptors["dify-sandbox"].pids_limit == 512
