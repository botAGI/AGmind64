"""Per-service access report — url / login / password / flags.

Combines the declarative ``access:`` block + ``routing.host`` from each
:class:`~agmind.schemas.ServiceDescriptor` with the secret *values* from the rendered
``.env`` into one in-memory model. This single source backs:

- the post-install summary (``agmind/cli/tui/summary_screen.py``),
- ``credentials.txt`` (written at install time),
- ``agmind endpoints`` / ``agmind open`` / ``agmind creds show``.

The report is derived **live** from descriptors + ``.env`` so it stays correct even if
``credentials.txt`` is stale or absent. Password values live in the entry only in memory;
callers decide whether to mask (summary/endpoints never print them; ``creds show`` masks
by default).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agmind.schemas import ServiceDescriptor


@dataclass(frozen=True)
class AccessEntry:
    """One operator-facing endpoint resolved from a descriptor + the rendered env."""

    service: str
    url: str
    login: str | None
    password: str | None
    password_env: str | None
    first_login_register: bool
    lan_only: bool
    api_kind: str | None

    @property
    def is_model_endpoint(self) -> bool:
        """True if the service exposes a machine API (e.g. OpenAI-compatible) rather than a UI."""
        return self.api_kind is not None


def build_access_report(
    descriptors: Mapping[str, ServiceDescriptor],
    env: Mapping[str, str],
) -> list[AccessEntry]:
    """Build the access report from descriptors + a rendered ``.env`` mapping.

    Includes every service that is published through Traefik (has ``routing.host``) so the
    operator gets a complete reachability list. Services without ``routing`` are internal-only
    and skipped. The optional ``access:`` block enriches an entry with login/password/flags;
    a routed service without ``access`` is reported url-only. Entries are sorted by service name.
    """
    entries: list[AccessEntry] = []
    for name in sorted(descriptors):
        descriptor = descriptors[name]
        routing = descriptor.routing
        if routing is None:
            continue  # internal-only service — no operator URL
        url = f"https://{routing.host}"
        access = descriptor.access
        if access is None:
            entries.append(
                AccessEntry(
                    service=name,
                    url=url,
                    login=None,
                    password=None,
                    password_env=None,
                    first_login_register=False,
                    lan_only=False,
                    api_kind=None,
                )
            )
            continue
        password = env.get(access.password_env) if access.password_env else None
        entries.append(
            AccessEntry(
                service=name,
                url=url,
                login=access.login,
                password=password,
                password_env=access.password_env,
                first_login_register=access.first_login_register,
                lan_only=access.lan_only,
                api_kind=access.api_kind,
            )
        )
    return entries
