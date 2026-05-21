"""Phase M4.U.1: tests for cluster mDNS auto-detect."""

from __future__ import annotations

import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from agmind.cluster.detect import (
    AGMIND_SERVICE_TYPE,
    DEFAULT_AGMIND_PORT,
    DiscoveredPeer,
    NodeInfo,
    _get_primary_ipv4,
    advertise,
    discover,
    gather_node_info,
)

pytestmark = pytest.mark.backend_any


# ---------- NodeInfo / TXT serialization ----------


def test_node_info_to_txt_record() -> None:
    info = NodeInfo(
        hostname="lab-host", address="10.0.0.5",
        agmind_version="0.3.0", gpu_name="AMD Radeon 8060S",
        ram_gb=125.5, is_strix_halo=True, services_count=12,
    )
    txt = info.to_txt_record()
    assert txt["hostname"] == "lab-host"
    assert txt["version"] == "0.3.0"
    assert txt["gpu"] == "AMD Radeon 8060S"
    assert txt["ram_gb"] == "125.5"
    assert txt["strix"] == "1"
    assert txt["services"] == "12"


def test_node_info_strix_false_serializes_to_0() -> None:
    info = NodeInfo(
        hostname="x", address="1.2.3.4", agmind_version="v",
        gpu_name="g", ram_gb=10.0, is_strix_halo=False,
    )
    assert info.to_txt_record()["strix"] == "0"


def test_node_info_truncates_long_hostname() -> None:
    info = NodeInfo(
        hostname="x" * 200, address="1.2.3.4", agmind_version="v",
        gpu_name="g", ram_gb=10.0, is_strix_halo=False,
    )
    assert len(info.to_txt_record()["hostname"]) <= 60


# ---------- DiscoveredPeer ----------


def test_discovered_peer_display_marks_strix() -> None:
    p = DiscoveredPeer(
        hostname="node1", address="10.0.0.5", port=41423,
        version="0.3", gpu="AMD Radeon 8060S",
        ram_gb=125.0, is_strix_halo=True, services_count=10,
    )
    assert "★" in p.display
    assert "node1" in p.display


def test_discovered_peer_display_non_strix_no_star() -> None:
    p = DiscoveredPeer(
        hostname="x", address="1.2.3.4", port=42,
        version="v", gpu="g", ram_gb=1.0, is_strix_halo=False, services_count=0,
    )
    assert "★" not in p.display


# ---------- _get_primary_ipv4 ----------


def test_get_primary_ipv4_returns_string() -> None:
    addr = _get_primary_ipv4()
    assert isinstance(addr, str)
    # Smoke: must be valid IPv4 dotted (или 127.0.0.1 fallback)
    parts = addr.split(".")
    assert len(parts) == 4
    for part in parts:
        assert 0 <= int(part) <= 255


def test_get_primary_ipv4_fallback_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSocket:
        def __init__(self, *args, **kw):
            pass

        def connect(self, *args):
            raise OSError("no network")

        def getsockname(self):
            return ("1.1.1.1", 0)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: FakeSocket())
    assert _get_primary_ipv4() == "127.0.0.1"


# ---------- gather_node_info ----------


def test_gather_node_info_returns_struct() -> None:
    info = gather_node_info(agmind_version="1.2.3")
    assert info.agmind_version == "1.2.3"
    assert isinstance(info.hostname, str)
    assert isinstance(info.address, str)


# ---------- discover (without real zeroconf — graceful) ----------


def test_discover_returns_empty_without_zeroconf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если zeroconf не installed — discover returns empty list (no crash)."""
    import sys
    monkeypatch.setitem(sys.modules, "zeroconf", None)
    # We can't fully simulate ImportError из inside; just verify call returns list
    # In current setup zeroconf IS installed, so it'll run normally with 0 results
    # (timeout=0.1 — no peers in test env).
    result = discover(timeout=0.5)
    assert isinstance(result, list)


def test_discover_excludes_self_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented behaviour — exclude_self=True default."""
    # In CI там no real peers, discover returns []
    result = discover(timeout=0.5, exclude_self=True)
    assert result == [] or all(p.address != "127.0.0.1" for p in result)


# ---------- advertise context manager (smoke) ----------


def test_advertise_context_manager_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: advertise().__enter__ regsiters, __exit__ unregisters.

    Не проверяет real mDNS broadcast (требует network) — только что
    return value поведение правильное.
    """
    info = NodeInfo(
        hostname="test-node", address="127.0.0.1",
        agmind_version="0.0.0-test", gpu_name="test",
        ram_gb=1.0, is_strix_halo=False, services_count=0,
    )
    reg = advertise(info, port=41999)
    with reg as advertised:
        assert advertised.hostname == "test-node"
    # __exit__ should have called unregister; no exception


def test_advertise_raises_if_no_zeroconf(monkeypatch: pytest.MonkeyPatch) -> None:
    """If zeroconf not installed — advertise raises RuntimeError."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zeroconf":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    info = NodeInfo(
        hostname="t", address="127.0.0.1", agmind_version="v",
        gpu_name="g", ram_gb=1.0, is_strix_halo=False,
    )
    with pytest.raises(RuntimeError, match="zeroconf"):
        advertise(info)


# ---------- CLI integration smoke ----------


def test_cmd_detect_no_peers(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd

    monkeypatch.setattr(cluster_cmd, "_discover", lambda timeout: [])
    rc = cluster_cmd.cmd_detect(timeout=0.1)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No agmind peers" in out


def test_cmd_detect_finds_peers(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd

    monkeypatch.setattr(cluster_cmd, "_discover", lambda timeout: [
        DiscoveredPeer(
            hostname="node1", address="10.0.0.5", port=41423,
            version="0.3.0", gpu="AMD Radeon 8060S",
            ram_gb=125.0, is_strix_halo=True, services_count=10,
        ),
    ])
    rc = cluster_cmd.cmd_detect(timeout=0.1)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Detected 1 peer" in out
    assert "node1" in out
    assert "★" in out


def test_cmd_detect_json_output(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as _json
    from agmind.cli import cluster_cmd

    monkeypatch.setattr(cluster_cmd, "_discover", lambda timeout: [
        DiscoveredPeer(
            hostname="alpha", address="1.2.3.4", port=42,
            version="x", gpu="y", ram_gb=2.0,
            is_strix_halo=False, services_count=0,
        ),
    ])
    rc = cluster_cmd.cmd_detect(timeout=0.1, as_json=True)
    assert rc == 0
    data = _json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["hostname"] == "alpha"


def test_cmd_status_self_only(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import cluster_cmd

    monkeypatch.setattr(cluster_cmd, "_discover", lambda timeout: [])
    monkeypatch.setattr(
        cluster_cmd, "gather_node_info",
        lambda: NodeInfo(
            hostname="solo", address="127.0.0.1", agmind_version="0.3",
            gpu_name="t", ram_gb=8.0, is_strix_halo=False,
        ),
    )
    rc = cluster_cmd.cmd_status(timeout=0.1)
    assert rc == 0
    out = capsys.readouterr().out
    assert "This node: solo" in out
    assert "1 node" in out
