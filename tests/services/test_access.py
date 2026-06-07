"""Tests for ``agmind.services.access`` — the per-service access report (url / login /
password / flags) derived from descriptors + the rendered ``.env``. This single in-memory
model backs the post-install summary, ``credentials.txt``, and the ``agmind endpoints`` /
``agmind creds show`` commands.

Also pins the declarative ``access:`` contract on the real web-UI descriptors.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agmind.schemas import AccessConfig, RoutingConfig, ServiceDescriptor
from agmind.services.access import AccessEntry, build_access_report
from agmind.services.renderer import DEFAULT_SERVICES_DIR, load_descriptors

pytestmark = pytest.mark.backend_any


def _svc(
    name: str,
    *,
    host: str | None = None,
    access: AccessConfig | None = None,
) -> ServiceDescriptor:
    return ServiceDescriptor(
        name=name,
        image=f"example/{name}:pinned",
        tier="app",
        routing=RoutingConfig(host=host) if host else None,
        access=access,
    )


def test_access_config_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AccessConfig(bogus=True)  # type: ignore[call-arg]


def test_access_config_defaults() -> None:
    a = AccessConfig()
    assert a.login is None
    assert a.password_env is None
    assert a.first_login_register is False
    assert a.lan_only is False
    assert a.api_kind is None


def test_builder_resolves_url_login_and_password() -> None:
    descriptors = {
        "grafana": _svc(
            "grafana",
            host="grafana.lab.test",
            access=AccessConfig(login="admin", password_env="GRAFANA_PASSWORD"),
        ),
    }
    report = build_access_report(descriptors, {"GRAFANA_PASSWORD": "s3cret-val"})
    assert len(report) == 1
    e = report[0]
    assert isinstance(e, AccessEntry)
    assert e.service == "grafana"
    assert e.url == "https://grafana.lab.test"
    assert e.login == "admin"
    assert e.password == "s3cret-val"
    assert e.password_env == "GRAFANA_PASSWORD"
    assert e.first_login_register is False
    assert e.api_kind is None


def test_builder_skips_internal_only_services() -> None:
    descriptors = {
        "qdrant": _svc("qdrant"),  # no routing → internal-only, not an access endpoint
        "grafana": _svc("grafana", host="g.test", access=AccessConfig(login="admin")),
    }
    report = build_access_report(descriptors, {})
    assert [e.service for e in report] == ["grafana"]


def test_builder_routing_only_service_is_url_only() -> None:
    descriptors = {"netdata": _svc("netdata", host="netdata.test")}
    report = build_access_report(descriptors, {})
    assert len(report) == 1
    e = report[0]
    assert e.url == "https://netdata.test"
    assert e.login is None
    assert e.password is None


def test_builder_password_unset_in_env_is_none() -> None:
    descriptors = {
        "grafana": _svc(
            "grafana", host="g.test", access=AccessConfig(login="admin", password_env="MISSING")
        ),
    }
    report = build_access_report(descriptors, {})
    assert report[0].password is None
    assert report[0].password_env == "MISSING"


def test_builder_model_endpoint_flag() -> None:
    descriptors = {
        "llama-llm": _svc("llama-llm", host="llama.test", access=AccessConfig(api_kind="openai")),
    }
    report = build_access_report(descriptors, {})
    assert report[0].api_kind == "openai"
    assert report[0].is_model_endpoint is True


def test_builder_substitutes_install_domain() -> None:
    descriptors = {"grafana": _svc("grafana", host="grafana.agmind.dev")}
    report = build_access_report(descriptors, {}, domain="lab.example.com")
    assert report[0].url == "https://grafana.lab.example.com"


def test_builder_keeps_host_when_domain_is_placeholder_or_none() -> None:
    descriptors = {"grafana": _svc("grafana", host="grafana.agmind.dev")}
    assert build_access_report(descriptors, {})[0].url == "https://grafana.agmind.dev"
    assert (
        build_access_report(descriptors, {}, domain="agmind.dev")[0].url
        == "https://grafana.agmind.dev"
    )


def test_builder_is_sorted_by_service_name() -> None:
    descriptors = {
        "zeta": _svc("zeta", host="z.test"),
        "alpha": _svc("alpha", host="a.test"),
    }
    report = build_access_report(descriptors, {})
    assert [e.service for e in report] == ["alpha", "zeta"]


# ---- contract: real descriptors carry the declared access metadata ----


def test_real_web_ui_descriptors_have_access() -> None:
    descriptors = load_descriptors(DEFAULT_SERVICES_DIR)

    grafana = descriptors["grafana"].access
    assert grafana is not None
    assert grafana.login == "admin"
    assert grafana.password_env == "GRAFANA_PASSWORD"

    owui = descriptors["openwebui"].access
    assert owui is not None
    assert owui.first_login_register is True

    for model_svc in ("llama-llm", "llama-embed", "llama-rerank"):
        acc = descriptors[model_svc].access
        assert acc is not None, model_svc
        assert acc.api_kind == "openai", model_svc


def test_model_endpoints_surface_served_model_name() -> None:
    """credentials.txt / creds show must print the real model id (the llama-server `/v1/models` value
    = the `--model` basename) so the operator pastes it straight into Dify, not '(your model file)'.
    Resolved from the descriptor command, honouring the rendered env (with ${VAR:-default} fallback)."""
    descriptors = load_descriptors(DEFAULT_SERVICES_DIR)

    # no env override → the descriptor's ${VAR:-default} default basename
    by_name = {e.service: e for e in build_access_report(descriptors, {})}
    assert by_name["llama-embed"].model_name == "bge-m3-Q8_0.gguf"
    assert by_name["llama-rerank"].model_name == "bge-reranker-v2-m3-Q8_0.gguf"
    # a UI service (no --model in its command) carries no model name
    assert by_name["grafana"].model_name is None

    # an env override flows through to the reported name
    overridden = {
        e.service: e for e in build_access_report(descriptors, {"AGMIND_EMBED_FILE": "x.gguf"})
    }
    assert overridden["llama-embed"].model_name == "x.gguf"


def test_access_note_surfaces_in_report_and_credentials() -> None:
    """A descriptor's access.note (e.g. portainer's admin-window restart recovery) must reach both the
    report entry and credentials.txt, so operators see recovery commands without having to ask."""
    from agmind.services.access import render_credentials_txt

    descriptors = load_descriptors(DEFAULT_SERVICES_DIR)
    report = build_access_report(descriptors, {})
    portainer = {e.service: e for e in report}["portainer"]
    assert portainer.note and "docker restart agmind-portainer" in portainer.note
    assert "docker restart agmind-portainer" in render_credentials_txt(report)
