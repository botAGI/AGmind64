"""Memory-footprint estimation: profile mem_limit sum vs host RAM / GTT pool.

Sums the declared ``resources.mem_limit`` of every service in the selected
profiles (or explicit service set) and compares the total against the host's
system RAM **and** the GPU GTT pool. On AMD Strix Halo unified memory the GTT
pool — not system RAM — is the real ceiling for GPU work, so it is reported
separately (it is often roughly half of RAM here).

Important: ``mem_limit`` is a hard *cap*, not a reservation. The sum is a
worst-case ceiling, not expected steady-state usage. ``full`` legitimately
exceeds most hosts by design, which is why over-commit is informational unless
the caller opts into strict mode.

This module is pure and hardware-free: host figures (RAM, GTT) are passed in as
bytes, so callers (the CLI) resolve them from ``detect_host`` while tests inject
fixed values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agmind.services.registry import Service, list_services, services_for_profile

KIB = 1024
MIB = 1024**2
GIB = 1024**3

# Mirrors agmind.schemas.service._MEM_LIMIT_RE but captures the parts so the sum
# can be computed. The descriptor schema already guarantees this exact format.
_MEM_LIMIT_RE = re.compile(r"^(\d+)([kmg])$")
_UNIT_BYTES = {"k": KIB, "m": MIB, "g": GIB}


def parse_mem_limit(value: str) -> int:
    """Parse a descriptor ``mem_limit`` into bytes.

    ``""`` means "no limit" and contributes ``0`` to a sum. Any other value
    must match the strict lowercase ``\\d+[kmg]`` descriptor format; anything
    else (``4gb``, ``4G``, ``4``) raises ``ValueError``.
    """
    if value == "":
        return 0
    match = _MEM_LIMIT_RE.match(value)
    if match is None:
        raise ValueError(
            f"mem_limit '{value}' invalid: expected lowercase '\\d+[kmg]' (e.g. '4g', '512m')"
        )
    return int(match.group(1)) * _UNIT_BYTES[match.group(2)]


@dataclass(frozen=True)
class ServiceMem:
    """One service's declared memory cap."""

    name: str
    mem_limit: str
    bytes: int

    def to_payload(self) -> dict[str, object]:
        return {"name": self.name, "mem_limit": self.mem_limit, "bytes": self.bytes}


@dataclass(frozen=True)
class MemEstimate:
    """Aggregate memory estimate for a profile/service selection vs a host."""

    profiles: tuple[str, ...]
    services: tuple[ServiceMem, ...]
    total_bytes: int
    ram_bytes: int
    gtt_bytes: int
    warnings: tuple[str, ...]

    @property
    def over_ram(self) -> bool:
        """True only when RAM is known (>0) and the cap sum exceeds it."""
        return self.ram_bytes > 0 and self.total_bytes > self.ram_bytes

    @property
    def over_gtt(self) -> bool:
        """True only when the GTT pool is known (>0) and the sum exceeds it."""
        return self.gtt_bytes > 0 and self.total_bytes > self.gtt_bytes

    def to_payload(self) -> dict[str, object]:
        return {
            "profiles": list(self.profiles),
            "services": [s.to_payload() for s in self.services],
            "total_bytes": self.total_bytes,
            "host": {"ram_bytes": self.ram_bytes, "gtt_bytes": self.gtt_bytes},
            "over_ram": self.over_ram,
            "over_gtt": self.over_gtt,
            "warnings": list(self.warnings),
        }


def _service_mem(svc: Service) -> ServiceMem:
    return ServiceMem(name=svc.name, mem_limit=svc.mem_limit, bytes=parse_mem_limit(svc.mem_limit))


def collect_service_mem(
    *,
    profiles: tuple[str, ...] | list[str] = (),
    services: tuple[str, ...] | list[str] = (),
    path: str | None = None,
) -> tuple[ServiceMem, ...]:
    """Return the deduped per-service memory caps, sorted by bytes desc then name.

    ``services`` (explicit names) takes precedence over ``profiles``. Unknown
    profiles raise ``ValueError`` (via the profile enum); unknown service names
    raise ``ValueError`` with an "unknown service" message.
    """
    if services:
        by_name = {s.name: s for s in list_services(path)}
        unknown = sorted(set(services) - set(by_name))
        if unknown:
            raise ValueError("unknown services: " + ", ".join(unknown))
        chosen = {name: by_name[name] for name in services}
    else:
        chosen = {}
        for profile in profiles:
            for svc in services_for_profile(profile, path):
                chosen[svc.name] = svc

    rows = [_service_mem(svc) for svc in chosen.values()]
    return tuple(sorted(rows, key=lambda r: (-r.bytes, r.name)))


def estimate_memory(
    *,
    profiles: tuple[str, ...] | list[str] = (),
    services: tuple[str, ...] | list[str] = (),
    ram_bytes: int = 0,
    gtt_bytes: int = 0,
    path: str | None = None,
) -> MemEstimate:
    """Build a :class:`MemEstimate`. ``ram_bytes``/``gtt_bytes`` of 0 = unknown."""
    rows = collect_service_mem(profiles=profiles, services=services, path=path)
    total = sum(r.bytes for r in rows)

    warnings: list[str] = []
    if ram_bytes > 0 and total > ram_bytes:
        warnings.append(
            f"mem_limit sum {total / GIB:.1f} GiB exceeds system RAM {ram_bytes / GIB:.1f} GiB"
        )
    if gtt_bytes > 0 and total > gtt_bytes:
        warnings.append(
            f"mem_limit sum {total / GIB:.1f} GiB exceeds GPU GTT pool {gtt_bytes / GIB:.1f} GiB"
        )

    return MemEstimate(
        profiles=tuple(profiles),
        services=rows,
        total_bytes=total,
        ram_bytes=ram_bytes,
        gtt_bytes=gtt_bytes,
        warnings=tuple(warnings),
    )
