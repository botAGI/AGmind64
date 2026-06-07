"""Authelia access policy. The 2FA step-up for infra-control consoles was DISABLED at the operator's
request (2026-06-07): the filesystem-notifier OTC enrollment was too clunky on self-host without SMTP.
Everything is now one_factor. This guards that decision (no rule re-introduces two_factor without an
SMTP notifier to deliver the enrollment code). Validated with `authelia validate-config`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_CFG = Path(__file__).resolve().parents[2] / "templates/authelia/configuration.yml"


def test_no_two_factor_without_smtp_notifier() -> None:
    """2FA step-up was removed (operator: the file-OTC enrollment was too clunky). Guard that no rule
    silently re-introduces two_factor unless an SMTP notifier exists to deliver the enrollment code —
    else the operator dead-ends at the identity-verification dialog again."""
    cfg = yaml.safe_load(_CFG.read_text())
    ac = cfg["access_control"]
    assert ac["default_policy"] == "one_factor"
    two_factor_domains: set[str] = set()
    for rule in ac.get("rules", []):
        if rule.get("policy") == "two_factor":
            dom = rule["domain"]
            two_factor_domains.update(dom if isinstance(dom, list) else [dom])
    if two_factor_domains:
        assert "smtp" in cfg.get("notifier", {}), (
            "two_factor rules require an SMTP notifier to deliver the enrollment OTC "
            f"(file-only OTC is too clunky on self-host); offending: {sorted(two_factor_domains)}"
        )


def test_filesystem_notifier_present_so_enrollment_never_locks_out() -> None:
    cfg = yaml.safe_load(_CFG.read_text())
    assert "filesystem" in cfg["notifier"]  # config/notifier deliverable without SMTP
