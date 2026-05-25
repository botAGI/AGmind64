from __future__ import annotations

import pytest

from agmind.components.checks import check_deploy_conflicts
from agmind.services.renderer import load_descriptors, select_services

pytestmark = pytest.mark.backend_any


def test_traefik_and_caddy_report_host_port_conflicts() -> None:
    all_descriptors = load_descriptors()
    selected = select_services(all_descriptors, services=["traefik", "caddy"])

    report = check_deploy_conflicts(selected)

    issues = [issue for issue in report.issues if issue.kind == "host_port_conflict"]
    assert {issue.detail for issue in issues} == {"80", "443"}
    assert all(issue.severity == "error" for issue in issues)


def test_traefik_alone_has_no_deploy_conflicts() -> None:
    all_descriptors = load_descriptors()
    selected = select_services(all_descriptors, services=["traefik"])

    report = check_deploy_conflicts(selected)

    assert report.issues == ()


def test_vector_dbs_are_not_deploy_conflicts() -> None:
    all_descriptors = load_descriptors()
    selected = select_services(all_descriptors, services=["qdrant", "weaviate", "milvus"])

    report = check_deploy_conflicts(selected)

    assert all(issue.kind != "host_port_conflict" for issue in report.issues)
