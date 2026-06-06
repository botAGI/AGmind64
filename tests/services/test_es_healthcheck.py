"""Live-audit 2026-06-05 (MED ragflow-es-started-not-healthy): RAGFlow depends_on
elasticsearch, but ES had no healthcheck so render_depends_on fell back to service_started
— ragflow could boot before ES was ready. Giving ES a healthcheck auto-upgrades the
dependency gate to service_healthy (renderer.render_depends_on)."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors, render_depends_on

pytestmark = pytest.mark.backend_any


def test_elasticsearch_has_healthcheck() -> None:
    es = load_descriptors()["elasticsearch"]
    assert es.health is not None, "elasticsearch must declare a healthcheck"
    # probes the cluster health endpoint
    assert any("_cluster/health" in part for part in es.health.test)


def test_ragflow_waits_for_elasticsearch_healthy() -> None:
    descriptors = load_descriptors()
    deps = render_depends_on(descriptors["ragflow"], descriptors)
    assert deps["elasticsearch"]["condition"] == "service_healthy"


def test_elasticsearch_security_enabled_with_authenticated_probe() -> None:
    """live-audit 2026-06-05 (MED elasticsearch-xpack-disabled): ES must require basic auth,
    bootstrap the elastic password, and its healthcheck must AUTHENTICATE (a CMD probe without
    -u would 401 once security is on -> ES never healthy -> ragflow never starts)."""
    es = load_descriptors()["elasticsearch"]
    assert es.env.get("xpack.security.enabled") == "true"
    assert "ELASTIC_PASSWORD" in es.env
    assert es.health is not None and es.health.test[0] == "CMD-SHELL"
    assert any("ELASTIC_PASSWORD" in part for part in es.health.test)


def test_ragflow_authenticates_to_elasticsearch() -> None:
    """RAGFlow's service_conf reads ES_USER (default 'elastic') + ELASTIC_PASSWORD."""
    assert "ELASTIC_PASSWORD" in load_descriptors()["ragflow"].env


def test_elastic_password_is_generated_init_only_secret() -> None:
    from agmind.install.secret_keys import INIT_ONLY, RUNTIME_SECRET_KEYS, classify

    assert "ELASTIC_PASSWORD" in RUNTIME_SECRET_KEYS  # EnvWriteStep generates it
    assert "ELASTIC_PASSWORD" in INIT_ONLY  # ES bootstraps it on first empty-dir init only
    assert classify("ELASTIC_PASSWORD") == "init_only"
