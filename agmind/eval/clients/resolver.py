"""DNS resolution — the other way the harness touches the network.

It lives here, in the clients package, because resolving a hostname IS network I/O: it consults
the resolver, it can be slow, and it can fail. Keeping it in the CLI put a ``socket.getaddrinfo``
call outside the egress chokepoint, which the AST guard correctly flagged the moment its scope
was widened to include the command module.

The function is injected into :func:`agmind.eval.endpoints.classify_endpoint` rather than called
by it, so the unit tier can classify endpoints with a stub and never perform DNS — the CI lane is
hermetic and offline.
"""

from __future__ import annotations

import socket


def resolve_host(host: str) -> tuple[str, ...]:
    """Resolve ``host`` to its addresses, or ``()`` when it cannot be resolved.

    Returns rather than raises: an unresolvable host is a verdict input ("unresolvable"), not an
    exceptional condition, and the classifier fails closed on an empty result.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return ()
    return tuple(sorted({str(info[4][0]) for info in infos}))


__all__ = ["resolve_host"]
