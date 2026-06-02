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


_DOMAIN_PLACEHOLDER = "agmind.dev"


def build_access_report(
    descriptors: Mapping[str, ServiceDescriptor],
    env: Mapping[str, str],
    *,
    domain: str | None = None,
) -> list[AccessEntry]:
    """Build the access report from descriptors + a rendered ``.env`` mapping.

    Includes every service that is published through Traefik (has ``routing.host``) so the
    operator gets a complete reachability list. Services without ``routing`` are internal-only
    and skipped. The optional ``access:`` block enriches an entry with login/password/flags;
    a routed service without ``access`` is reported url-only. Entries are sorted by service name.

    ``domain`` mirrors the renderer's domain substitution: descriptors hardcode the
    ``agmind.dev`` placeholder host, which the renderer rewrites to the install domain — so the
    report does the same to keep URLs correct on a non-``agmind.dev`` install.
    """
    entries: list[AccessEntry] = []
    for name in sorted(descriptors):
        descriptor = descriptors[name]
        routing = descriptor.routing
        if routing is None:
            continue  # internal-only service — no operator URL
        host = routing.host
        if domain and domain != _DOMAIN_PLACEHOLDER:
            host = host.replace(_DOMAIN_PLACEHOLDER, domain)
        url = f"https://{host}"
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


_REGISTER_HINT = "(create on first login)"


def _password_field(entry: AccessEntry) -> str | None:
    """Return the credentials.txt password line value, or None to omit the line."""
    if entry.password:
        return entry.password  # this IS the secrets file (chmod 600) — show the real value
    if entry.first_login_register:
        return _REGISTER_HINT
    if entry.password_env:
        return f"(see {entry.password_env} in .env)"
    if entry.login:
        return "(managed on host)"
    return None


def render_credentials_txt(
    report: list[AccessEntry],
    *,
    generated_at: str | None = None,
    llama_model: str | None = None,
    server_ip: str | None = None,
) -> str:
    """Render the sectioned, human-readable ``credentials.txt`` body (written ``chmod 600``).

    Shows real passwords (it is the secrets file). UI logins and OpenAI-compatible model
    endpoints (copy-paste "Add Model" blocks) get separate sections.
    """
    lines = ["# AGmind credentials — DO NOT COMMIT"]
    if generated_at:
        lines.append(f"# generated: {generated_at}")

    logins = [e for e in report if not e.is_model_endpoint]
    endpoints = [e for e in report if e.is_model_endpoint]

    if logins:
        lines += ["", "== Logins =="]
        for e in logins:
            lines.append(f"{e.service}   {e.url}")
            if e.login:
                lines.append(f"  Login:    {e.login}")
            elif e.first_login_register:
                lines.append(f"  Login:    {_REGISTER_HINT}")
            pw = _password_field(e)
            if pw is not None:
                lines.append(f"  Password: {pw}")
            if e.lan_only and server_ip:
                lines.append(
                    f"  LAN-only — SSH tunnel: ssh -L PORT:127.0.0.1:PORT <user>@{server_ip}"
                )
            lines.append("")

    if endpoints:
        lines += ["== Model endpoints (OpenAI-compatible — Dify → Model Provider → OpenAI-API) =="]
        for e in endpoints:
            lines.append(e.service)
            lines.append(f"  API endpoint URL: {e.url}/v1")
            lines.append(f"  Model name:       {llama_model or '(your model file)'}")
            lines.append("  API Key:          none")
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_endpoint_lines(report: list[AccessEntry]) -> list[str]:
    """Render endpoint lines for the post-install summary — URL + login hint, NEVER a password."""
    lines: list[str] = []
    for e in report:
        if e.is_model_endpoint:
            hint = "OpenAI API"
        elif e.login:
            hint = f"login: {e.login}"
        elif e.first_login_register:
            hint = "create account on first login"
        else:
            hint = "open"
        lan = " — LAN-only (ssh -L)" if e.lan_only else ""
        lines.append(f"  {e.url}   {e.service} ({hint}){lan}")
    return lines
