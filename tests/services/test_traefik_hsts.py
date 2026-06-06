"""Live-audit 2026-06-05 (LOW tls-min-version-12-no-hsts-preload): the edge already pins
minVersion TLS1.2 + HSTS with includeSubdomains; opt into the browser HSTS preload list too."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]


def test_security_headers_enable_hsts_preload() -> None:
    mw = yaml.safe_load((_REPO / "templates/traefik/dynamic/middlewares.yml").read_text())
    h = mw["http"]["middlewares"]["default-security-headers"]["headers"]
    assert h["stsPreload"] is True
    assert h["stsIncludeSubdomains"] is True
    assert h["stsSeconds"] >= 31536000  # >= 1 year (preload requirement)


def test_tls_min_version_is_12() -> None:
    t = yaml.safe_load((_REPO / "templates/traefik/dynamic/transport.yml").read_text())
    for opt in t["tls"]["options"].values():
        assert opt["minVersion"] == "VersionTLS12"
