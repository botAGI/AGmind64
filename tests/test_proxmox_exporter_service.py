from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from agmind.addons import load_tool_candidates
from agmind.components import load_component_contracts
from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import render_to_string

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = REPO_ROOT / "templates" / "services" / "proxmox-exporter.yaml"
PVE_EXAMPLE_PATH = (
    REPO_ROOT / "templates" / "observability" / "proxmox-exporter" / "pve.yml.example"
)
SCRAPE_EXAMPLE_PATH = (
    REPO_ROOT / "templates" / "observability" / "proxmox-exporter" / "prometheus-scrape.yml.example"
)

IMAGE = "prompve/prometheus-pve-exporter:3.9.0"
DIGEST = "sha256:78a58df7a31c9fbee94962cd06422672668529de6421892df9aed4b6cee0757f"


def _descriptor() -> ServiceDescriptor:
    assert SERVICE_PATH.exists(), "missing Proxmox exporter service descriptor"
    raw = yaml.safe_load(SERVICE_PATH.read_text(encoding="utf-8"))
    return ServiceDescriptor.model_validate(raw)


def test_proxmox_exporter_descriptor_validates_and_is_pinned() -> None:
    descriptor = _descriptor()

    assert descriptor.name == "proxmox-exporter"
    assert descriptor.image == IMAGE
    assert descriptor.digest == DIGEST
    assert descriptor.tier == "ops"
    assert descriptor.owner == "observability-stack"


def test_proxmox_exporter_is_opt_in_and_not_generic_observability() -> None:
    descriptor = _descriptor()

    assert descriptor.profiles == ["proxmox"]
    assert "observability" not in descriptor.profiles
    assert descriptor.ports == ["127.0.0.1:9221:9221"]
    assert descriptor.volumes == ["/etc/agmind/proxmox-exporter/pve.yml:/etc/pve.yml:ro"]
    assert descriptor.command == ["/etc/pve.yml", "9221", "0.0.0.0"]


def test_proxmox_exporter_prometheus_metadata_matches_pve_exporter_contract() -> None:
    descriptor = _descriptor()

    assert descriptor.observability.prometheus_scrape is True
    assert descriptor.observability.metrics_path == "/pve"
    assert descriptor.observability.metrics_port == 9221
    assert descriptor.observability.loki_scrape is False


def test_observability_component_owns_proxmox_exporter() -> None:
    contract = load_component_contracts()["observability-stack"]

    assert "proxmox-exporter" in contract.runtime.service_descriptors
    assert "proxmox" in contract.runtime.compose_profiles
    assert "proxmox-exporter:9221" in contract.runtime.ports


def test_proxmox_exporter_candidate_is_accepted_after_descriptor_admission() -> None:
    candidate = load_tool_candidates()["proxmox-exporter"]

    assert candidate.status == "accepted"
    assert candidate.dependencies.profiles == ("proxmox",)
    assert "real Proxmox" in candidate.next_step


def test_proxmox_exporter_examples_exist_without_real_tokens() -> None:
    assert PVE_EXAMPLE_PATH.exists(), "missing pve.yml example"
    assert SCRAPE_EXAMPLE_PATH.exists(), "missing Prometheus scrape example"

    pve_text = PVE_EXAMPLE_PATH.read_text(encoding="utf-8")
    scrape_text = SCRAPE_EXAMPLE_PATH.read_text(encoding="utf-8")

    assert "<PVE_TOKEN_VALUE>" in pve_text
    assert "verify_ssl: true" in pve_text
    assert "token_value: " in pve_text
    assert "sEcr3T" not in pve_text
    assert "password:" not in pve_text
    assert "__param_target" in scrape_text
    assert "metrics_path: /pve" in scrape_text


def test_rendered_compose_includes_proxmox_exporter_labels() -> None:
    rendered = render_to_string(
        profiles=["core", "observability", "proxmox"],
        domain="ci.example.com",
    )
    compose = yaml.safe_load(rendered)
    service = compose["services"]["proxmox-exporter"]

    assert service["image"] == f"{IMAGE}@{DIGEST}"
    assert service["profiles"] == ["proxmox"]
    assert service["ports"] == ["127.0.0.1:9221:9221"]
    assert service["labels"]["prometheus.scrape"] == "true"
    assert service["labels"]["prometheus.path"] == "/pve"
    assert service["labels"]["prometheus.port"] == "9221"
    assert "loki.scrape" not in service["labels"]


def test_component_check_script_accepts_proxmox_exporter_ownership() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "component_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
