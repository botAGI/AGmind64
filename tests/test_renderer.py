"""Phase H'.C: tests для agmind.services.renderer."""

from __future__ import annotations

import re

import pytest
import yaml

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import (
    DEFAULT_LOGGING,
    descriptor_to_compose_service,
    filter_by_profile,
    load_descriptors,
    render_compose,
    render_observability_labels,
    render_to_string,
    render_traefik_labels,
    to_yaml,
)

pytestmark = pytest.mark.backend_any


def _minimal_descriptor(**overrides: object) -> ServiceDescriptor:
    base: dict[str, object] = {
        "name": "qdrant",
        "image": "qdrant/qdrant:v1.18.0",
        "tier": "storage",
        "purpose": "Vector store",
        "ports": ["127.0.0.1:6333:6333"],
    }
    base.update(overrides)
    return ServiceDescriptor.model_validate(base)


# ---------- load_descriptors ----------


def test_load_descriptors_real_directory() -> None:
    """Все 32 файла загружаются без ошибок."""
    descriptors = load_descriptors()
    assert len(descriptors) >= 30
    assert "qdrant" in descriptors
    assert "llama-llm" in descriptors


def test_load_descriptors_returns_typed_objects() -> None:
    descriptors = load_descriptors()
    for d in descriptors.values():
        assert isinstance(d, ServiceDescriptor)
        assert isinstance(d.tier, str)


# ---------- filter_by_profile ----------


def test_filter_by_profile_core() -> None:
    descriptors = load_descriptors()
    core = filter_by_profile(descriptors, ["core"])
    assert "llama-llm" in core
    assert "qdrant" in core
    assert "ragflow" not in core  # ragflow profile only


def test_filter_by_profile_full_returns_all() -> None:
    descriptors = load_descriptors()
    full = filter_by_profile(descriptors, ["full"])
    assert len(full) == len(descriptors)


def test_filter_by_profile_empty_for_unknown() -> None:
    descriptors = load_descriptors()
    nothing = filter_by_profile(descriptors, ["nonexistent-profile"])
    assert nothing == {}


def test_filter_by_profile_multiple() -> None:
    descriptors = load_descriptors()
    sel = filter_by_profile(descriptors, ["core", "ragflow"])
    assert "qdrant" in sel
    assert "ragflow" in sel  # from ragflow profile
    assert "elasticsearch" in sel  # from ragflow profile


def test_filter_by_profile_ragflow_includes_redis_runtime_dependency() -> None:
    descriptors = load_descriptors()
    sel = filter_by_profile(descriptors, ["core", "ragflow"])

    assert "ragflow" in sel
    assert "redis" in sel
    assert sel["redis"].profiles == ["rag", "ragflow"]


def test_rendered_compose_has_no_unguarded_interpolation() -> None:
    """Raw `${VAR}` makes Docker Compose substitute blanks in production validation."""
    rendered = render_to_string(profiles=["full"], domain="ci.example.com")

    assert re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", rendered) == []


# ---------- render_traefik_labels ----------


def test_traefik_labels_empty_when_no_routing() -> None:
    d = _minimal_descriptor()
    assert render_traefik_labels(d) == {}


def test_traefik_labels_basic_routing() -> None:
    d = _minimal_descriptor(
        routing={
            "host": "qdrant.agmind.dev",
            "middleware_chain": "chain-internal",
        }
    )
    labels = render_traefik_labels(d)
    assert labels["traefik.enable"] == "true"
    assert labels["traefik.http.routers.qdrant.rule"] == "Host(`qdrant.agmind.dev`)"
    assert labels["traefik.http.routers.qdrant.middlewares"] == "chain-internal@file"
    assert labels["traefik.http.services.qdrant.loadbalancer.server.port"] == "6333"
    assert labels["traefik.http.services.qdrant.loadbalancer.healthcheck.path"] == "/health"


def test_traefik_labels_sse_safe() -> None:
    """SSE routing должен добавить flushinterval=1ms и no-http2."""
    d = _minimal_descriptor(
        name="llama-q4",
        routing={
            "host": "llama-q4.agmind.dev",
            "middleware_chain": "chain-llm",
            "sse": True,
        },
    )
    labels = render_traefik_labels(d)
    flush_key = "traefik.http.services.llama-q4.loadbalancer.responseforwarding.flushinterval"
    tls_opts_key = "traefik.http.routers.llama-q4.tls.options"
    assert labels[flush_key] == "1ms"
    assert labels[tls_opts_key] == "no-http2@file"


def test_traefik_labels_no_sse_means_no_flush() -> None:
    d = _minimal_descriptor(
        routing={"host": "x.agmind.dev", "middleware_chain": "chain-internal", "sse": False}
    )
    labels = render_traefik_labels(d)
    flush_keys = [k for k in labels if "flushinterval" in k]
    assert flush_keys == []


# ---------- render_observability_labels ----------


def test_observability_labels_default_loki_only() -> None:
    d = _minimal_descriptor()
    labels = render_observability_labels(d)
    # Default: loki_scrape=True, prometheus_scrape=False
    assert labels["loki.scrape"] == "true"
    assert labels["agmind.service"] == "qdrant"
    assert labels["agmind.tier"] == "storage"
    assert "prometheus.scrape" not in labels


def test_observability_labels_with_prometheus() -> None:
    d = _minimal_descriptor(
        observability={
            "prometheus_scrape": True,
            "metrics_path": "/metrics",
        }
    )
    labels = render_observability_labels(d)
    assert labels["prometheus.scrape"] == "true"
    assert labels["prometheus.path"] == "/metrics"
    assert labels["prometheus.port"] == "6333"  # из первого port


def test_observability_explicit_metrics_port() -> None:
    d = _minimal_descriptor(
        observability={
            "prometheus_scrape": True,
            "metrics_port": 9090,
        }
    )
    labels = render_observability_labels(d)
    assert labels["prometheus.port"] == "9090"


# ---------- descriptor_to_compose_service ----------


def test_compose_service_minimal() -> None:
    d = _minimal_descriptor()
    svc = descriptor_to_compose_service(d)
    assert svc["image"] == "qdrant/qdrant:v1.18.0"
    assert svc["container_name"] == "agmind-qdrant"
    assert svc["restart"] == "unless-stopped"
    assert svc["ports"] == ["127.0.0.1:6333:6333"]
    assert svc["logging"] == DEFAULT_LOGGING


def test_compose_service_with_digest_fq_image() -> None:
    d = _minimal_descriptor(digest="abc123def")
    svc = descriptor_to_compose_service(d)
    assert svc["image"] == "qdrant/qdrant:v1.18.0@sha256:abc123def"


def test_compose_service_healthcheck_format() -> None:
    d = _minimal_descriptor(
        health={
            "test": ["CMD", "curl", "-f", "http://localhost:6333/healthz"],
            "interval": "30s",
        }
    )
    svc = descriptor_to_compose_service(d)
    hc = svc["healthcheck"]
    assert hc["test"] == ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
    assert hc["interval"] == "30s"


def test_compose_service_includes_metadata_labels() -> None:
    d = _minimal_descriptor()
    svc = descriptor_to_compose_service(d)
    labels = svc["labels"]
    assert labels["agmind.service"] == "qdrant"
    assert labels["agmind.tier"] == "storage"
    assert labels["agmind.owner"] == "agmind-core"
    assert labels["loki.scrape"] == "true"


def test_compose_service_traefik_disabled_no_routing_labels() -> None:
    d = _minimal_descriptor(routing={"host": "qdrant.lan", "middleware_chain": "chain-internal"})
    svc = descriptor_to_compose_service(d, traefik_enabled=False)
    labels = svc["labels"]
    assert "traefik.enable" not in labels
    # observability labels still там
    assert labels["agmind.service"] == "qdrant"


def test_compose_service_logging_always_present() -> None:
    d = _minimal_descriptor()
    svc = descriptor_to_compose_service(d)
    assert svc["logging"] == DEFAULT_LOGGING
    # 50m × 3 = max ~150MB на сервис
    assert svc["logging"]["options"]["max-size"] == "50m"
    assert svc["logging"]["options"]["max-file"] == "3"


# ---------- render_compose end-to-end ----------


def test_render_compose_smoke_core_profile() -> None:
    """Loaded all + filtered core + rendered → valid compose dict."""
    descriptors = load_descriptors()
    core = filter_by_profile(descriptors, ["core"])
    compose = render_compose(list(core.values()))

    # Modern compose-spec без version field (2026, see ADR-0006 fact-check fix)
    assert "version" not in compose
    assert "services" in compose
    assert "llama-llm" in compose["services"]
    assert compose["networks"]["default"]["name"] == "agmind"


def test_render_compose_services_sorted_deterministic() -> None:
    """Service order детерминирован (по имени) — критично для diff."""
    descriptors = load_descriptors()
    core = filter_by_profile(descriptors, ["core"])
    compose1 = render_compose(list(core.values()))
    compose2 = render_compose(list(core.values()))
    assert list(compose1["services"].keys()) == list(compose2["services"].keys())
    assert list(compose1["services"].keys()) == sorted(compose1["services"].keys())


def test_render_to_string_produces_valid_yaml() -> None:
    out = render_to_string(["core"])
    assert "# Auto-generated by `agmind render compose`" in out
    parsed = yaml.safe_load(out)
    assert "services" in parsed
    # `version:` removed — modern compose-spec (см. ADR-0006 fact-check fix 2026-05-19)
    assert "version" not in parsed
    assert "networks" in parsed


def test_render_to_string_empty_profile_raises() -> None:
    with pytest.raises(ValueError, match="No services match"):
        render_to_string(["nonexistent-profile-xyz"])


def test_to_yaml_includes_header() -> None:
    compose = {"version": "3.9", "services": {}}
    out = to_yaml(compose)
    assert out.startswith("# Auto-generated")
    assert "agmind/services/renderer.py" in out


# ---------- All 32 services smoke test ----------


@pytest.mark.parametrize(
    "name",
    list(load_descriptors().keys()) or [pytest.param("none", marks=pytest.mark.skip)],
)
def test_each_service_renders_without_error(name: str) -> None:
    """Каждый из 32 service descriptors рендерится в compose dict без ошибок."""
    descriptors = load_descriptors()
    d = descriptors[name]
    svc = descriptor_to_compose_service(d)
    assert svc["container_name"] == f"agmind-{d.name}"
    assert "image" in svc
    # YAML round-trip — valid YAML structure
    yaml_text = yaml.safe_dump(svc)
    yaml.safe_load(yaml_text)  # must not raise


def test_full_profile_includes_all_services() -> None:
    descriptors = load_descriptors()
    full = filter_by_profile(descriptors, ["full"])
    assert len(full) == len(descriptors)
