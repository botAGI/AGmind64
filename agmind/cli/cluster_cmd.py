"""Phase M4.U.1: `agmind cluster` subcommands — auto-detect peers via mDNS."""

from __future__ import annotations

import json
import signal
import sys
import time

import typer

from agmind.cluster.detect import (
    DEFAULT_AGMIND_PORT,
    DEFAULT_DISCOVERY_TIMEOUT,
    gather_node_info,
)
from agmind.cluster.detect import (
    advertise as _advertise,
)
from agmind.cluster.detect import (
    discover as _discover,
)
from agmind.cluster.inspect import inspect_cluster as _inspect_cluster


def cmd_detect(timeout: float = DEFAULT_DISCOVERY_TIMEOUT, as_json: bool = False) -> int:
    """One-shot mDNS browse: print discovered peers."""
    peers = _discover(timeout=timeout)
    if as_json:
        from dataclasses import asdict

        print(json.dumps([asdict(p) for p in peers], indent=2, ensure_ascii=False))
        return 0

    if not peers:
        print(f"No agmind peers found (searched for {timeout:.1f}s).")
        print(
            "Hint: on other nodes run `agmind cluster advertise` чтобы они появились в discovery."
        )
        return 0

    print(f"Detected {len(peers)} peer(s):")
    print(f"{'':<3}{'HOSTNAME':<26} {'ADDRESS':<17} {'GPU':<34} {'RAM':>7}  VERSION")
    print("-" * 100)
    for p in peers:
        marker = "★" if p.is_strix_halo else " "
        print(
            f" {marker} {p.hostname:<26} {p.address:<17} {p.gpu:<34} {p.ram_gb:>5.1f}GB  v{p.version}"
        )
    print()
    print("★ = Strix Halo (gfx1151) compatible peer")
    return 0


def cmd_advertise(port: int = DEFAULT_AGMIND_PORT, duration: float = 0.0) -> int:
    """Daemon mode: register this node + sleep until Ctrl+C (or duration).

    duration=0 → blocks until SIGINT/SIGTERM.
    """
    info = gather_node_info()
    print(f"Advertising as {info.hostname} @ {info.address}:{port}")
    print(f"  GPU: {info.gpu_name}")
    print(f"  RAM: {info.ram_gb:.1f} GB")
    print(f"  Strix Halo: {'yes' if info.is_strix_halo else 'no'}")
    print(f"  agmind v{info.agmind_version}")
    print()
    print("Press Ctrl+C to stop.")

    stop_flag = {"stop": False}

    def _on_sig(signum, frame):  # type: ignore[no-untyped-def]
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    try:
        with _advertise(info, port=port):
            start = time.monotonic()
            while not stop_flag["stop"]:
                if duration > 0 and (time.monotonic() - start) >= duration:
                    break
                time.sleep(0.5)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\nUnregistered. Bye.")
    return 0


def cmd_status(as_json: bool = False, timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> int:
    """Show this node info + discovered peers (one-shot)."""
    self_info = gather_node_info()
    peers = _discover(timeout=timeout)

    if as_json:
        from dataclasses import asdict

        payload = {
            "self": asdict(self_info),
            "peers": [asdict(p) for p in peers],
            "cluster_size": 1 + len(peers),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"This node: {self_info.hostname} @ {self_info.address}")
    print(f"  GPU: {self_info.gpu_name}")
    print(
        f"  RAM: {self_info.ram_gb:.1f} GB · Strix Halo: {'yes' if self_info.is_strix_halo else 'no'}"
    )
    print(f"  agmind v{self_info.agmind_version}")
    print()
    if not peers:
        print("Cluster: 1 node (this only)")
        print("Hint: run `agmind cluster advertise` on other nodes для discovery.")
        return 0
    print(f"Cluster: {1 + len(peers)} nodes ({len(peers)} peer(s) discovered):")
    for p in peers:
        marker = "★" if p.is_strix_halo else " "
        print(f" {marker} {p.hostname:<26} {p.address:<17} {p.gpu:<32} {p.ram_gb:.1f}GB")
    return 0


def cmd_inspect(timeout: float = DEFAULT_DISCOVERY_TIMEOUT, as_json: bool = False) -> int:
    """Inspect local cluster/runtime environment and recommend a deploy target."""
    report = _inspect_cluster(discover_timeout=timeout)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"Detected target: {report.detected_target} (confidence {report.confidence:.1f})")
    if report.target is not None:
        print(f"Target contract: {report.target.name} [{report.target.status}]")
        print(
            "  "
            f"runtime={report.target.runtime_kind} "
            f"renderer={report.target.renderer} "
            f"profiles={','.join(report.target.profiles) or '-'}"
        )
        print(
            "  "
            f"provisioner={report.target.provisioner_kind} "
            f"storage={report.target.storage_profile} "
            f"secrets={report.target.secrets_profile}"
        )
    if report.reasons:
        print("Reasons:")
        for reason in report.reasons:
            print(f"  - {reason}")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")
    print()
    print(
        "Docker: "
        f"{'yes' if report.docker.available else 'no'}"
        f" compose={'yes' if report.docker.compose_available else 'no'}"
    )
    print(
        "Kubernetes: "
        f"{'yes' if report.kubernetes.available else 'no'}"
        f" k3s={'yes' if report.kubernetes.k3s else 'no'}"
        f" nodes={report.kubernetes.node_count}"
    )
    print(
        "Proxmox: "
        f"host={'yes' if report.proxmox.host else 'no'}"
        f" vm_guest={'yes' if report.proxmox.vm_guest else 'no'}"
    )
    print(f"Peers: {len(report.peers)} discovered")
    if report.lan_neighbors:
        print(f"LAN neighbors: {len(report.lan_neighbors)} candidate(s)")
        for neighbor in report.lan_neighbors:
            ports: list[str] = []
            if neighbor.agmind_port_open:
                ports.append("agmind:41423")
            if neighbor.ssh_port_open:
                ports.append("ssh:22")
            port_text = f" ports={','.join(ports)}" if ports else ""
            print(
                "  - "
                f"{neighbor.address:<15} {neighbor.mac:<17} "
                f"{neighbor.interface:<10} {neighbor.state}{port_text}"
            )
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``cluster`` command group to ``app``."""

    # ---- cluster subcommand group (Phase M4.U.1 — mDNS auto-detect) ----
    cluster_app = typer.Typer(
        name="cluster",
        help="Multi-node coordination — mDNS-based peer discovery.",
        no_args_is_help=True,
    )
    app.add_typer(cluster_app)

    @cluster_app.command("detect")
    def cluster_detect(
        timeout: float = typer.Option(
            3.0,
            "--timeout",
            "-t",
            help="Discovery duration в секундах",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Browse LAN для agmind peers via mDNS (one-shot)."""
        raise typer.Exit(code=cmd_detect(timeout=timeout, as_json=as_json))

    @cluster_app.command("advertise")
    def cluster_advertise(
        port: int = typer.Option(
            41423,
            "--port",
            "-p",
            help="Port для service advertisement",
        ),
        duration: float = typer.Option(
            0.0,
            "--duration",
            "-d",
            help="Stop после N seconds (0 = forever / Ctrl+C)",
        ),
    ) -> None:
        """Publish this node как `_agmind._tcp.local.` service (daemon mode)."""
        raise typer.Exit(code=cmd_advertise(port=port, duration=duration))

    @cluster_app.command("status")
    def cluster_status(
        timeout: float = typer.Option(3.0, "--timeout", "-t"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show this node info + discovered peers."""
        raise typer.Exit(code=cmd_status(timeout=timeout, as_json=as_json))

    @cluster_app.command("inspect")
    def cluster_inspect(
        timeout: float = typer.Option(3.0, "--timeout", "-t"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Inspect local runtime/cluster environment and recommend deploy target."""
        raise typer.Exit(code=cmd_inspect(timeout=timeout, as_json=as_json))


__all__ = ["cmd_advertise", "cmd_detect", "cmd_inspect", "cmd_status", "register"]
