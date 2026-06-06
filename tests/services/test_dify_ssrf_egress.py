"""Live-audit 2026-06-05 (MED dify-api-worker-egress-unproxied): dify-api/worker route their
user-controlled HTTP-request-node + MCP/tool fetches through the ssrf-proxy cage. dify reads
SSRF_PROXY_* ONLY for these (verified live: api/core/mcp/utils.py) — provider/DB calls stay
direct, so chat/inference is unaffected."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


@pytest.mark.parametrize("svc", ["dify-api", "dify-worker"])
def test_dify_user_fetches_routed_through_ssrf_proxy(svc: str) -> None:
    env = load_descriptors()[svc].env
    assert env["SSRF_PROXY_HTTP_URL"] == "http://ssrf-proxy:3128"
    assert env["SSRF_PROXY_HTTPS_URL"] == "http://ssrf-proxy:3128"
    # provider/DB creds stay as direct env (NOT proxied) — chat/inference unaffected
    assert "ssrf" not in env.get("DB_HOST", "").lower()
