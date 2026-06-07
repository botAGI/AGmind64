"""Tests for cluster environment inspection and deploy target recommendation."""

from __future__ import annotations

import json

import pytest

from agmind.cluster.detect import DiscoveredPeer
from agmind.cluster.inspect import CommandResult, inspect_cluster

pytestmark = pytest.mark.backend_any


def _runner(fixtures: dict[tuple[str, ...], CommandResult]):
    def run(args: tuple[str, ...]) -> CommandResult:
        return fixtures.get(args, CommandResult(returncode=127, stderr="not found"))

    return run


def test_inspect_cluster_recommends_k3s_when_kubernetes_is_available() -> None:
    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
        ("kubectl", "config", "current-context"): CommandResult(returncode=0, stdout="default\n"),
        ("kubectl", "version", "-o", "json"): CommandResult(
            returncode=0,
            stdout=json.dumps({"serverVersion": {"gitVersion": "v1.33.5+k3s1"}}),
        ),
        ("kubectl", "get", "nodes", "-o", "json"): CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "cp-1",
                                "labels": {"node-role.kubernetes.io/control-plane": "true"},
                            },
                            "status": {"allocatable": {"amd.com/gpu": "1"}},
                        },
                        {
                            "metadata": {"name": "worker-1", "labels": {}},
                            "status": {"allocatable": {}},
                        },
                    ]
                }
            ),
        ),
        ("kubectl", "get", "storageclass", "-o", "json"): CommandResult(
            returncode=0,
            stdout=json.dumps({"items": [{"metadata": {"name": "local-path"}}]}),
        ),
    }
    peer = DiscoveredPeer(
        hostname="worker",
        address="10.0.0.8",
        port=41423,
        version="0.1",
        gpu="AMD Radeon 8060S",
        ram_gb=96.0,
        is_strix_halo=True,
        services_count=0,
    )

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [peer],
    )

    assert report.detected_target == "k3s"
    assert report.confidence == 0.9
    assert report.kubernetes.available is True
    assert report.kubernetes.k3s is True
    assert report.kubernetes.node_count == 2
    assert report.kubernetes.amd_gpu_allocatable == 1
    assert report.kubernetes.storage_classes == ("local-path",)
    assert report.peers == (peer,)


def test_inspect_cluster_recommends_ubuntu_compose_for_docker_host() -> None:
    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
    }

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )

    assert report.detected_target == "ubuntu-compose"
    assert report.confidence == 0.7
    assert report.docker.available is True
    assert report.docker.compose_available is True
    assert report.kubernetes.available is False


def test_inspect_cluster_reports_lan_neighbors_when_mdns_empty() -> None:
    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
        ("ip", "neigh", "show"): CommandResult(
            returncode=0,
            stdout=(
                "192.168.1.1 dev wlp195s0 lladdr 50:ff:20:ed:31:4b REACHABLE\n"
                "192.168.1.58 dev wlp195s0 lladdr 96:a3:51:ad:e6:79 REACHABLE\n"
                "192.168.1.78 dev wlp195s0 lladdr 74:56:3c:bb:7a:c5 STALE\n"
                "fe80::1 dev wlp195s0 lladdr aa:bb router REACHABLE\n"
                "192.168.1.99 dev wlp195s0 FAILED\n"
            ),
        ),
        ("ip", "route", "show", "default"): CommandResult(
            returncode=0,
            stdout="default via 192.168.1.1 dev wlp195s0 proto dhcp\n",
        ),
    }

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
        port_probe=lambda address, port, timeout: address == "192.168.1.78" and port == 41423,
    )

    assert [neighbor.address for neighbor in report.lan_neighbors] == [
        "192.168.1.58",
        "192.168.1.78",
    ]
    assert report.lan_neighbors[0].mac == "96:a3:51:ad:e6:79"
    assert report.lan_neighbors[0].state == "REACHABLE"
    assert report.lan_neighbors[1].agmind_port_open is True
    assert "2 LAN neighbor candidate(s) visible" in report.reasons
    assert "AGmind mDNS peers are empty" in report.warnings[0]


def test_inspect_cluster_enriches_detected_target_from_catalog() -> None:
    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
    }

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )

    assert report.target is not None
    assert report.target.id == "ubuntu-compose"
    assert report.target.status == "supported"
    assert report.target.runtime_kind == "compose"
    assert report.target.renderer == "agmind render compose"
    assert report.target.profiles == ("core", "rag", "observability")
    assert report.target.provisioner_kind == "none"
    assert report.target.storage_profile == "local-paths"
    assert report.target.secrets_profile == "env-files"


def test_inspect_cluster_warns_when_detected_target_is_not_in_catalog() -> None:
    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
    }

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
        targets={},
    )

    assert report.target is None
    assert "not present in templates/deploy-targets catalog" in report.warnings[-1]


def test_inspect_cluster_recommends_proxmox_target_on_pve_host() -> None:
    fixtures = {
        ("pveversion",): CommandResult(returncode=0, stdout="pve-manager/8.4.1\n"),
        ("systemd-detect-virt",): CommandResult(returncode=0, stdout="none\n"),
    }

    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: path == "/etc/pve",
        discover_peers=lambda: [],
    )

    assert report.detected_target == "proxmox-vm-compose"
    assert report.confidence == 0.8
    assert report.proxmox.host is True
    assert "Proxmox host detected" in report.warnings[0]


def test_cmd_inspect_json_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd

    report = inspect_cluster(
        run=_runner({}),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )
    monkeypatch.setattr(cluster_cmd, "_inspect_cluster", lambda discover_timeout: report)

    rc = cluster_cmd.cmd_inspect(timeout=0.1, as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["detected_target"] == "unknown"
    assert payload["docker"]["available"] is False


def test_cmd_inspect_text_output_includes_target_contract(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd

    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
    }
    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )
    monkeypatch.setattr(cluster_cmd, "_inspect_cluster", lambda discover_timeout: report)

    rc = cluster_cmd.cmd_inspect(timeout=0.1, as_json=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Target contract: Ubuntu Compose [supported]" in out
    assert "runtime=compose" in out
    assert "provisioner=none" in out
    assert "storage=local-paths" in out
    assert "secrets=env-files" in out


def test_cmd_inspect_text_output_includes_lan_neighbors(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd
    from agmind.cluster.inspect import LanNeighbor

    fixtures = {
        ("docker", "version", "--format", "{{.Server.Version}}"): CommandResult(
            returncode=0, stdout="28.5.1\n"
        ),
        ("docker", "compose", "version", "--short"): CommandResult(returncode=0, stdout="2.40.3\n"),
    }
    report = inspect_cluster(
        run=_runner(fixtures),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )
    report = report.__class__(
        **{
            **report.to_dict(),
            "docker": report.docker,
            "kubernetes": report.kubernetes,
            "proxmox": report.proxmox,
            "peers": report.peers,
            "target": report.target,
            "lan_neighbors": (
                LanNeighbor(
                    address="192.168.1.58",
                    mac="96:a3:51:ad:e6:79",
                    interface="wlp195s0",
                    state="REACHABLE",
                ),
            ),
        }
    )
    monkeypatch.setattr(cluster_cmd, "_inspect_cluster", lambda discover_timeout: report)

    rc = cluster_cmd.cmd_inspect(timeout=0.1, as_json=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert "LAN neighbors: 1 candidate(s)" in out
    assert "192.168.1.58" in out


def test_run_command_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from agmind.cluster import inspect as inspect_mod
    from agmind.core import proc as proc_mod

    def raise_timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=("docker", "version"), timeout=5)

    monkeypatch.setattr(proc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(proc_mod.subprocess, "run", raise_timeout)

    result = inspect_mod._run_command(("docker", "version"))  # noqa: SLF001

    assert result.returncode == 124
    assert "timed out" in result.stderr
