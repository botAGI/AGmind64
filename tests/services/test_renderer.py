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
    select_services,
    to_yaml,
)

pytestmark = pytest.mark.backend_any

VALID_SHA256 = "a" * 64


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
    # redis joins rag/ragflow/security/observability (B7: exporter co-deploy).
    assert sel["redis"].profiles == ["rag", "ragflow", "security", "observability"]


def test_select_services_rejects_unknown_explicit_service_names() -> None:
    descriptors = {"qdrant": _minimal_descriptor()}

    with pytest.raises(ValueError, match="Unknown services requested: missing-service"):
        select_services(descriptors, services=["qdrant", "missing-service"])


def test_traefik_compose_mounts_cloudflare_token_file() -> None:
    rendered = render_to_string(services=["traefik"], domain="lab.example.com")
    compose = yaml.safe_load(rendered)
    volumes = compose["services"]["traefik"]["volumes"]

    assert "/var/lib/agmind/secrets/cf_dns_api_token:/run/secrets/cf_dns_api_token:ro" in volumes


def test_rendered_compose_has_no_unguarded_interpolation() -> None:
    """Raw `${VAR}` makes Docker Compose substitute blanks in production validation."""
    rendered = render_to_string(profiles=["full"], domain="ci.example.com")

    assert re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", rendered) == []


@pytest.mark.parametrize(
    "domain",
    [
        "bad domain.example",
        "bad`domain.example",
        "bad\n.example",
        "evil.${VAR}.example",
        "https://lab.example.com",
        "*.example.com",
    ],
)
def test_render_to_string_rejects_invalid_domain(domain: str) -> None:
    with pytest.raises(ValueError, match="domain"):
        render_to_string(services=["traefik"], domain=domain)


def test_render_to_string_normalizes_domain() -> None:
    rendered = render_to_string(services=["traefik"], domain="Lab.Example.COM.")

    assert "lab.example.com" in rendered
    assert "Lab.Example.COM" not in rendered


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
    # single-(default-)network routed service: no ambiguity → no traefik.docker.network
    assert "traefik.docker.network" not in labels


def test_traefik_docker_network_pinned_for_multihomed_routed_service() -> None:
    """AUTH-1: a routed service on >1 network must pin traefik.docker.network to the shared edge, else
    Traefik's docker provider may dial an internal data/mgmt net it can't reach → the loadbalancer has
    no usable server → 503. For Authelia that 503'd forward-auth → every gated app 302'd to a dead
    portal (whole-stack outage). live-audit 2026-06-08."""
    multi = _minimal_descriptor(
        routing={"host": "authelia.agmind.dev", "middleware_chain": "chain-internal"},
        networks=["default", "data-net"],
    )
    assert render_traefik_labels(multi)["traefik.docker.network"] == "agmind"  # default edge name
    # a namespaced render threads its network name through
    assert render_traefik_labels(multi, network_name="proj")["traefik.docker.network"] == "proj"


def test_traefik_labels_routing_port_override() -> None:
    """routing.port decouples the Traefik upstream port from the published host port.

    Live-audit 2026-06-05 (HIGH ragflow-edge-route-wrong-port): RAGFlow publishes only the
    API port 9380 but its web UI is on container port 80 (nginx). The edge router must target
    80; without a port override _first_container_port picks 9380 and the UI 404s at the edge.
    """
    d = _minimal_descriptor(
        routing={"host": "rag.agmind.dev", "middleware_chain": "chain-llm", "port": 80},
    )
    labels = render_traefik_labels(d)
    # overrides _first_container_port (which would yield 6333 from the default ports[])
    assert labels["traefik.http.services.qdrant.loadbalancer.server.port"] == "80"


def test_traefik_labels_path_prefixes_and_priority() -> None:
    """Multi-component-on-one-host (Dify): path_prefixes scope the router rule to specific
    paths and priority ranks it above the host-only catch-all sibling."""
    d = _minimal_descriptor(
        name="dify-api",
        routing={
            "host": "dify.agmind.dev",
            "middleware_chain": "chain-llm",
            "path_prefixes": ["/console/api", "/api", "/v1"],
            "priority": 100,
        },
    )
    labels = render_traefik_labels(d)
    assert labels["traefik.http.routers.dify-api.rule"] == (
        "Host(`dify.agmind.dev`) && "
        "(PathPrefix(`/console/api`) || PathPrefix(`/api`) || PathPrefix(`/v1`))"
    )
    assert labels["traefik.http.routers.dify-api.priority"] == "100"


def test_traefik_labels_no_priority_label_when_zero() -> None:
    """priority=0 (default) emits NO priority label and a plain Host-only rule — every
    existing host-only service's labels stay byte-identical (regression guard)."""
    d = _minimal_descriptor(routing={"host": "qdrant.agmind.dev"})
    labels = render_traefik_labels(d)
    assert not any(k.endswith(".priority") for k in labels)
    assert labels["traefik.http.routers.qdrant.rule"] == "Host(`qdrant.agmind.dev`)"


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


def test_compose_service_hardening_fields() -> None:
    """live-audit 2026-06-05 (HIGH schema-cannot-cap-drop-or-readonly-rootfs): the schema and
    renderer must express the container-hardening primitives cap_drop / read_only / pids_limit /
    no_new_privileges."""
    d = _minimal_descriptor(
        cap_drop=["ALL"],
        cap_add=["NET_BIND_SERVICE"],
        read_only=True,
        pids_limit=256,
        no_new_privileges=True,
    )
    svc = descriptor_to_compose_service(d)
    assert svc["cap_drop"] == ["ALL"]
    assert svc["cap_add"] == ["NET_BIND_SERVICE"]
    assert svc["read_only"] is True
    assert svc["pids_limit"] == 256
    assert svc["security_opt"] == ["no-new-privileges:true"]


def test_compose_service_no_new_privileges_merges_without_dup() -> None:
    d = _minimal_descriptor(
        security_opt=["seccomp=unconfined", "no-new-privileges:true"],
        no_new_privileges=True,
    )
    svc = descriptor_to_compose_service(d)
    assert svc["security_opt"] == ["seccomp=unconfined", "no-new-privileges:true"]


def test_compose_service_hardening_absent_by_default() -> None:
    """A default descriptor emits NO hardening keys → every existing service byte-identical."""
    svc = descriptor_to_compose_service(_minimal_descriptor())
    for key in ("cap_drop", "read_only", "pids_limit"):
        assert key not in svc


def test_compose_service_renders_mem_reservation() -> None:
    """OPT-1: the soft scheduling floor is emitted as compose `mem_reservation:` alongside
    the hard `mem_limit:` cap."""
    d = _minimal_descriptor(resources={"cpus": 2.0, "mem_limit": "4g", "mem_reservation": "1g"})
    svc = descriptor_to_compose_service(d)
    assert svc["mem_limit"] == "4g"
    assert svc["mem_reservation"] == "1g"


def test_compose_service_mem_reservation_absent_by_default() -> None:
    """A descriptor without mem_reservation emits NO mem_reservation key (byte-identical render)."""
    d = _minimal_descriptor(resources={"cpus": 2.0, "mem_limit": "4g"})
    svc = descriptor_to_compose_service(d)
    assert "mem_reservation" not in svc


def test_compose_service_cgroupns_and_privileged() -> None:
    """grafana-dashboards.md CRITICAL-1: cAdvisor needs the host cgroup namespace
    (compose top-level `cgroup:`) under cgroup v2 to walk every cgroup and emit
    per-container metrics. privileged is the last-resort escalation."""
    d = _minimal_descriptor(cgroupns_mode="host", privileged=True)
    svc = descriptor_to_compose_service(d)
    assert svc["cgroup"] == "host"
    assert svc["privileged"] is True


def test_compose_service_cgroupns_privileged_absent_by_default() -> None:
    """Default descriptor emits neither key → every other service byte-identical."""
    svc = descriptor_to_compose_service(_minimal_descriptor())
    assert "cgroup" not in svc
    assert "privileged" not in svc


def test_cgroupns_mode_rejects_invalid_value() -> None:
    import pytest

    with pytest.raises(ValueError, match="cgroupns_mode"):
        _minimal_descriptor(cgroupns_mode="bogus")


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
    d = _minimal_descriptor(digest=VALID_SHA256)
    svc = descriptor_to_compose_service(d)
    assert svc["image"] == f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}"


def test_fq_image_drops_illegal_tag_when_digest_pins_it() -> None:
    """A descriptor whose tag contains a char illegal in a docker reference (e.g. '+',
    as grafana's '13.0.1+security-01' version label) must still render a VALID image
    reference. The digest is authoritative, so pin by digest only (repo@sha256:<d>);
    rendering the docker-invalid name:tag@sha256:<d> fails `docker compose up` with
    'invalid reference format'."""
    d = _minimal_descriptor(image="grafana/grafana:13.0.1+security-01", digest=VALID_SHA256)
    ref = d.fq_image()
    assert ref == f"grafana/grafana@sha256:{VALID_SHA256}"
    assert "+" not in ref
    assert ":13.0.1" not in ref


def test_fq_image_keeps_valid_tag_with_digest() -> None:
    """Regression: a legal tag is preserved in name:tag@digest form."""
    d = _minimal_descriptor(image="qdrant/qdrant:v1.18.0", digest=VALID_SHA256)
    assert d.fq_image() == f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}"


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
    """Loaded all + filtered core+security + rendered → valid compose dict.

    security rides along: rendering core (traefik + chain-llm services) with traefik
    labels REQUIRES authelia since the P0.3 topology gate (15-04) — core alone with
    traefik enabled now fail-closes by design.
    """
    descriptors = load_descriptors()
    core = filter_by_profile(descriptors, ["core", "security"])
    compose = render_compose(list(core.values()))

    # Modern compose-spec без version field (2026, see ADR-0006 fact-check fix)
    assert "version" not in compose
    assert "services" in compose
    assert "llama-llm" in compose["services"]
    assert compose["networks"]["default"]["name"] == "agmind"


def test_render_compose_waits_for_healthy_runtime_dependencies() -> None:
    descriptors = load_descriptors()
    # C2: dify-api consumes vector_db — must include a provider (qdrant) so
    # _check_unresolved_consumes does not raise. The test focus is depends_on
    # rendering, not capability resolution.
    selected = [
        descriptors["dify-api"],
        descriptors["postgres"],
        descriptors["redis"],
        descriptors["qdrant"],  # provides vector_db for dify-api
    ]

    compose = render_compose(selected, traefik_enabled=False)

    assert compose["services"]["dify-api"]["depends_on"] == {
        "postgres": {
            "condition": "service_healthy",
            "restart": True,
        },
        "redis": {
            "condition": "service_healthy",
            "restart": True,
        },
    }


def test_render_compose_keeps_started_condition_for_dependencies_without_healthcheck() -> None:
    # render_depends_on: a dependency WITHOUT a healthcheck → service_started; one WITH a
    # healthcheck → service_healthy. Synthetic descriptors so the assertion is decoupled from
    # which catalog services happen to ship a healthcheck (live-audit 2026-06-05 added several).
    dep_no_hc = _minimal_descriptor(name="dep-no-hc", ports=[])
    dep_hc = _minimal_descriptor(name="dep-hc", ports=[], health={"test": ["CMD", "true"]})
    consumer = _minimal_descriptor(name="consumer-x", ports=[], depends_on=["dep-no-hc", "dep-hc"])

    compose = render_compose([consumer, dep_no_hc, dep_hc], traefik_enabled=False)

    deps = compose["services"]["consumer-x"]["depends_on"]
    assert deps["dep-no-hc"] == {"condition": "service_started", "restart": True}
    assert deps["dep-hc"]["condition"] == "service_healthy"  # has a healthcheck → wait for healthy


def test_render_compose_waits_for_mysql_before_ragflow() -> None:
    descriptors = load_descriptors()
    selected = [
        descriptors["ragflow"],
        descriptors["mysql"],
        descriptors["elasticsearch"],
        descriptors["minio"],
        descriptors["redis"],
    ]

    compose = render_compose(selected, traefik_enabled=False)

    assert compose["services"]["mysql"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        # db-secrets→FILE: root password read from the mounted secret file, not env.
        'PW="$$(cat /run/secrets/mysql_root_password)"; '
        'mysqladmin ping -h 127.0.0.1 -uroot -p"$$PW" --silent && '
        'mysql -h 127.0.0.1 -uroot -p"$$PW" -e "SELECT 1" rag_flow',
    ]
    assert compose["services"]["ragflow"]["depends_on"]["mysql"]["condition"] == ("service_healthy")
    assert compose["services"]["minio"]["healthcheck"]["test"] == [
        "CMD",
        "mc",
        "ready",
        "local",
    ]
    assert compose["services"]["ragflow"]["depends_on"]["minio"]["condition"] == ("service_healthy")


def test_render_compose_rejects_traversal_project_name() -> None:
    """Review MEDIUM render-project-unvalidated-traversal: a path-like project namespace must
    be rejected before it reaches data_root=/var/lib/<project> or the compose identifiers."""
    import pytest

    descriptors = load_descriptors()
    # security included: the success renders below hit the P0.3 authelia gate otherwise.
    core = list(filter_by_profile(descriptors, ["core", "security"]).values())
    for bad in ("../../tmp/evil", "a/b", "Evil", "has space"):
        with pytest.raises(ValueError, match="project name"):
            render_compose(core, project_name=bad)
    # defaults + real scenario namespaces still render
    render_compose(core, project_name="agmind")
    render_compose(core, project_name="agmind-prod-min")


def test_render_compose_rejects_traversal_in_data_root() -> None:
    import pytest

    descriptors = load_descriptors()
    core = list(filter_by_profile(descriptors, ["core"]).values())
    with pytest.raises(ValueError, match="traversal"):
        render_compose(core, data_root="/var/lib/../../etc")


def test_render_compose_raises_on_dangling_depends_on() -> None:
    """Review LOW render-compose-no-depends-guard: a direct partial render must not emit a
    dangling depends_on that Compose hard-errors on — render_compose fails closed."""
    import pytest

    descriptors = load_descriptors()
    with pytest.raises(ValueError, match="Missing dependencies in render selection"):
        render_compose([descriptors["dify-web"]], traefik_enabled=False)


def test_render_compose_services_sorted_deterministic() -> None:
    """Service order детерминирован (по имени) — критично для diff."""
    descriptors = load_descriptors()
    core = filter_by_profile(descriptors, ["core", "security"])
    compose1 = render_compose(list(core.values()))
    compose2 = render_compose(list(core.values()))
    assert list(compose1["services"].keys()) == list(compose2["services"].keys())
    assert list(compose1["services"].keys()) == sorted(compose1["services"].keys())


def test_render_to_string_produces_valid_yaml() -> None:
    out = render_to_string(["core", "security"])
    assert "# Auto-generated by `agmind render compose`" in out
    parsed = yaml.safe_load(out)
    assert "services" in parsed
    # `version:` removed — modern compose-spec (см. ADR-0006 fact-check fix 2026-05-19)
    assert "version" not in parsed
    assert "networks" in parsed


def test_render_to_string_empty_profile_raises() -> None:
    with pytest.raises(ValueError, match="Unknown profiles requested: nonexistent-profile-xyz"):
        render_to_string(["nonexistent-profile-xyz"])


def test_render_to_string_rejects_unknown_profile_names() -> None:
    with pytest.raises(ValueError, match="Unknown profiles requested: missing-profile"):
        render_to_string(["core", "missing-profile"])


def test_render_to_string_rejects_unknown_explicit_service_names() -> None:
    with pytest.raises(ValueError, match="Unknown services requested: missing-service"):
        render_to_string(services=["traefik", "missing-service"])


def test_render_to_string_rejects_explicit_services_with_missing_dependencies() -> None:
    with pytest.raises(ValueError, match="Missing dependencies for selected services"):
        render_to_string(services=["dify-api"])


def test_render_to_string_explicit_services_ignore_unused_unknown_profiles() -> None:
    rendered = render_to_string(
        profiles=["missing-profile"],
        services=["traefik"],
        domain="lab.example.com",
    )
    parsed = yaml.safe_load(rendered)

    assert set(parsed["services"]) == {"traefik"}
    assert "missing-profile" not in rendered


# ---------- traefik_enabled selection-derive (P0.3 / 15-04, ratified local-default) ----------


def _traefik_labels_of(parsed: dict, service: str) -> list[str]:
    labels = parsed["services"][service].get("labels", {})
    keys = labels.keys() if isinstance(labels, dict) else labels
    return [k for k in keys if str(k).startswith("traefik.")]


def test_render_to_string_no_traefik_in_selection_derives_local() -> None:
    """Ratified 2026-07-17: default install is LOCAL. Without traefik in the selection,
    render_to_string must not emit traefik labels (traefik_enabled derives to False)."""
    rendered = render_to_string(services=["llama-llm"], domain="lab.example.com")
    parsed = yaml.safe_load(rendered)
    assert _traefik_labels_of(parsed, "llama-llm") == []


def test_render_to_string_traefik_in_selection_derives_public() -> None:
    """traefik selected → traefik_enabled derives to True → routed services carry labels.
    authelia+redis included: chain-llm routes require authelia (P0.3 topology gate)."""
    rendered = render_to_string(
        services=["traefik", "llama-llm", "authelia", "redis"],
        domain="lab.example.com",
    )
    parsed = yaml.safe_load(rendered)
    assert _traefik_labels_of(parsed, "llama-llm")


def test_render_to_string_explicit_false_wins_over_traefik_selection() -> None:
    """An explicit traefik_enabled always beats the selection-derive sentinel."""
    rendered = render_to_string(
        services=["traefik", "llama-llm", "authelia", "redis"],
        domain="lab.example.com",
        traefik_enabled=False,
    )
    parsed = yaml.safe_load(rendered)
    assert _traefik_labels_of(parsed, "llama-llm") == []


def test_render_to_string_explicit_true_without_traefik_service() -> None:
    """render_cmd contract: explicit --traefik keeps labels even if traefik itself is not
    selected (authelia is chain-public, so the P0.3 gate does not apply to it)."""
    rendered = render_to_string(
        services=["authelia", "redis"],
        domain="lab.example.com",
        traefik_enabled=True,
    )
    parsed = yaml.safe_load(rendered)
    assert _traefik_labels_of(parsed, "authelia")


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


# ---------- C2: fail-closed on unresolved non-optional consumes ----------

# A semver-pinned image for synthetic test descriptors (passes the pinned-image validator).
_TEST_IMAGE = "test/scratch:1.0.0"


def test_render_compose_raises_on_unresolved_non_optional_consumes_controlled() -> None:
    """C2 RED (controlled mutation): render_compose raises ValueError when a selected
    set contains a consumer whose non-optional, non-cross-profile capability has no
    provider.

    Uses a synthetic selected set (bypasses load_descriptors) so the test is fully
    isolated and deterministic. The precondition—no vector_db provider present—is
    checked explicitly to distinguish a test-setup bug from a real failure.
    """
    consumer = ServiceDescriptor.model_validate(
        {
            "name": "needs-vector",
            "image": _TEST_IMAGE,
            "tier": "storage",
            "purpose": "Test",
            "profiles": ["core"],
            "consumes": ["vector_db"],
        }
    )
    # Precondition: no vector_db provider in the one-element list.
    from agmind.services.compatibility import resolve_capability_provider_for_consumer

    selected = {"needs-vector": consumer}
    provider = resolve_capability_provider_for_consumer(selected, "vector_db", "needs-vector")
    assert provider is None, "Precondition: test fixture must have no vector_db provider"

    # After C2 is implemented this must raise; currently it DOES NOT (RED gate).
    with pytest.raises(ValueError, match="needs-vector.*vector_db|vector_db.*needs-vector"):
        render_compose([consumer])


def test_render_to_string_rag_profile_still_renders_cross_profile_consumes() -> None:
    """C2 regression: render_to_string(profiles=['rag']) must NOT raise even though
    dify-api consumes 'llm_inference' (provided by llama-llm in 'core').
    The pair ('dify-api', 'llm_inference') is in KNOWN_CROSS_PROFILE_CONSUMES — excluded.
    """
    # Should not raise
    result = render_to_string(profiles=["rag"])
    assert "dify-api" in result


def test_render_compose_optional_consumes_does_not_raise() -> None:
    """C2 regression: a consumer with only OPTIONAL_MISSING_CAPABILITIES unresolved
    (dify_external_kb, reranker) must still render without raising.
    """
    consumer = ServiceDescriptor.model_validate(
        {
            "name": "optional-consumer",
            "image": _TEST_IMAGE,
            "tier": "storage",
            "purpose": "Test",
            "profiles": ["core"],
            "consumes": ["dify_external_kb", "reranker"],
        }
    )
    # Both capabilities are OPTIONAL_MISSING_CAPABILITIES — must NOT raise.
    result = render_compose([consumer])
    assert "optional-consumer" in result["services"]


# Known render_to_string failures to exempt from the all-profiles smoke. EMPTY:
# nginx and the core-nginx profile were removed in phase 08 (no templates/nginx/ conf.d,
# same defect class as caddy). Every remaining selectable profile renders — add an entry
# here ONLY for a genuinely deferred, documented render failure.
_PREEXISTING_RENDER_FAILURES: frozenset[tuple[str, ...]] = frozenset()


def test_render_to_string_all_profile_sets_still_render() -> None:
    """Every profile in ALL_PROFILE_SETS renders without raising — including the C2
    fail-closed check across all 11 remaining lanes.

    There are currently NO exempted profiles: _PREEXISTING_RENDER_FAILURES is empty,
    so a regression that breaks any profile's render fails this gate immediately.
    """
    from agmind.services.profile_sets import ALL_PROFILE_SETS

    errors = []
    for profile_set in ALL_PROFILE_SETS:
        if profile_set in _PREEXISTING_RENDER_FAILURES:
            continue  # skip known deferred failures (currently none)
        try:
            # traefik_enabled=False mirrors the 14 CI isolation lanes
            # (compose_profile_check): this gate asserts per-profile render mechanics,
            # not routing topology — the P0.3 authelia gate has its own tests.
            render_to_string(profiles=list(profile_set), traefik_enabled=False)
        except ValueError as e:
            errors.append(f"  profile={profile_set}: {e}")

    assert not errors, "Profiles raised unexpectedly:\n" + "\n".join(errors)


# ---------- C4: env precedence-shadow guard ----------


def test_no_descriptor_shadows_bindings_injected_key() -> None:
    """C4: No descriptor may hardcode a key that BINDINGS would inject for it
    (given its 'consumes'), except via an explicit reviewed allowlist.

    The merge order is ``{**extra, **descriptor.env}`` (renderer.py:410) so
    descriptor.env wins over injection.  A hardcoded key that DIFFERS from the
    injected value silently ships a mis-configured container.

    SHADOW_ALLOWLIST: entries where a descriptor legitimately mirrors the
    injected value (value-identical, currently harmless) — forward guard against
    future divergence from a NEW provider entry.

    The two former "harmless mirrors" (ragflow VLM_ENDPOINT, openwebui OPENAI_API_BASE_URL)
    were REMOVED from the descriptors — live-audit 2026-06-05 (HIGH dangling-llama-llm) proved
    they are NOT harmless: with model_id=skip there is no llama-llm, the binding correctly omits
    the endpoint, but the static mirror dangled at a dead DNS name. The allowlist is now empty,
    so re-adding any such hardcode fails this guard.
    """
    from agmind.services.capability_bindings import BINDINGS
    from agmind.services.renderer import load_descriptors

    # No descriptor may shadow a BINDINGS-injected key (the llm_inference mirrors were removed).
    # Format: {(descriptor_name, env_key): injected_value}
    SHADOW_ALLOWLIST: dict[tuple[str, str], str] = {}

    descriptors = load_descriptors()

    # Build the set of keys that BINDINGS would inject for each consuming descriptor.
    # For a given (consumer, cap) we collect all keys that ANY provider would inject.
    def _injected_keys_for_consumer(consumer_name: str, consumes: list[str]) -> set[str]:
        keys: set[str] = set()
        for cap in consumes:
            cap_table = BINDINGS.get(cap, {})
            for provider_table in cap_table.values():
                consumer_table = provider_table.get(consumer_name, {})
                keys.update(consumer_table.keys())
        return keys

    violations: list[str] = []
    for name, d in sorted(descriptors.items()):
        if not d.consumes:
            continue
        injected_keys = _injected_keys_for_consumer(name, d.consumes)
        for key in sorted(injected_keys):
            if key not in d.env:
                continue  # not hardcoded → no shadow risk
            hardcoded_val = d.env[key]
            allowed_val = SHADOW_ALLOWLIST.get((name, key))
            if allowed_val is not None and hardcoded_val == allowed_val:
                continue  # value-identical mirror in allowlist → harmless
            violations.append(
                f"  {name}: env[{key!r}]={hardcoded_val!r} shadows BINDINGS injection "
                f"(allowlist={allowed_val!r})"
            )

    assert not violations, (
        "Descriptor env keys shadow BINDINGS-injected keys outside the reviewed allowlist.\n"
        "Either remove the hardcoded key (let injection handle it) or add to SHADOW_ALLOWLIST "
        "with a comment explaining why the value-identical mirror is intentional.\n"
        "Violations:\n" + "\n".join(violations)
    )
