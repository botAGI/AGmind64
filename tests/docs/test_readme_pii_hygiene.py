"""Phase 09-06 (M8): the public READMEs must not leak host-specific LAN PII.

`beelinknode-GTR-Pro` / `192.168.1.151` / `192.168.1.58` / `192.168.1.78` were committed to
README.md and README.ru.md. Host-specific status belongs in local run-notes, not a public
README. This gate blocks RFC1918 IP literals and known leaked host tokens from regressing."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_ROOT = Path(__file__).resolve().parents[2]
_READMES = [_ROOT / "README.md", _ROOT / "README.ru.md"]

# RFC1918 private ranges (10/8, 172.16/12, 192.168/16). Loopback 127.x is allowed (it is the
# documented bind address for local-only ports throughout the docs).
_RFC1918 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
_LEAKED_HOST_TOKENS = ("beelinknode", "GTR-Pro")


@pytest.mark.parametrize("readme", _READMES, ids=lambda p: p.name)
def test_readme_has_no_rfc1918_ip(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    hits = _RFC1918.findall(text)
    assert not hits, f"{readme.name} leaks private LAN IPs: {sorted(set(hits))}"


@pytest.mark.parametrize("readme", _READMES, ids=lambda p: p.name)
def test_readme_has_no_leaked_host_tokens(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    found = [tok for tok in _LEAKED_HOST_TOKENS if tok in text]
    assert not found, f"{readme.name} leaks host-specific tokens: {found}"
