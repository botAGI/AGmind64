"""Phase M4.U.1: `agmind cluster` subcommands — auto-detect peers via mDNS."""

from __future__ import annotations

import json
import signal
import sys
import time

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


__all__ = ["cmd_advertise", "cmd_detect", "cmd_status"]
