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
    model_name: str | None = None  # for model endpoints: the id llama-server reports at /v1/models
    note: str | None = None  # optional operator hint (e.g. a first-login caveat / recovery command)
    internal_url: str | None = (
        None  # model endpoints: in-stack docker URL (http://svc:port) for Dify
    )

    @property
    def is_model_endpoint(self) -> bool:
        """True if the service exposes a machine API (e.g. OpenAI-compatible) rather than a UI."""
        return self.api_kind is not None


_DOMAIN_PLACEHOLDER = "agmind.dev"


def _resolve_model_name(descriptor: ServiceDescriptor, env: Mapping[str, str]) -> str | None:
    """The model id an OpenAI-compatible llama-server reports at ``/v1/models`` is the basename of its
    ``--model`` arg (e.g. ``bge-m3-Q8_0.gguf``) — exactly what the operator must paste into Dify's
    "Model name" field. Parse it from the descriptor command, resolving any ``${VAR:-default}`` against
    the rendered env, so the report shows the real name instead of a "(your model file)" placeholder.
    Returns None for services whose command has no ``--model`` (i.e. non-model endpoints)."""
    from pathlib import PurePosixPath

    from agmind.install.secrets_audit import resolve_env_value

    command = list(descriptor.command or [])
    for i, token in enumerate(command):
        if token in ("--model", "-m") and i + 1 < len(command):
            resolved = resolve_env_value(str(command[i + 1]), env)
            return PurePosixPath(resolved).name or None
    return None


def _internal_model_url(descriptor: ServiceDescriptor) -> str | None:
    """The in-stack OpenAI-API base URL a co-deployed container (Dify, openwebui) must call to
    reach this model: ``http://<service>:<container-port>``.

    Docker's embedded DNS resolves the compose service name on the shared ``default`` network, so
    a container talks to the model DIRECTLY — no DNS record, no TLS, and crucially NO Authelia. The
    public ``https://<host>`` route sits behind the chain-llm Authelia middleware, which 302s every
    unauthenticated API call → pasting it into Dify never connects (live-audit 2026-06-13). The
    container port is ``routing.port`` when set, else the container side of the first ``ports``
    mapping (``[ip:]host:container`` → ``container``). Returns None when neither is discoverable, so
    the caller falls back to the public URL."""
    routing = descriptor.routing
    port = routing.port if routing and routing.port else None
    if port is None:
        for spec in descriptor.ports:
            container = spec.split("/", 1)[0].rsplit(":", 1)[-1]  # drop /proto, take container side
            if container.isdigit():
                port = int(container)
                break
    if port is None:
        return None
    return f"http://{descriptor.name}:{port}"


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
        # Substitute the `<domain>` placeholder in operator notes with the resolved install
        # domain so the rendered hint is copy-paste-ready (e.g. `portainer.lab.agmind.dev`,
        # not the literal `portainer.<domain>`). live-audit 2026-06-08 (M5).
        note = access.note
        if note is not None:
            note = note.replace("<domain>", domain or _DOMAIN_PLACEHOLDER)
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
                model_name=_resolve_model_name(descriptor, env) if access.api_kind else None,
                note=note,
                internal_url=_internal_model_url(descriptor) if access.api_kind else None,
            )
        )
    return entries


_REGISTER_HINT = "(create on first login)"


def _password_field(entry: AccessEntry, *, mask: bool = False) -> str | None:
    """Return the credentials password line value, or None to omit the line.

    When ``mask`` is True the real secret is masked (``creds show`` default — a shoulder-surf
    guard); otherwise the real value is shown (``credentials.txt`` IS the secrets file).
    """
    from agmind.core.secrets import mask_value

    if entry.password:
        return mask_value(entry.password) if mask else entry.password
    if entry.first_login_register:
        return _REGISTER_HINT
    if entry.password_env:
        return f"(see {entry.password_env} in .env)"
    if entry.login:
        return "(managed on host)"
    return None


def _derive_domain(report: list[AccessEntry]) -> str:
    """Best-effort install domain from the report's service URLs (e.g. ``grafana.lab.agmind.dev`` →
    ``lab.agmind.dev``) for the credentials.txt DNS reminder. Falls back to a generic placeholder."""
    for entry in report:
        host = entry.url.split("://", 1)[-1].split("/", 1)[0]
        labels = host.split(".")
        if len(labels) > 2:
            return ".".join(labels[1:])
    return "<your-domain>"


def render_credentials_txt(
    report: list[AccessEntry],
    *,
    generated_at: str | None = None,
    llama_model: str | None = None,
    server_ip: str | None = None,
    mask: bool = False,
    header: bool = True,
) -> str:
    """Render the sectioned, human-readable credentials body — the SINGLE source of truth for
    both ``credentials.txt`` (written ``chmod 600``) and ``agmind creds show``.

    Shows real passwords by default (the file is the secrets file); ``mask=True`` masks them for
    ``creds show``. UI logins, per-service operator notes, and OpenAI-compatible model endpoints
    (copy-paste "Add Model" blocks) get separate sections. ``header=False`` drops the
    ``# DO NOT COMMIT`` banner (irrelevant for terminal output).
    """
    lines: list[str] = []
    if header:
        lines.append("# AGmind credentials — DO NOT COMMIT")
        if generated_at:
            lines.append(f"# generated: {generated_at}")
        # DNS reminder: every service URL below is a subdomain that must resolve to this host. The
        # operator hits NXDOMAIN ("site can't be reached") on any subdomain with no DNS record — the
        # #1 "service is dead" support gripe. A single wildcard covers all current + future services.
        _dns_domain = _derive_domain(report)
        _dns_ip = server_ip or "<server-ip>"
        lines.append(
            f"# ⚠️ DNS: add a wildcard `*.{_dns_domain} -> {_dns_ip}` to your LOCAL DNS "
            f"(AdGuard/Pi-hole/router) — or per-host lines in your OS hosts file — or the browser "
            f"gets NXDOMAIN and the service looks dead."
        )

    logins = [e for e in report if not e.is_model_endpoint]
    endpoints = [e for e in report if e.is_model_endpoint]

    if logins:
        if lines:
            lines.append("")
        lines.append("== Logins ==")
        for e in logins:
            lines.append(f"{e.service}   {e.url}")
            if e.login:
                lines.append(f"  Login:    {e.login}")
            elif e.first_login_register:
                lines.append(f"  Login:    {_REGISTER_HINT}")
            pw = _password_field(e, mask=mask)
            if pw is not None:
                lines.append(f"  Password: {pw}")
            if e.lan_only and server_ip:
                lines.append(
                    f"  LAN-only — SSH tunnel: ssh -L PORT:127.0.0.1:PORT <user>@{server_ip}"
                )
            if e.note:
                lines.append(f"  Note:     {e.note}")
            lines.append("")

    if endpoints:
        lines += ["== Model endpoints (OpenAI-compatible) =="]
        lines.append(
            "# In Dify: Settings → Model Provider → OpenAI-API-compatible. Paste the API endpoint"
        )
        lines.append(
            "# URL below — it is the in-stack docker address, so Dify reaches the model container"
        )
        lines.append(
            "# directly (no DNS record, no TLS, no Authelia login, unlike the browser URL)."
        )
        for e in endpoints:
            lines.append(e.service)
            # The in-stack docker URL is the one that actually works from a co-deployed container.
            # The public https:// route is behind the chain-llm Authelia middleware (302s API
            # calls) + needs DNS/TLS, so it is a SECONDARY host/LAN line, never the primary one.
            base = e.internal_url or e.url
            lines.append(f"  API endpoint URL: {base}/v1")
            lines.append(
                f"  Model name:       {e.model_name or llama_model or '(your model file)'}"
            )
            lines.append("  API Key:          none")
            if e.internal_url:
                lines.append(
                    f"  Host/LAN clients (outside docker): {e.url}/v1 — behind Authelia login"
                )
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_endpoint_lines(report: list[AccessEntry]) -> list[str]:
    """Render endpoint lines for the post-install summary — URL + login hint, NEVER a password."""
    lines: list[str] = []
    for e in report:
        if e.is_model_endpoint:
            hint = f"OpenAI API — model: {e.model_name}" if e.model_name else "OpenAI API"
        elif e.login:
            hint = f"login: {e.login}"
        elif e.first_login_register:
            hint = "create account on first login"
        else:
            hint = "open"
        lan = " — LAN-only (ssh -L)" if e.lan_only else ""
        lines.append(f"  {e.url}   {e.service} ({hint}){lan}")
    return lines
