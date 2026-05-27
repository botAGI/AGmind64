"""Phase M4.U.1: cluster auto-detect via mDNS (zeroconf).

Each agmind node может объявлять себя в LAN через `_agmind._tcp.local.`
service. Другие agmind nodes browse'ят этот тип и автоматически discover'ят
peers — без manual IP edits.

Public API:
    advertise(node_info, port=41423, timeout_s=...)  — register service
    discover(timeout=3.0)                            — list found peers
    DiscoveredPeer                                   — NamedTuple результат

UI integration:
    agmind cluster detect          # one-shot scan + print
    agmind cluster advertise       # daemon (loop until Ctrl+C)
    agmind doctor                  # `cluster-peers` check (added в M4.U.2)

Без zeroconf installed → graceful fallback: advertise no-op, discover [].
"""

from __future__ import annotations

import platform
import socket
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from typing import cast

from agmind.core.logging import logger

log = logger(__name__)

AGMIND_SERVICE_TYPE = "_agmind._tcp.local."
DEFAULT_AGMIND_PORT = 41423
DEFAULT_DISCOVERY_TIMEOUT = 3.0  # seconds


@dataclass(frozen=True)
class NodeInfo:
    """Info advertised по mDNS — visible to other peers."""

    hostname: str
    address: str  # primary local IP (IPv4)
    agmind_version: str
    gpu_name: str
    ram_gb: float
    is_strix_halo: bool
    services_count: int = 0  # how many compose services running (для discovery preview)

    def to_txt_record(self) -> dict[str, str]:
        """Convert to mDNS TXT record — все values stringified, max ~200 bytes total."""
        return {
            "hostname": self.hostname[:60],
            "version": self.agmind_version[:32],
            "gpu": self.gpu_name[:48],
            "ram_gb": f"{self.ram_gb:.1f}",
            "strix": "1" if self.is_strix_halo else "0",
            "services": str(self.services_count),
        }


@dataclass(frozen=True)
class DiscoveredPeer:
    """One peer found via mDNS browse."""

    hostname: str
    address: str
    port: int
    version: str
    gpu: str
    ram_gb: float
    is_strix_halo: bool
    services_count: int

    @property
    def display(self) -> str:
        marker = "★" if self.is_strix_halo else " "
        return (
            f"{marker} {self.hostname:<24} {self.address:<15} "
            f"{self.gpu:<32} {self.ram_gb:>5.1f}GB  v{self.version}"
        )


def _read_ram_gb() -> float:
    """Read total physical RAM в GiB. Pure stdlib (без psutil)."""
    try:
        from pathlib import Path as _P

        for line in _P("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb / 1024 / 1024
    except (OSError, ValueError, IndexError):
        pass
    # Fallback: psutil if installed
    try:
        import psutil

        return float(psutil.virtual_memory().total / 1024**3)
    except ImportError:
        return 0.0


def _get_primary_ipv4() -> str:
    """Best-effort detect primary outbound IPv4 (no actual outbound traffic)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return cast(str, s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def gather_node_info(agmind_version: str = "") -> NodeInfo:
    """Inspect host чтобы заполнить NodeInfo для advertise."""
    from agmind.compute.detect import detect_gpu

    hostname = platform.node() or "unknown"
    addr = _get_primary_ipv4()
    if not agmind_version:
        try:
            from agmind import __version__

            agmind_version = __version__
        except ImportError:
            agmind_version = "unknown"
    try:
        gpu = detect_gpu()
        gpu_name = gpu.name if gpu else "none"
        is_strix = gpu.is_strix_halo if gpu else False
    except Exception:  # noqa: BLE001
        gpu_name = "unknown"
        is_strix = False
    ram_gb = _read_ram_gb()
    return NodeInfo(
        hostname=hostname,
        address=addr,
        agmind_version=agmind_version,
        gpu_name=gpu_name,
        ram_gb=ram_gb,
        is_strix_halo=is_strix,
        services_count=0,
    )


def discover(
    timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    exclude_self: bool = True,
) -> list[DiscoveredPeer]:
    """Browse `_agmind._tcp` services на LAN. Returns list of found peers.

    Blocks для `timeout` seconds.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        log.warning("zeroconf not installed — cluster discover unavailable")
        return []

    found: list[DiscoveredPeer] = []
    self_addr = _get_primary_ipv4()
    self_host = platform.node()

    class _Listener(ServiceListener):
        def add_service(self, zc, type_, name):  # type: ignore[no-untyped-def]
            info = zc.get_service_info(type_, name, timeout=1500)
            if info is None:
                return
            txt = {
                k.decode("utf-8", errors="replace"): (
                    v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
                )
                for k, v in (info.properties or {}).items()
                if isinstance(k, bytes | bytearray)
            }
            addrs = [".".join(str(b) for b in a) for a in info.addresses or []]
            addr = addrs[0] if addrs else ""
            hostname = txt.get("hostname", "") or info.server.rstrip(".")
            if exclude_self and (addr == self_addr or hostname == self_host):
                return
            try:
                ram = float(txt.get("ram_gb", "0"))
            except ValueError:
                ram = 0.0
            try:
                services = int(txt.get("services", "0"))
            except ValueError:
                services = 0
            found.append(
                DiscoveredPeer(
                    hostname=hostname,
                    address=addr,
                    port=info.port or DEFAULT_AGMIND_PORT,
                    version=txt.get("version", "?"),
                    gpu=txt.get("gpu", "?"),
                    ram_gb=ram,
                    is_strix_halo=txt.get("strix", "0") == "1",
                    services_count=services,
                )
            )

        def update_service(self, zc, type_, name):  # type: ignore[no-untyped-def]
            pass

        def remove_service(self, zc, type_, name):  # type: ignore[no-untyped-def]
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, AGMIND_SERVICE_TYPE, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    # Dedupe by (hostname, address)
    seen: set[tuple[str, str]] = set()
    out: list[DiscoveredPeer] = []
    for p in found:
        key = (p.hostname, p.address)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def advertise(
    info: NodeInfo,
    port: int = DEFAULT_AGMIND_PORT,
) -> AbstractContextManager[NodeInfo]:
    """Register this node для mDNS browse'ов. Returns context manager.

    Usage:
        with advertise(gather_node_info()) as registration:
            # do stuff; service is advertised while inside
            time.sleep(...)
    """
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError as exc:
        raise RuntimeError("zeroconf not installed — install via pip install zeroconf") from exc

    name = f"{info.hostname}.{AGMIND_SERVICE_TYPE}"
    # Trim hostname to ≤63 chars for DNS label limit
    if len(name) > 63 + len(AGMIND_SERVICE_TYPE):
        short_host = info.hostname[: 63 - len(AGMIND_SERVICE_TYPE) - 1]
        name = f"{short_host}.{AGMIND_SERVICE_TYPE}"

    addr_bytes = socket.inet_aton(info.address)
    service = ServiceInfo(
        type_=AGMIND_SERVICE_TYPE,
        name=name,
        addresses=[addr_bytes],
        port=port,
        properties={k.encode(): v.encode() for k, v in info.to_txt_record().items()},
        server=f"{info.hostname}.local.",
    )

    class _Registration:
        def __init__(self) -> None:
            self.zc = Zeroconf()
            self.service = service

        def __enter__(self) -> NodeInfo:
            self.zc.register_service(self.service)
            log.info("mDNS advertised: %s @ %s:%d", info.hostname, info.address, port)
            return info

        def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
            try:
                self.zc.unregister_service(self.service)
            except Exception:  # noqa: BLE001
                pass
            self.zc.close()

    return _Registration()


def detect_peers_as_dict(timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> list[dict[str, object]]:
    """Convenience: discover + return list[dict] для JSON output."""
    return [asdict(p) for p in discover(timeout=timeout)]


__all__ = [
    "AGMIND_SERVICE_TYPE",
    "DEFAULT_AGMIND_PORT",
    "DEFAULT_DISCOVERY_TIMEOUT",
    "DiscoveredPeer",
    "NodeInfo",
    "advertise",
    "detect_peers_as_dict",
    "discover",
    "gather_node_info",
]
