"""Post-install access CLI — re-view where to log in, with what, at any time.

    agmind endpoints [--json]      # SERVICE | URL | STATE  (no secrets)
    agmind open <service> [--open] # print the service URL (SSH-pipeable)
    agmind creds show [--show] [--json]  # logins + passwords (root-only, masked by default)

All three re-derive the access report live from the service descriptors + the rendered
``.env`` under ``--install-dir`` (default ``/opt/agmind``), so they stay correct even if
``credentials.txt`` is stale or absent. Only ``creds show`` ever reveals a password, and only
to root with ``--show``.
"""

from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
from pathlib import Path

import typer

from agmind.cli.ops_cmd import _running_compose_services
from agmind.core.env import parse_env_file_or_empty
from agmind.core.secrets import mask_value
from agmind.services.access import (
    _DOMAIN_PLACEHOLDER,
    AccessEntry,
    build_access_report,
    render_credentials_txt,
)
from agmind.services.renderer import load_descriptors

_DEFAULT_INSTALL_DIR = Path("/opt/agmind")


def _load_compose(install_dir: Path) -> dict[str, object] | None:
    """Parse the rendered (world-readable) compose, or None if absent/unreadable."""
    compose = install_dir / "docker-compose.yml"
    if not compose.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _deployed_services(install_dir: Path) -> set[str] | None:
    """Service names from the rendered compose, or None if there is no compose file."""
    data = _load_compose(install_dir)
    if data is None:
        return None
    services = data.get("services")
    return set(services) if isinstance(services, dict) else set()


def _recover_domain_from_compose(install_dir: Path) -> str | None:
    """Recover the install domain from the WORLD-READABLE compose, no ``.env`` needed.

    A non-root operator can't read root-owned ``/opt/agmind/.env``, so ``AGMIND_DOMAIN``
    is invisible and the report would otherwise fall back to the bogus ``agmind.dev``
    placeholder → every printed URL is unreachable (live-audit 2026-06-08 UX-1). The domain
    is NOT a secret: the renderer stamps it into the traefik ``Host(`sub.<domain>`)`` router
    rules of the compose. Each descriptor declares its host as ``sub.agmind.dev`` (placeholder);
    matching the rendered ``Host(`sub.<domain>`)`` back to that placeholder yields ``<domain>``.
    Returns None when no router rule is recoverable (→ caller keeps the placeholder).
    """
    import re

    data = _load_compose(install_dir)
    if data is None:
        return None
    services = data.get("services")
    if not isinstance(services, dict):
        return None
    descriptors = load_descriptors()
    # placeholder host (e.g. "auth.agmind.dev") per deployed service
    placeholder_hosts = {
        name: d.routing.host
        for name, d in descriptors.items()
        if name in services and d.routing is not None
    }
    host_re = re.compile(r"Host\(`([^`]+)`\)")
    for name in sorted(services):
        svc = services[name]
        if not isinstance(svc, dict):
            continue
        labels = svc.get("labels")
        placeholder = placeholder_hosts.get(name)
        if placeholder is None or not placeholder.endswith(f".{_DOMAIN_PLACEHOLDER}"):
            continue
        subdomain = placeholder[: -len(_DOMAIN_PLACEHOLDER) - 1]  # "auth.agmind.dev" -> "auth"
        # labels may be a list ("k=v") or a dict ({k: v})
        label_values: list[str] = []
        if isinstance(labels, dict):
            label_values = [str(v) for v in labels.values()]
        elif isinstance(labels, list):
            label_values = [str(v).split("=", 1)[1] for v in labels if "=" in str(v)]
        for value in label_values:
            for rendered_host in host_re.findall(value):
                host_str = str(rendered_host)
                if host_str.startswith(f"{subdomain}."):
                    return host_str[len(subdomain) + 1 :]
    return None


def _llm_disabled_consumers(install_dir: Path) -> list[str]:
    """Deployed services that consume `llm_inference` while NO llama-llm is deployed.

    When the operator installs with model_id=skip there is no llama-llm, so openwebui's chat
    backend and ragflow's/dify's generation path point at nothing. Surface that instead of
    leaving it silent (live-audit 2026-06-05 llm-skip-unsurfaced)."""
    deployed = _deployed_services(install_dir)
    if deployed is None or "llama-llm" in deployed:
        return []
    descriptors = load_descriptors()
    return sorted(
        name
        for name in deployed
        if (d := descriptors.get(name)) is not None and "llm_inference" in d.consumes
    )


def _load_report(install_dir: Path) -> list[AccessEntry]:
    env_path = install_dir / ".env"
    # `endpoints` shows no secrets — degrade gracefully if .env is root-owned + unreadable
    # (running as a non-root user) instead of crashing with a traceback.
    env = parse_env_file_or_empty(env_path)
    # The domain is NOT a secret. When .env is unreadable (root-owned 0600, non-root caller)
    # recover it from the world-readable compose so URLs show the REAL domain, not the
    # `agmind.dev` placeholder (live-audit 2026-06-08 UX-1).
    domain = (
        env.get("AGMIND_DOMAIN") or _recover_domain_from_compose(install_dir) or _DOMAIN_PLACEHOLDER
    )
    descriptors = load_descriptors()
    deployed = _deployed_services(install_dir)
    if deployed is not None:
        descriptors = {n: d for n, d in descriptors.items() if n in deployed}
    return build_access_report(descriptors, env, domain=domain)


def _kind_tag(entry: AccessEntry) -> str:
    """A short ``KIND`` column value so an operator can tell an OpenAI API from a web UI at a
    glance (live-audit 2026-06-08 M3). Model endpoints are bare ``/v1`` APIs, not browsable."""
    if entry.is_model_endpoint:
        model = f", model: {entry.model_name}" if entry.model_name else ""
        return f"[OpenAI API → /v1{model}]"
    return "[UI]"


def _password_display(entry: AccessEntry, *, reveal: bool) -> str:
    if entry.password is not None:
        return entry.password if reveal else mask_value(entry.password)
    if entry.first_login_register:
        return "(create on first login)"
    if entry.password_env:
        return f"(see {entry.password_env} in .env)"
    return "(none)"


def register(app: typer.Typer) -> None:
    """Attach the access commands (`endpoints`, `open`, `creds show`) to ``app``."""

    @app.command("endpoints")
    def endpoints(
        install_dir: Path = typer.Option(_DEFAULT_INSTALL_DIR, "--install-dir"),
        json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
    ) -> None:
        """List reachable services: SERVICE | URL | KIND | STATE (no secrets)."""
        report = _load_report(install_dir)
        running = set(_running_compose_services(install_dir))
        disabled = set(_llm_disabled_consumers(install_dir))
        rows = [
            {
                "service": e.service,
                "url": e.url,
                "kind": _kind_tag(e),
                "state": "running" if e.service in running else "stopped",
                "llm_disabled": e.service in disabled,
            }
            for e in report
        ]
        if json_out:
            typer.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        elif not rows:
            typer.echo("No published services found.")
        else:
            svc_w = max(len(r["service"]) for r in rows)  # type: ignore[arg-type]
            url_w = max(len(r["url"]) for r in rows)  # type: ignore[arg-type]
            kind_w = max(len(r["kind"]) for r in rows)  # type: ignore[arg-type]
            for r in rows:
                # Inline tag so the LLM-disabled note survives piping (L3) and is co-located.
                tag = "  (LLM disabled)" if r["llm_disabled"] else ""
                typer.echo(
                    f"{r['service']:<{svc_w}}  {r['url']:<{url_w}}  "
                    f"{r['kind']:<{kind_w}}  {r['state']}{tag}"
                )
            typer.echo(f"\nFull credential file: {install_dir / 'credentials.txt'} (chmod 600)")
        if disabled:
            typer.echo(
                "\n⚠ no LLM deployed (model skipped at install) — chat/generation is disabled "
                f"for: {', '.join(sorted(disabled))}. Re-run install with a model (not "
                "--model-id skip) to enable it.",
                err=True,
            )

    @app.command("open")
    def open_service(
        service: str = typer.Argument(..., help="service name (see `agmind endpoints`)"),
        install_dir: Path = typer.Option(_DEFAULT_INSTALL_DIR, "--install-dir"),
        launch: bool = typer.Option(
            False, "--open", help="launch a browser (xdg-open) if a display is available"
        ),
    ) -> None:
        """Print a service's URL (pipeable over SSH); optionally open it in a browser."""
        report = _load_report(install_dir)
        match = next((e for e in report if e.service == service), None)
        if match is None:
            available = ", ".join(e.service for e in report) or "<none published>"
            typer.echo(
                f"unknown or unpublished service: {service}\navailable: {available}", err=True
            )
            raise typer.Exit(code=1)
        typer.echo(match.url)
        if launch and os.environ.get("DISPLAY") and shutil.which("xdg-open"):
            subprocess.run(["xdg-open", match.url], check=False)

    creds_app = typer.Typer(
        help="Service credentials (root-only). Re-derived live from .env + descriptors, so it "
        "stays correct even if credentials.txt is stale."
    )

    @creds_app.command("show")
    def creds_show(
        install_dir: Path = typer.Option(_DEFAULT_INSTALL_DIR, "--install-dir"),
        show: bool = typer.Option(False, "--show", help="reveal passwords (default: masked)"),
        json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
    ) -> None:
        """Show logins/passwords + operator notes + model endpoints from the rendered .env.

        Root-only (it reads secrets). Passwords are masked unless ``--show`` is passed. This is
        the same sectioned view as ``credentials.txt`` (single source of truth).
        """
        if os.geteuid() != 0:
            typer.echo("agmind creds show requires root (it reads secrets from .env).", err=True)
            raise typer.Exit(code=1)
        report = _load_report(install_dir)
        creds = [
            e
            for e in report
            if e.login or e.password or e.first_login_register or e.is_model_endpoint or e.note
        ]
        if json_out:
            out = [
                {
                    "service": e.service,
                    "url": e.url,
                    "login": e.login,
                    "password": _password_display(e, reveal=show),
                    "note": e.note,
                    "api_kind": e.api_kind,
                    "model_name": e.model_name,
                    # in-stack URL Dify/other containers must call (None for plain UIs); the public
                    # `url` is auth-gated and won't work as a model-provider endpoint.
                    "internal_url": e.internal_url,
                }
                for e in creds
            ]
            typer.echo(_json.dumps(out, indent=2, ensure_ascii=False))
            return
        if not creds:
            typer.echo("No managed credentials found.")
            return
        # Render the SAME sectioned report as credentials.txt (notes + model endpoints), masked
        # by default — so `creds show` and credentials.txt can never diverge (live-audit UX-3/H2).
        typer.echo(render_credentials_txt(creds, mask=not show, header=False).rstrip("\n"))
        if not show and any(e.password for e in creds):
            typer.echo("\n(passwords masked — re-run with --show to reveal)")
        typer.echo(f"\nFull credential file: {install_dir / 'credentials.txt'} (chmod 600)")

    @creds_app.command("refresh")
    def creds_refresh(
        install_dir: Path = typer.Option(_DEFAULT_INSTALL_DIR, "--install-dir"),
    ) -> None:
        """Regenerate credentials.txt (chmod 600) from the live descriptors + .env.

        credentials.txt is written once at install and never refreshed, so it drifts when a
        descriptor or .env changes (stale model name, missing notes). This rewrites it from the
        current state using the SAME renderer the installer uses. Root-only (reads .env).
        """
        from datetime import UTC, datetime

        from agmind.core.env import parse_env_file
        from agmind.core.secrets import write_private_text

        if os.geteuid() != 0:
            typer.echo(
                "agmind creds refresh requires root (it reads .env and writes credentials.txt).",
                err=True,
            )
            raise typer.Exit(code=1)
        env_path = install_dir / ".env"
        if not env_path.exists():
            typer.echo(f"no .env at {env_path} — is this a deployed install dir?", err=True)
            raise typer.Exit(code=1)
        try:
            env = parse_env_file(env_path)
        except (PermissionError, OSError) as exc:
            typer.echo(f"cannot read {env_path} ({exc}) — re-run with sudo.", err=True)
            raise typer.Exit(code=1) from exc
        domain = (
            env.get("AGMIND_DOMAIN")
            or _recover_domain_from_compose(install_dir)
            or _DOMAIN_PLACEHOLDER
        )
        descriptors = load_descriptors()
        deployed = _deployed_services(install_dir)
        if deployed is not None:
            descriptors = {n: d for n, d in descriptors.items() if n in deployed}
        report = build_access_report(descriptors, env, domain=domain)
        generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = render_credentials_txt(report, generated_at=generated_at)
        creds_path = install_dir / "credentials.txt"
        write_private_text(creds_path, text)
        typer.echo(f"✓ rewrote {creds_path} ({len(report)} endpoints, chmod 600)")

    app.add_typer(creds_app, name="creds")
