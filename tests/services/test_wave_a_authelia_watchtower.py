"""Night Wave A — live-audit 2026-06-07.
UI-1 portal-no-landing: Authelia direct-login lands on the homarr portal (default_redirection_url).
SEC-6 authelia-hardening: brute-force regulation + bounded sessions + password_policy.
SEC-2 watchtower-no-new-priv: the socket-holding updater blocks privilege escalation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_CFG = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "templates/authelia/configuration.yml").read_text()
)


def test_authelia_direct_login_redirects_to_portal() -> None:
    cookie = _CFG["session"]["cookies"][0]
    assert cookie["default_redirection_url"] == "https://homarr.__AGMIND_DOMAIN__"


def test_authelia_sessions_are_bounded() -> None:
    cookie = _CFG["session"]["cookies"][0]
    assert cookie["expiration"] and cookie["inactivity"]  # not unbounded


def test_authelia_has_bruteforce_regulation_and_password_policy() -> None:
    assert _CFG["regulation"]["max_retries"] >= 1
    assert _CFG["regulation"]["find_time"] and _CFG["regulation"]["ban_time"]
    assert _CFG["password_policy"]["zxcvbn"]["enabled"] is True


def test_watchtower_blocks_privilege_escalation() -> None:
    assert load_descriptors()["watchtower"].no_new_privileges is True
