"""Cluster environment inspection and deployment target recommendation."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from agmind.cluster.detect import DEFAULT_DISCOVERY_TIMEOUT, DiscoveredPeer, discover
from agmind.deploy.targets import DeploymentTarget, load_deploy_targets


@dataclass(frozen=True)
class CommandResult:
    """Small subprocess result used by injectable probes."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class DockerInspect:
    """Local Docker runtime status."""

    available: bool
    version: str = ""
    compose_available: bool = False
    compose_version: str = ""


@dataclass(frozen=True)
class KubernetesInspect:
    """Kubernetes/k3s status visible from the current environment."""

    available: bool
    context: str = ""
    server_version: str = ""
    k3s: bool = False
    node_count: int = 0
    control_plane_count: int = 0
    amd_gpu_allocatable: int = 0
    storage_classes: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class ProxmoxInspect:
    """Proxmox host/guest hints."""

    host: bool
    vm_guest: bool = False
    version: str = ""
    virtualization: str = ""


@dataclass(frozen=True)
class DeploymentTargetInspect:
    """Deploy target contract summary attached to an inspection recommendation."""

    id: str
    name: str
    status: str
    summary: str
    runtime_kind: str
    renderer: str
    profiles: tuple[str, ...]
    provisioner_kind: str
    storage_profile: str
    secrets_profile: str


@dataclass(frozen=True)
class ClusterInspectReport:
    """Unified environment report for choosing an AGmind deployment target."""

    detected_target: str
    confidence: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    docker: DockerInspect
    kubernetes: KubernetesInspect
    proxmox: ProxmoxInspect
    peers: tuple[DiscoveredPeer, ...] = ()
    target: DeploymentTargetInspect | None = None

    def to_dict(self) -> dict[str, object]:
        """Return JSON-serializable report."""
        return asdict(self)


CommandRunner = Callable[[tuple[str, ...]], CommandResult]
PathExists = Callable[[str], bool]
PeerDiscovery = Callable[[], list[DiscoveredPeer]]


def inspect_cluster(
    *,
    run: CommandRunner | None = None,
    path_exists: PathExists | None = None,
    discover_peers: PeerDiscovery | None = None,
    discover_timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    targets: Mapping[str, DeploymentTarget] | None = None,
) -> ClusterInspectReport:
    """Inspect local runtime, cluster APIs, Proxmox hints, and LAN peers."""
    runner = run or _run_command
    exists = path_exists or _path_exists
    peer_probe = discover_peers or (lambda: discover(timeout=discover_timeout, exclude_self=True))
    target_catalog = load_deploy_targets() if targets is None else targets

    docker = _inspect_docker(runner)
    kubernetes = _inspect_kubernetes(runner)
    proxmox = _inspect_proxmox(runner, exists)
    peers = tuple(peer_probe())
    detected_target, confidence, reasons, warnings = _recommend_target(
        docker=docker,
        kubernetes=kubernetes,
        proxmox=proxmox,
        peer_count=len(peers),
    )
    target = _target_inspect(detected_target, target_catalog, warnings)

    return ClusterInspectReport(
        detected_target=detected_target,
        confidence=confidence,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        docker=docker,
        kubernetes=kubernetes,
        proxmox=proxmox,
        peers=peers,
        target=target,
    )


def _inspect_docker(run: CommandRunner) -> DockerInspect:
    version = run(("docker", "version", "--format", "{{.Server.Version}}"))
    compose = run(("docker", "compose", "version", "--short"))
    return DockerInspect(
        available=version.returncode == 0,
        version=version.stdout.strip() if version.returncode == 0 else "",
        compose_available=compose.returncode == 0,
        compose_version=compose.stdout.strip() if compose.returncode == 0 else "",
    )


def _inspect_kubernetes(run: CommandRunner) -> KubernetesInspect:
    context = run(("kubectl", "config", "current-context"))
    if context.returncode != 0:
        return KubernetesInspect(
            available=False,
            error=(context.stderr or context.stdout).strip(),
        )

    version = run(("kubectl", "version", "-o", "json"))
    nodes = run(("kubectl", "get", "nodes", "-o", "json"))
    storage = run(("kubectl", "get", "storageclass", "-o", "json"))

    server_version = _server_git_version(version.stdout) if version.returncode == 0 else ""
    node_items = _json_items(nodes.stdout) if nodes.returncode == 0 else ()
    storage_classes = _storage_class_names(storage.stdout) if storage.returncode == 0 else ()

    return KubernetesInspect(
        available=True,
        context=context.stdout.strip(),
        server_version=server_version,
        k3s=_is_k3s(server_version, node_items),
        node_count=len(node_items),
        control_plane_count=sum(1 for item in node_items if _is_control_plane_node(item)),
        amd_gpu_allocatable=sum(_amd_gpu_count(item) for item in node_items),
        storage_classes=storage_classes,
    )


def _inspect_proxmox(run: CommandRunner, path_exists: PathExists) -> ProxmoxInspect:
    pveversion = run(("pveversion",))
    virt = run(("systemd-detect-virt",))
    virt_name = virt.stdout.strip() if virt.returncode == 0 else ""
    return ProxmoxInspect(
        host=path_exists("/etc/pve") or pveversion.returncode == 0,
        vm_guest=virt_name in {"kvm", "qemu"},
        version=pveversion.stdout.strip() if pveversion.returncode == 0 else "",
        virtualization=virt_name,
    )


def _recommend_target(
    *,
    docker: DockerInspect,
    kubernetes: KubernetesInspect,
    proxmox: ProxmoxInspect,
    peer_count: int,
) -> tuple[str, float, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []

    if kubernetes.available and kubernetes.k3s:
        reasons.append(f"k3s API reachable via context '{kubernetes.context}'")
        if kubernetes.node_count:
            reasons.append(f"{kubernetes.node_count} Kubernetes node(s) visible")
        if kubernetes.amd_gpu_allocatable:
            reasons.append(f"{kubernetes.amd_gpu_allocatable} amd.com/gpu allocatable")
        return "k3s", 0.9, reasons, warnings

    if proxmox.host:
        reasons.append("Proxmox VE host tooling detected")
        warnings.append(
            "Proxmox host detected; prefer provisioning AGmind VMs instead of installing the app stack on the PVE host"
        )
        return "proxmox-vm-compose", 0.8, reasons, warnings

    if docker.available and docker.compose_available:
        reasons.append("Docker Engine and Compose are available")
        if proxmox.vm_guest:
            reasons.append("host appears to be a Proxmox/KVM guest")
        if peer_count:
            reasons.append(f"{peer_count} AGmind LAN peer(s) discovered")
        return "ubuntu-compose", 0.7, reasons, warnings

    warnings.append(
        "No supported runtime detected: Docker Compose, k3s, or Proxmox tooling unavailable"
    )
    return "unknown", 0.0, reasons, warnings


def _target_inspect(
    detected_target: str,
    targets: Mapping[str, DeploymentTarget],
    warnings: list[str],
) -> DeploymentTargetInspect | None:
    if detected_target == "unknown":
        return None

    target = targets.get(detected_target)
    if target is None:
        warnings.append(
            f"Detected deployment target '{detected_target}' is not present in templates/deploy-targets catalog"
        )
        return None

    return DeploymentTargetInspect(
        id=target.id,
        name=target.name,
        status=target.status,
        summary=target.summary,
        runtime_kind=target.runtime.kind,
        renderer=target.runtime.renderer,
        profiles=target.runtime.profiles,
        provisioner_kind=target.provisioner.kind,
        storage_profile=target.storage_profile,
        secrets_profile=target.secrets_profile,
    )


def _server_git_version(stdout: str) -> str:
    data = _json_object(stdout)
    server = data.get("serverVersion", {})
    if isinstance(server, dict):
        value = server.get("gitVersion", "")
        return str(value) if value else ""
    return ""


def _json_object(stdout: str) -> dict[str, object]:
    try:
        data: object = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _json_items(stdout: str) -> tuple[dict[str, object], ...]:
    data = _json_object(stdout)
    items = data.get("items", ())
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _storage_class_names(stdout: str) -> tuple[str, ...]:
    names: list[str] = []
    for item in _json_items(stdout):
        metadata = item.get("metadata", {})
        if isinstance(metadata, dict):
            name = metadata.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return tuple(sorted(names))


def _is_k3s(server_version: str, nodes: tuple[dict[str, object], ...]) -> bool:
    if "k3s" in server_version.lower():
        return True
    return any("k3s.io/" in key for item in nodes for key in _node_labels(item))


def _is_control_plane_node(item: dict[str, object]) -> bool:
    labels = _node_labels(item)
    return (
        "node-role.kubernetes.io/control-plane" in labels
        or "node-role.kubernetes.io/master" in labels
    )


def _node_labels(item: dict[str, object]) -> dict[str, object]:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    labels = metadata.get("labels", {})
    return labels if isinstance(labels, dict) else {}


def _amd_gpu_count(item: dict[str, object]) -> int:
    status = item.get("status", {})
    if not isinstance(status, dict):
        return 0
    allocatable = status.get("allocatable", {})
    if not isinstance(allocatable, dict):
        return 0
    value = allocatable.get("amd.com/gpu", 0)
    try:
        return int(str(value))
    except ValueError:
        return 0


def _run_command(args: tuple[str, ...]) -> CommandResult:
    if shutil.which(args[0]) is None:
        return CommandResult(returncode=127, stderr=f"{args[0]} not found")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        command = " ".join(args)
        return CommandResult(
            returncode=124,
            stdout=_output_text(exc.output),
            stderr=f"{command} timed out after {exc.timeout} seconds",
        )
    except OSError as exc:
        return CommandResult(returncode=127, stderr=str(exc))
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _path_exists(path: str) -> bool:
    return Path(path).exists()


__all__ = [
    "ClusterInspectReport",
    "CommandResult",
    "DeploymentTargetInspect",
    "DockerInspect",
    "KubernetesInspect",
    "ProxmoxInspect",
    "inspect_cluster",
]
