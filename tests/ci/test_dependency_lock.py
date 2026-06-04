"""Phase 12-02 (M8): a verifiable Python dependency lock.

`constraints/*.txt` are a compatibility ENVELOPE (broad ranges), not a lock — inadequate for a
privileged installer. `constraints/dev.lock` is a fully-pinned, hash-verified resolution
(`uv pip compile pyproject.toml --extra dev --generate-hashes`). This gate keeps it a real
lock: every requirement pinned with `==` and at least one sha256 hash."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_LOCK = Path(__file__).resolve().parents[2] / "constraints" / "dev.lock"

# A requirement line: `name==version \` (continuation) — uv's --generate-hashes layout.
_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==[^\s;]+", re.MULTILINE)
# A range operator at the start of a requirement (what a lock must NOT contain).
_RANGE_RE = re.compile(r"^[A-Za-z0-9._-]+\s*(>=|<=|>|<|~=|!=)", re.MULTILINE)


def test_lock_exists() -> None:
    assert _LOCK.exists(), "constraints/dev.lock missing — run `uv pip compile … --generate-hashes`"


def test_lock_pins_many_packages_exactly() -> None:
    text = _LOCK.read_text(encoding="utf-8")
    pinned = _PIN_RE.findall(text)
    # A real resolution of agmind[dev] pulls in well over 50 packages.
    assert len(pinned) > 50, f"lock looks too small ({len(pinned)} pins) — not a full resolution"


def test_lock_has_no_range_requirements() -> None:
    text = _LOCK.read_text(encoding="utf-8")
    # Strip comment lines (# via …) so an annotation can't trip the range check.
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    ranges = _RANGE_RE.findall(body)
    assert not ranges, f"lock contains range requirements (not pinned): {ranges[:5]}"


def test_lock_is_hash_verified() -> None:
    text = _LOCK.read_text(encoding="utf-8")
    assert text.count("--hash=sha256:") > 100, "lock is not hash-pinned (--generate-hashes)"


def test_lock_records_regeneration_command() -> None:
    """The header must record how to regenerate it, so the lock can't become a mystery file."""
    head = _LOCK.read_text(encoding="utf-8")[:400]
    assert "uv pip compile" in head and "--generate-hashes" in head


# ---------------------------------------------------------------------------
# Staleness / conflict guard (review MEDIUM dev-lock-unused-and-stale)
# ---------------------------------------------------------------------------

_CORE = Path(__file__).resolve().parents[2] / "constraints" / "core.txt"


def _lock_pins() -> dict[str, str]:
    """Map normalized package name → pinned version from the lock."""
    text = _LOCK.read_text(encoding="utf-8")
    pins: dict[str, str] = {}
    for match in re.finditer(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<ver>[^\s;\\]+)", text, re.MULTILINE):
        pins[match.group("name").lower().replace("_", "-")] = match.group("ver")
    return pins


def test_lock_satisfies_declared_core_envelope() -> None:
    """Every runtime constraint in core.txt must be SATISFIED by the lock's pin — so the lock
    can never silently drift OUTSIDE the project's declared envelope (the class of drift that
    let argon2-cffi sit at a version the envelope would later forbid). Network-free: compares
    the committed lock against the committed constraints, no resolution."""
    from packaging.requirements import Requirement

    pins = _lock_pins()
    violations: list[str] = []
    for raw in _CORE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        pinned = pins.get(req.name.lower().replace("_", "-"))
        assert pinned, f"core.txt requires {req.name} but the lock has no pin for it"
        if not req.specifier.contains(pinned, prereleases=True):
            violations.append(f"{req.name}: lock=={pinned} violates core.txt '{req.specifier}'")
    assert not violations, "constraints/dev.lock drifted outside core.txt envelope:\n" + "\n".join(
        violations
    )
