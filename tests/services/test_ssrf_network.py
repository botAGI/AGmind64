"""Multi-network rendering + SSRF cage for dify-sandbox.

The renderer was single-`default`-network; this adds a per-service `networks`
field so a service can be pinned to an internal-only network (no host route).
Empty networks must stay byte-identical to the old single-net output, and the
ssrf-net cage must be `internal: true` (the load-bearing security primitive).
"""

from __future__ import annotations

import pytest
import yaml

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import (
    descriptor_to_compose_service,
    load_descriptors,
    render_compose,
    render_to_string,
)

pytestmark = pytest.mark.backend_any


def _desc(name: str, **kw) -> ServiceDescriptor:
    return ServiceDescriptor(name=name, image="example/x:1.0.0", tier="app", **kw)


# ---- schema + per-service emission ----


def test_schema_accepts_networks_field() -> None:
    d = _desc("sandbox", networks=["ssrf-net"])
    assert d.networks == ["ssrf-net"]


def test_empty_networks_emits_no_networks_key() -> None:
    svc = descriptor_to_compose_service(_desc("plain"))
    assert "networks" not in svc, "empty networks must stay byte-identical to single-net output"


def test_networks_emitted_as_compose_mapping() -> None:
    svc = descriptor_to_compose_service(_desc("caged", networks=["ssrf-net"]))
    assert svc["networks"] == {"ssrf-net": None}


# ---- top-level internal network ----


def test_internal_network_declared_with_internal_true() -> None:
    compose = render_compose([_desc("caged", networks=["ssrf-net"], profiles=["rag"])])
    nets = compose["networks"]
    assert nets["default"]["name"] == "agmind"  # unchanged
    assert nets["ssrf-net"]["internal"] is True
    assert nets["ssrf-net"]["name"] == "agmind_ssrf-net"


def test_internal_network_survives_project_namespacing() -> None:
    """Scenario/CI stacks render with a non-default project_name (`agmind render scenario
    core-rag` → project `agmind-core-rag`, ssrf-proxy + dify-sandbox in the closure). The
    cage MUST stay `internal: true` on that namespaced path too — otherwise a renderer
    refactor could silently un-cage scenario stacks while the default-render test stays
    green (security audit 2026-06-04)."""
    compose = render_compose(
        [_desc("caged", networks=["ssrf-net"], profiles=["rag"])], project_name="scn7"
    )
    nets = compose["networks"]
    assert nets["default"]["name"] == "scn7"
    assert nets["ssrf-net"] == {"name": "scn7_ssrf-net", "driver": "bridge", "internal": True}
    assert compose["services"]["caged"]["networks"] == {"ssrf-net": None}


def test_real_catalog_only_tiered_services_have_networks() -> None:
    # Only the ssrf cage (dify-sandbox/ssrf-proxy) and the data-net tier (etcd/milvus-minio
    # caged + milvus dual-homed — live-audit 2026-06-05) declare explicit networks; every other
    # service stays on the implicit default net (no networks key).
    _TIERED = {
        # ssrf cage
        "dify-sandbox",
        "ssrf-proxy",
        # data-net caged datastores
        "etcd",
        "milvus-minio",
        "mysql",
        "elasticsearch",
        "postgres",
        "redis",
        "qdrant",
        # data-net dual-homed consumers
        "milvus",
        "ragflow",
        "dify-api",
        "dify-worker",
        "dify-plugin-daemon",
        "postgres-exporter",
        "redis-exporter",
        "authelia",
    }
    rendered = render_to_string(profiles=["full"], domain="ci.example.com")
    doc = yaml.safe_load(rendered)
    for name, svc in doc["services"].items():
        if name in _TIERED:
            continue
        assert "networks" not in svc, f"{name} should not declare networks"


# ---- dify-sandbox cage + ssrf-proxy ----


def test_dify_sandbox_is_caged_on_ssrf_net_only() -> None:
    descriptors = load_descriptors()
    sandbox = descriptors["dify-sandbox"]
    assert sandbox.networks == ["ssrf-net"], "sandbox must be pinned to the internal net only"
    assert "ssrf-proxy" in sandbox.depends_on
    proxy_env = sandbox.env
    assert proxy_env.get("HTTP_PROXY") == "http://ssrf-proxy:3128"
    assert proxy_env.get("HTTPS_PROXY") == "http://ssrf-proxy:3128"


def test_ssrf_proxy_descriptor_dual_homed_no_healthcheck() -> None:
    descriptors = load_descriptors()
    assert "ssrf-proxy" in descriptors
    proxy = descriptors["ssrf-proxy"]
    assert set(proxy.networks) == {"ssrf-net", "default"}, "proxy bridges internal + default"
    # distroless squid → no usable shell → no healthcheck (dependents use service_started)
    assert proxy.health is None


def test_stage_squid_config_writes_readonly_config(tmp_path) -> None:
    from agmind.install.steps import _stage_squid_config

    src = tmp_path / "squid.conf"
    src.write_text("http_port 3128\n", encoding="utf-8")
    target = tmp_path / "etc" / "ssrf-proxy"

    _stage_squid_config(src, target)

    assert (target / "squid.conf").read_text(encoding="utf-8") == "http_port 3128\n"
