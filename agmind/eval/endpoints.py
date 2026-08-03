"""Endpoint egress classifier for operator-supplied eval endpoints (AI-SPEC §4.2).

The eval harness talks to endpoints the operator configures — a retriever and (in Phase 2a) an
LLM judge. That is a new egress plane, and the existing A8 gate cannot see it: A8 inspects the
``env`` keys of service descriptors and has no hook into CLI code. The precedent for the hole is
already in the repo (``agmind/loadtest/k6.py`` carries a DEFAULT_ENDPOINT that passes every
gate). So zero-egress here is enforced structurally: clients take a verdict, not a URL.

**Why an explicit allow-list rather than ``ipaddress.is_private``** — two independent reasons:

1. *Version contingency.* ``requires-python = ">=3.12"``, and the ``ipaddress`` classification
   tables changed *within* the 3.12 series after CVE-2024-4032 (``100.64.0.0/10`` moved out of
   the private set, ``_private_networks_exceptions`` appeared). A hard product promise must not
   depend on the interpreter's patch level — the same test would pass on one 3.12 and silently
   permit on another that still satisfies our declared floor.
2. *Wrong predicate shape.* ``is_private`` answers "not globally routable"; we need "on my
   premises". On 3.12.3 it returns True for ``0.0.0.0/8``, ``198.18.0.0/15``, ``240.0.0.0/4``,
   ``192.0.2.0/24``, ``203.0.113.0/24`` and ``255.255.255.255/32``. ``http://0.0.0.0:8086``
   connects to localhost on Linux and would have sailed through. CGNAT/Tailscale
   (``100.64.0.0/10``) is excluded here *deliberately and visibly*, not by an accident of a
   stdlib table.

Out of scope, recorded rather than hidden: DNS rebinding (a public name that resolves privately
during the check and publicly at connect time) is not defended against. Loopback and literal-IP
configurations — the overwhelming majority — are immune by construction, and the client re-checks
the resolved address immediately before the request rather than trusting a stale pre-flight.

A private address also does not *prove* the machine behind it lacks internet egress; a judge on
the LAN could forward every prompt and chunk to a cloud API. ``residual_risk_note()`` exists so
that caveat is printed to the operator in words, not buried in documentation.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

#: Resolve a hostname to zero or more textual IP addresses. Injected so the unit tier never
#: performs DNS (the CI lane is hermetic and offline).
Resolver = Callable[[str], tuple[str, ...]]

_LOOPBACK: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)

#: On-premises ranges, allowed only with an explicit ``--allow-lan`` style opt-in. Includes
#: 172.16/12, which is where this host's docker bridge networks live (172.17/16, 172.18/16).
_LAN: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

#: Stable slugs. Tests and the gate assert on these; prose would rot.
REASONS = frozenset(
    {
        "private-ok",
        "lan-opt-in",
        "lan-requires-opt-in",
        "public-address",
        "unresolvable",
        "bad-scheme",
        "no-host",
    }
)


@dataclass(frozen=True)
class EndpointVerdict:
    """The result of classifying one endpoint. Clients require this, never a bare URL."""

    url: str
    host: str
    addresses: tuple[str, ...]
    allowed: bool
    reason: str
    lan_opt_in: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "host": self.host,
            "addresses": list(self.addresses),
            "allowed": self.allowed,
            "reason": self.reason,
            "lan_opt_in": self.lan_opt_in,
        }

    def residual_risk_note(self) -> str:
        """The caveat the operator must read when a LAN endpoint is opted into."""
        return (
            "This endpoint is on a private address. That does not prove the machine behind it "
            "has no internet egress — a judge on your LAN could forward every prompt and every "
            "chunk of retrieved text to a cloud API. Zero-egress is only as strong as the host "
            "you point this at."
        )


def _in_any(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    return any(addr.version == net.version and addr in net for net in networks)


def classify_endpoint(
    url: str,
    *,
    resolve: Resolver,
    allow_lan: bool = False,
) -> EndpointVerdict:
    """Classify ``url`` as on-premises or not, fail-closed.

    A literal IP is classified directly and the resolver is never called. A hostname is resolved
    through the injected ``resolve``; ANY address outside the allow-list sinks the whole verdict,
    so a round-robin that mixes a private and a public address cannot slip through.
    """
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        return EndpointVerdict(url, "", (), False, "bad-scheme")

    host = split.hostname or ""
    if not host:
        return EndpointVerdict(url, "", (), False, "no-host")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        addresses: tuple[str, ...] = (str(literal),)
    else:
        try:
            addresses = tuple(resolve(host))
        except OSError:
            return EndpointVerdict(url, host, (), False, "unresolvable")
        if not addresses:
            return EndpointVerdict(url, host, (), False, "unresolvable")

    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for text in addresses:
        try:
            parsed.append(ipaddress.ip_address(text))
        except ValueError:
            # An unparseable answer is not evidence of safety.
            return EndpointVerdict(url, host, addresses, False, "public-address")

    # Fail-closed across the whole address set, not on the first match.
    if any(not (_in_any(a, _LOOPBACK) or _in_any(a, _LAN)) for a in parsed):
        return EndpointVerdict(url, host, addresses, False, "public-address")

    needs_lan = any(not _in_any(a, _LOOPBACK) for a in parsed)
    if needs_lan and not allow_lan:
        return EndpointVerdict(url, host, addresses, False, "lan-requires-opt-in")

    if needs_lan:
        return EndpointVerdict(url, host, addresses, True, "lan-opt-in", lan_opt_in=True)
    return EndpointVerdict(url, host, addresses, True, "private-ok")


__all__ = ["REASONS", "EndpointVerdict", "Resolver", "classify_endpoint"]
