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
