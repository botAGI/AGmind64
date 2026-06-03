"""Tests for agmind.diagnostics.estimate — mem_limit-vs-RAM/GTT estimation.

Hardware-free: every host figure (RAM, GTT) is injected, so these never call
``detect_host``. Catalog assertions check the SUMMATION mechanism, never a
frozen service count (Правила Карпатого #14 — descriptors come and go).
"""

from __future__ import annotations

import pytest

from agmind.diagnostics.estimate import (
    GIB,
    KIB,
    MIB,
    collect_service_mem,
    estimate_memory,
    parse_mem_limit,
)

pytestmark = pytest.mark.backend_any


def test_parse_mem_limit_units() -> None:
    assert parse_mem_limit("4g") == 4 * GIB
    assert parse_mem_limit("512m") == 512 * MIB
    assert parse_mem_limit("1024k") == 1024 * KIB
    assert parse_mem_limit("112g") == 112 * GIB


def test_parse_mem_limit_empty_is_unlimited_zero() -> None:
    assert parse_mem_limit("") == 0


@pytest.mark.parametrize("bad", ["4gb", "4", "abc", "4G", "4 g", "g4", "-4g"])
def test_parse_mem_limit_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_mem_limit(bad)


def test_collect_service_mem_core_profile_sums_and_sorts() -> None:
    rows = collect_service_mem(profiles=["core"])
    assert rows, "core profile must contain at least one service"
    # Each row's bytes is exactly parse_mem_limit of its raw limit (mechanism).
    for r in rows:
        assert r.bytes == parse_mem_limit(r.mem_limit)
    # Sorted descending by bytes, then name (stable mechanism, no magic count).
    assert list(rows) == sorted(rows, key=lambda r: (-r.bytes, r.name))


def test_collect_service_mem_dedupes_across_profiles() -> None:
    # qdrant/redis/postgres span multiple profiles; the union must dedupe.
    rows = collect_service_mem(profiles=["rag", "observability"])
    names = [r.name for r in rows]
    assert len(names) == len(set(names)), "service appears more than once across profiles"


def test_collect_service_mem_by_explicit_services() -> None:
    rows = collect_service_mem(services=["qdrant"])
    assert [r.name for r in rows] == ["qdrant"]


def test_collect_service_mem_unknown_profile_raises() -> None:
    with pytest.raises(ValueError):
        collect_service_mem(profiles=["does-not-exist"])


def test_collect_service_mem_unknown_service_raises() -> None:
    with pytest.raises(ValueError, match="unknown service"):
        collect_service_mem(services=["nope-not-a-service"])


def test_estimate_memory_total_equals_row_sum() -> None:
    est = estimate_memory(profiles=["core"], ram_bytes=0, gtt_bytes=0)
    assert est.total_bytes == sum(r.bytes for r in est.services)


def test_estimate_memory_over_ram_and_gtt_flags() -> None:
    # Inject a tiny host: any non-empty profile over-commits it.
    est = estimate_memory(profiles=["core"], ram_bytes=1 * GIB, gtt_bytes=1 * GIB)
    assert est.total_bytes > 1 * GIB
    assert est.over_ram is True
    assert est.over_gtt is True
    assert est.warnings, "over-commit must surface a warning"


def test_estimate_memory_fits_when_host_is_huge() -> None:
    est = estimate_memory(profiles=["core"], ram_bytes=10_000 * GIB, gtt_bytes=10_000 * GIB)
    assert est.over_ram is False
    assert est.over_gtt is False
    assert est.warnings == ()


def test_estimate_memory_unknown_host_never_flags_over() -> None:
    # ram/gtt == 0 means "unknown" — must not report over-commit on a guess.
    est = estimate_memory(profiles=["full"], ram_bytes=0, gtt_bytes=0)
    assert est.over_ram is False
    assert est.over_gtt is False


def test_estimate_memory_payload_shape() -> None:
    est = estimate_memory(profiles=["core"], ram_bytes=2 * GIB, gtt_bytes=1 * GIB)
    payload = est.to_payload()
    assert set(payload) >= {
        "profiles",
        "services",
        "total_bytes",
        "host",
        "over_ram",
        "over_gtt",
        "warnings",
    }
    assert payload["host"]["ram_bytes"] == 2 * GIB
    assert payload["host"]["gtt_bytes"] == 1 * GIB
    assert isinstance(payload["over_ram"], bool)
    assert payload["services"][0]["name"]
