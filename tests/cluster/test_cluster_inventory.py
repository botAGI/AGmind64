"""Phase M5.4.3: tests for cluster Ansible inventory generation."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from agmind.cluster.inventory import (
    generate_inventory_yaml,
    write_inventory,
)

pytestmark = pytest.mark.backend_any


def test_inventory_yaml_parses_with_no_peers() -> None:
    text = generate_inventory_yaml(peers=[])
    parsed = yaml.safe_load(text)
    assert "all" in parsed
    assert "agmind_master" in parsed["all"]["children"]
    workers = parsed["all"]["children"]["agmind_workers"]
    # No peers — empty hosts dict
    assert workers["hosts"] in ({}, None)


def test_inventory_yaml_includes_each_peer() -> None:
    peers = [("beelink-alpha", "192.168.1.20"), ("beelink-beta", "192.168.1.21")]
    text = generate_inventory_yaml(peers=peers)
    parsed = yaml.safe_load(text)
    workers = parsed["all"]["children"]["agmind_workers"]["hosts"]
    assert set(workers.keys()) == {"beelink-alpha", "beelink-beta"}
    assert workers["beelink-alpha"]["ansible_host"] == "192.168.1.20"
    assert workers["beelink-alpha"]["agmind_role"] == "worker"
    assert workers["beelink-alpha"]["agmind_profiles"] == ["core"]
    # Endpoint URL uses *.local pattern (mDNS)
    assert ".local:8080" in workers["beelink-alpha"]["agmind_worker_endpoint"]


def test_inventory_master_profiles_customizable() -> None:
    text = generate_inventory_yaml(
        peers=[],
        master_profiles=("core", "rag", "ui"),
    )
    parsed = yaml.safe_load(text)
    master = parsed["all"]["children"]["agmind_master"]["hosts"]
    # Find the single master entry (dict with one key)
    profiles = next(iter(master.values()))["agmind_profiles"]
    assert profiles == ["core", "rag", "ui"]


def test_inventory_slug_sanitizes_hostname() -> None:
    """Hostnames с .local / dots должны нормализоваться для inventory key."""
    peers = [("foo.bar.local", "10.0.0.5"), ("Mixed Case", "10.0.0.6")]
    text = generate_inventory_yaml(peers=peers)
    parsed = yaml.safe_load(text)
    keys = set(parsed["all"]["children"]["agmind_workers"]["hosts"])
    assert "foo-bar" in keys
    assert "mixed-case" in keys


def test_write_inventory_creates_file(tmp_path: Path) -> None:
    out = tmp_path / "inventory" / "cluster.yml"
    written = write_inventory(
        peers=[("worker-1", "10.0.0.7")],
        output_path=out,
    )
    assert written == out
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "worker-1" in content
    # File chmod = 0o644
    mode = out.stat().st_mode & 0o777
    assert mode == 0o644


def test_write_inventory_preserves_existing_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "inventory" / "cluster.yml"
    out.parent.mkdir(parents=True)
    old = "# old cluster inventory\nall: {}\n"
    out.write_text(old, encoding="utf-8")
    out.chmod(0o600)
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self == out or self.name == f".{out.name}.tmp":
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        write_inventory(peers=[("worker-1", "10.0.0.7")], output_path=out)

    assert out.read_text(encoding="utf-8") == old
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert not out.with_name(f".{out.name}.tmp").exists()
