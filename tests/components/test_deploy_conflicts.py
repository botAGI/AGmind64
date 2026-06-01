from __future__ import annotations

import pytest

from agmind.components.checks import check_deploy_conflicts
from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import load_descriptors, select_services

pytestmark = pytest.mark.backend_any


def _descriptor(name: str, *, ports: list[str]) -> ServiceDescriptor:
    return ServiceDescriptor(name=name, image=f"example/{name}:1.0", tier="storage", ports=ports)


def _host_port_conflicts(selected: dict[str, ServiceDescriptor]) -> list:
    report = check_deploy_conflicts(selected)
    return [issue for issue in report.issues if issue.kind == "host_port_conflict"]


def test_wildcard_bind_conflicts_with_specific_bind() -> None:
    selected = {
        "wildcard": _descriptor("wildcard", ports=["8080:80"]),
        "specific": _descriptor("specific", ports=["127.0.0.1:8080:80"]),
    }

    conflicts = _host_port_conflicts(selected)

    assert len(conflicts) == 1
    assert conflicts[0].services == ("specific", "wildcard")
    assert conflicts[0].detail == "8080"


def test_distinct_explicit_bind_ips_do_not_conflict() -> None:
    selected = {
        "loopback": _descriptor("loopback", ports=["127.0.0.1:8080:80"]),
        "lan": _descriptor("lan", ports=["192.168.1.10:8080:80"]),
    }

    assert _host_port_conflicts(selected) == []


def test_same_explicit_bind_ip_conflicts() -> None:
    selected = {
        "first": _descriptor("first", ports=["127.0.0.1:8080:80"]),
        "second": _descriptor("second", ports=["127.0.0.1:8080:80"]),
    }

    conflicts = _host_port_conflicts(selected)

    assert len(conflicts) == 1
    assert conflicts[0].services == ("first", "second")
    assert conflicts[0].detail == "8080"


def test_traefik_and_nginx_report_host_port_conflicts() -> None:
    all_descriptors = load_descriptors()
    selected = select_services(all_descriptors, services=["traefik", "nginx"])

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
