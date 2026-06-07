"""OPT-1 (optimization audit 2026-06-08): every service with a hard ``mem_limit`` carries a
soft ``mem_reservation`` so Docker has a scheduling floor under memory pressure (not only an OOM
ceiling). Regression guard + renderer-emission proof on representative descriptors."""

from __future__ import annotations

import pytest

from agmind.diagnostics.estimate import parse_mem_limit
from agmind.services.renderer import descriptor_to_compose_service, load_descriptors

pytestmark = pytest.mark.backend_any


def test_every_mem_limit_has_a_reservation() -> None:
    """Catalog baseline: any descriptor that declares a hard mem_limit must also declare a
    soft mem_reservation (OPT-1). A new descriptor with a limit but no reservation fails here."""
    descriptors = load_descriptors()
    missing = sorted(
        name
        for name, d in descriptors.items()
        if d.resources.mem_limit and not d.resources.mem_reservation
    )
    assert not missing, (
        f"These services declare mem_limit but no mem_reservation (add a soft floor): {missing}"
    )


def test_reservation_never_exceeds_limit() -> None:
    """A reservation above the hard cap is a misconfig (Docker would reject / it makes no sense)."""
    descriptors = load_descriptors()
    violations: list[str] = []
    for name, d in descriptors.items():
        if not (d.resources.mem_limit and d.resources.mem_reservation):
            continue
        limit = parse_mem_limit(d.resources.mem_limit)
        reservation = parse_mem_limit(d.resources.mem_reservation)
        if reservation > limit:
            violations.append(
                f"{name}: mem_reservation {d.resources.mem_reservation} > "
                f"mem_limit {d.resources.mem_limit}"
            )
    assert not violations, "mem_reservation exceeds mem_limit:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("name", "expected_reservation"),
    [
        ("elasticsearch", "5g"),  # heap is the whole footprint (live 4.6 GiB)
        ("ragflow", "3g"),  # live ~2.6 GiB
        ("postgres", "256m"),  # live ~40 MiB — tiny floor
        ("redis", "512m"),
        ("node-exporter", "64m"),  # exporter — minimal scheduler signal
    ],
)
def test_representative_reservations_render(name: str, expected_reservation: str) -> None:
    """Representative descriptors emit the expected mem_reservation through the compose renderer."""
    d = load_descriptors()[name]
    assert d.resources.mem_reservation == expected_reservation
    svc = descriptor_to_compose_service(d)
    assert svc["mem_reservation"] == expected_reservation
    # the hard cap is still present and distinct
    assert "mem_limit" in svc
