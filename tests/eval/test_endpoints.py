"""Phase 18 (M11) — endpoint egress classifier (AI-SPEC §4.2).

The judge and retriever endpoints are OPERATOR-SUPPLIED, which opens an egress plane the A8
descriptor gate structurally cannot see (it inspects service-descriptor env keys and has no hook
into CLI code). This classifier is the enforcement, so its rules are asserted here as behaviour,
not documented as intent.

The allow-list is deliberately repo-owned rather than ``ipaddress.is_private``: that property
answers "not globally routable", we need "on my premises", and its table has shifted *within*
the 3.12 series (CVE-2024-4032). A hard product promise cannot rest on a CPython patch level.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


def _never_called(host: str) -> tuple[str, ...]:  # pragma: no cover - asserted not to run
    raise AssertionError(f"resolver must not be called for a literal IP (host={host!r})")


def _resolves_to(*addresses: str):
    return lambda _host: tuple(addresses)


# --- scheme -----------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "file:///etc/passwd", "127.0.0.1:8080"])
def test_non_http_scheme_is_refused(url: str) -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint(url, resolve=_resolves_to("127.0.0.1"))
    assert v.allowed is False
    assert v.reason == "bad-scheme"


# --- literal IPs: resolver must not be consulted -----------------------------------------


def test_literal_loopback_is_allowed_without_resolution() -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://127.0.0.1:8086/v1", resolve=_never_called)
    assert v.allowed is True
    assert v.reason == "private-ok"
    assert v.addresses == ("127.0.0.1",)


def test_literal_ipv6_loopback_is_allowed() -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://[::1]:6006/v1", resolve=_never_called)
    assert v.allowed is True
    assert v.reason == "private-ok"


# --- the deliberate exclusions (this is where is_private would have let us down) ----------


def test_zero_address_is_refused_even_though_stdlib_calls_it_private() -> None:
    """``0.0.0.0`` connects to localhost on Linux and ``is_private`` returns True for it —
    it is refused here on purpose, by allow-list rather than by luck."""
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://0.0.0.0:8086/v1", resolve=_never_called, allow_lan=True)
    assert v.allowed is False
    assert v.reason == "public-address"


@pytest.mark.parametrize(
    "address",
    [
        "100.64.1.1",  # CGNAT / Tailscale — excluded deliberately, not by stdlib accident
        "198.18.0.1",  # benchmark range
        "192.0.2.10",  # TEST-NET-1
        "203.0.113.5",  # TEST-NET-3
        "240.0.0.1",  # reserved
        "255.255.255.255",  # broadcast
        "8.8.8.8",  # plainly public
    ],
)
def test_addresses_outside_the_allow_list_are_refused(address: str) -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint(f"http://{address}:8000/v1", resolve=_never_called, allow_lan=True)
    assert v.allowed is False, f"{address} must not be treated as on-premises"
    assert v.reason == "public-address"


# --- LAN is opt-in ------------------------------------------------------------------------


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.5", "192.168.1.45", "169.254.10.1"])
def test_lan_requires_opt_in(address: str) -> None:
    from agmind.eval.endpoints import classify_endpoint

    url = f"http://{address}:8000/v1"
    assert classify_endpoint(url, resolve=_never_called).allowed is False
    assert classify_endpoint(url, resolve=_never_called).reason == "lan-requires-opt-in"

    opted = classify_endpoint(url, resolve=_never_called, allow_lan=True)
    assert opted.allowed is True
    assert opted.reason == "lan-opt-in"
    assert opted.lan_opt_in is True


def test_docker_bridge_networks_fall_inside_the_lan_range() -> None:
    """The host's docker networks live in 172.17/16 and 172.18/16, inside 172.16/12."""
    from agmind.eval.endpoints import classify_endpoint

    for address in ("172.17.0.1", "172.18.0.6", "172.23.0.6"):
        v = classify_endpoint(f"http://{address}:6006", resolve=_never_called, allow_lan=True)
        assert v.allowed is True, address


# --- hostnames --------------------------------------------------------------------------


def test_hostname_resolving_to_loopback_is_allowed() -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://phoenix.local:6006", resolve=_resolves_to("127.0.0.1"))
    assert v.allowed is True
    assert v.host == "phoenix.local"


def test_unresolvable_hostname_is_refused() -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://nowhere.invalid:6006", resolve=_resolves_to())
    assert v.allowed is False
    assert v.reason == "unresolvable"


def test_mixed_round_robin_fails_closed() -> None:
    """One public address among private ones must sink the whole verdict — a round-robin that
    is private on the check and public on the connect is exactly the hole this closes."""
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint(
        "http://sneaky.example:8000",
        resolve=_resolves_to("127.0.0.1", "10.0.0.5", "93.184.216.34"),
        allow_lan=True,
    )
    assert v.allowed is False
    assert v.reason == "public-address"
    assert len(v.addresses) == 3, "the verdict must record every address it saw"


def test_resolver_failure_is_refused_not_raised() -> None:
    from agmind.eval.endpoints import classify_endpoint

    def boom(_host: str) -> tuple[str, ...]:
        raise OSError("dns down")

    v = classify_endpoint("http://phoenix.local:6006", resolve=boom)
    assert v.allowed is False
    assert v.reason == "unresolvable"


def test_missing_host_is_refused() -> None:
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http:///v1", resolve=_never_called)
    assert v.allowed is False
    assert v.reason == "no-host"


# --- verdict surface ----------------------------------------------------------------------


def test_verdict_is_serialisable_and_carries_the_residual_warning() -> None:
    """A private address does NOT prove the machine behind it has no internet egress — the
    verdict must say so in its own words rather than bury it in docs."""
    from agmind.eval.endpoints import classify_endpoint

    v = classify_endpoint("http://192.168.1.45:8000/v1", resolve=_never_called, allow_lan=True)
    d = v.to_dict()
    assert d["allowed"] is True
    assert d["reason"] == "lan-opt-in"
    assert "does not prove" in v.residual_risk_note().lower()


def test_reasons_are_stable_slugs() -> None:
    """Tests and the gate assert on these; prose would rot."""
    from agmind.eval.endpoints import REASONS

    assert REASONS == frozenset(
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
