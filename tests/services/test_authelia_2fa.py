"""Live-audit 2026-06-05 (MED authelia-no-2fa-stepup): infra-CONTROL consoles require 2FA
step-up; everything else stays one_factor. Enrollment via the filesystem notifier (no lockout).
Validated with `authelia validate-config` (config schema accepted)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_CFG = Path(__file__).resolve().parents[2] / "templates/authelia/configuration.yml"


def test_admin_consoles_require_two_factor() -> None:
    cfg = yaml.safe_load(_CFG.read_text())
    ac = cfg["access_control"]
    assert ac["default_policy"] == "one_factor"  # baseline stays single-factor
    two_factor_domains: set[str] = set()
    for rule in ac.get("rules", []):
        if rule.get("policy") == "two_factor":
            dom = rule["domain"]
            two_factor_domains.update(dom if isinstance(dom, list) else [dom])
    # the infra-control consoles must be behind 2FA
    for svc in ("portainer", "grafana", "n8n"):
        assert f"{svc}.__AGMIND_DOMAIN__" in two_factor_domains, svc


def test_filesystem_notifier_present_so_enrollment_never_locks_out() -> None:
    cfg = yaml.safe_load(_CFG.read_text())
    assert "filesystem" in cfg["notifier"]  # TOTP enrollment deliverable without SMTP
