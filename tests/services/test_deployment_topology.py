from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agmind.services.deployment_topology import (
    build_deployment_topology_report,
    build_deployment_topology_report_for_services,
)
from agmind.services.renderer import load_descriptors, select_services
from agmind.services.topology_checks import (
    DEFAULT_TOPOLOGY_PROFILE_SETS,
    format_topology_check_report,
    validate_topology_profiles,
)

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_deployment_topology_report_for_services_rejects_unknown_service() -> None:
    with pytest.raises(ValueError, match="Unknown services requested: missing-service"):
        build_deployment_topology_report_for_services(("dify-api", "missing-service"))


def test_deployment_topology_report_combines_retrieval_and_compatibility_warnings() -> None:
    descriptors = load_descriptors()
    selected = select_services(
        descriptors,
        services=[
            "dify-api",
            "llama-llm",
            "llama-embed",
            "milvus",
            "postgres",
            "qdrant",
            "redis",
        ],
    )

    report = build_deployment_topology_report(selected, all_descriptors=descriptors)

    assert "DIFY VECTOR DB ..... milvus (ambiguous: qdrant also selected)" in report.retrieval_lines
    assert report.dependency_warnings == ()
    assert any(
        "Dify has multiple vector_db providers selected" in warning
        for warning in report.compatibility_warnings
    )
    assert "RAG STORAGE PLAN .." in report.block_lines()
    assert "TOPOLOGY WARNINGS ." in report.block_lines()


def test_deployment_topology_report_records_missing_runtime_dependencies() -> None:
    descriptors = load_descriptors()
    selected = select_services(descriptors, services=["ragflow"])

    report = build_deployment_topology_report(selected, all_descriptors=descriptors)

    assert any("ragflow needs elasticsearch" in warning for warning in report.dependency_warnings)
    assert any("ragflow needs mysql" in warning for warning in report.dependency_warnings)
    assert "TOPOLOGY WARNINGS ." in report.block_lines()


def test_deployment_topology_profile_core_rag_has_no_optional_kb_warning() -> None:
    descriptors = load_descriptors()
    selected = select_services(descriptors, profiles=["core", "rag"])

    report = build_deployment_topology_report(selected, all_descriptors=descriptors)

    assert report.warning_count == 0
    assert report.compatibility_warnings == ()


def test_deployment_topology_profile_core_rag_keeps_optional_kb_as_info() -> None:
    descriptors = load_descriptors()
    selected = select_services(descriptors, profiles=["core", "rag"])

    report = build_deployment_topology_report(selected, all_descriptors=descriptors)

    assert report.info_count == 1
    assert report.warning_count == 0
    assert report.compatibility_infos == (
        "Сервис(ы) dify-api requires 'dify_external_kb', но ни один selected сервис не provides его.",
    )
    payload = report.to_payload()
    assert payload["info_count"] == 1
    assert payload["expected_info_count"] == 1
    assert payload["unexpected_info_count"] == 0
    assert payload["infos"][0]["severity"] == "info"
    assert payload["infos"][0]["kind"] == "optional_missing_capability"
    assert payload["infos"][0]["expected"] is True
    assert payload["has_warnings"] is False


def test_deployment_topology_report_payload_has_stable_warning_counts() -> None:
    descriptors = load_descriptors()
    selected = select_services(
        descriptors,
        services=[
            "dify-api",
            "elasticsearch",
            "llama-embed",
            "llama-llm",
            "milvus",
            "minio",
            "mysql",
            "postgres",
            "qdrant",
            "ragflow",
            "redis",
        ],
    )

    report = build_deployment_topology_report(selected, all_descriptors=descriptors)
    payload = report.to_payload()

    assert payload["has_warnings"] is True
    assert payload["warning_count"] == 2
    assert payload["dependency_warning_count"] == 0
    assert payload["compatibility_warning_count"] == 2
    assert payload["retrieval"]["dify_vector_provider"] == "milvus"
    assert payload["retrieval"]["dify_vector_providers"] == ["milvus", "qdrant"]
    assert payload["warnings"][0]["source"] == "compatibility"
    assert payload["warnings"][0]["severity"] == "warning"


def test_topology_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "topology_check.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "topology OK" in result.stdout
    assert "profile sets" in result.stdout


def test_validate_topology_profiles_reports_standard_lanes_clean() -> None:
    report = validate_topology_profiles()

    assert report.ok is True
    assert tuple(item.profiles for item in report.profiles) == DEFAULT_TOPOLOGY_PROFILE_SETS
    assert all(item.warning_count == 0 for item in report.profiles)
    assert report.warning_count == 0
    assert report.info_count == 1
    assert report.expected_info_count == 1
    assert report.unexpected_info_count == 0
    assert report.to_json()["info_count"] == 1
    assert report.to_json()["expected_info_count"] == 1
    assert report.to_json()["unexpected_info_count"] == 0
    assert any(item.info_count == 1 for item in report.profiles if item.profiles == ("core", "rag"))
    assert any(
        item.expected_info_count == 1 and item.unexpected_info_count == 0
        for item in report.profiles
        if item.profiles == ("core", "rag")
    )
    text = format_topology_check_report(report)
    assert "core,rag: OK (13 services, warnings=0, info=1, expected_info=1)" in text
    assert "topology OK: 5 profile sets" in text


def test_validate_topology_profiles_reports_ambiguous_manual_profile_set() -> None:
    report = validate_topology_profiles(profile_sets=(("rag", "rag-milvus"),))

    assert report.ok is False
    assert report.profiles[0].profiles == ("rag", "rag-milvus")
    assert report.profiles[0].warning_count > 0
    assert any(
        "Dify has multiple vector_db providers selected" in warning.message
        for warning in report.profiles[0].warnings
    )
    assert "topology FAILED" in format_topology_check_report(report)


def test_validate_topology_profiles_rejects_unknown_profile() -> None:
    report = validate_topology_profiles(profile_sets=(("core", "missing-profile"),))

    assert report.ok is False
    assert report.profiles[0].profiles == ("core", "missing-profile")
    assert report.profiles[0].errors == (
        "core,missing-profile: unknown profile(s): missing-profile",
    )
    assert "core,missing-profile: FAILED" in format_topology_check_report(report)
