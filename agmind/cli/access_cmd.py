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
from agmind.services.access import AccessEntry, build_access_report
from agmind.services.renderer import load_descriptors

_DEFAULT_INSTALL_DIR = Path("/opt/agmind")


def _deployed_services(install_dir: Path) -> set[str] | None:
    """Service names from the rendered compose, or None if there is no compose file."""
    compose = install_dir / "docker-compose.yml"
    if not compose.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        services = data.get("services") or {}
        return set(services)
    except Exception:  # noqa: BLE001 — fall back to "show all" on an unreadable compose
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
    domain = env.get("AGMIND_DOMAIN") or "agmind.dev"
    descriptors = load_descriptors()
    deployed = _deployed_services(install_dir)
    if deployed is not None:
        descriptors = {n: d for n, d in descriptors.items() if n in deployed}
    return build_access_report(descriptors, env, domain=domain)


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
        """List reachable services: SERVICE | URL | STATE (no secrets)."""
        report = _load_report(install_dir)
        running = set(_running_compose_services(install_dir))
        rows = [
            {
                "service": e.service,
                "url": e.url,
                "state": "running" if e.service in running else "stopped",
            }
            for e in report
        ]
        disabled = _llm_disabled_consumers(install_dir)
        if json_out:
            typer.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        elif not rows:
            typer.echo("No published services found.")
        else:
            svc_w = max(len(r["service"]) for r in rows)
            url_w = max(len(r["url"]) for r in rows)
            for r in rows:
                typer.echo(f"{r['service']:<{svc_w}}  {r['url']:<{url_w}}  {r['state']}")
        if disabled:
            typer.echo(
                "\n⚠ no LLM deployed (model skipped at install) — chat/generation is disabled "
                f"for: {', '.join(disabled)}. Re-run install with a model (not --model-id skip) "
                "to enable it.",
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
            typer.echo(f"unknown or unpublished service: {service}", err=True)
            raise typer.Exit(code=1)
        typer.echo(match.url)
        if launch and os.environ.get("DISPLAY") and shutil.which("xdg-open"):
            subprocess.run(["xdg-open", match.url], check=False)  # noqa: S603,S607

    creds_app = typer.Typer(help="Service credentials (root-only).")

    @creds_app.command("show")
    def creds_show(
        install_dir: Path = typer.Option(_DEFAULT_INSTALL_DIR, "--install-dir"),
        show: bool = typer.Option(False, "--show", help="reveal passwords (default: masked)"),
        json_out: bool = typer.Option(False, "--json", help="machine-readable output"),
    ) -> None:
        """Show service logins/passwords from the rendered .env (root-only, masked by default)."""
        if os.geteuid() != 0:
            typer.echo("agmind creds show requires root (it reads secrets from .env).", err=True)
            raise typer.Exit(code=1)
        report = _load_report(install_dir)
        creds = [e for e in report if e.login or e.password or e.first_login_register]
        if json_out:
            out = [
                {
                    "service": e.service,
                    "url": e.url,
                    "login": e.login,
                    "password": _password_display(e, reveal=show),
                }
                for e in creds
            ]
            typer.echo(_json.dumps(out, indent=2, ensure_ascii=False))
            return
        if not creds:
            typer.echo("No managed credentials found.")
            return
        for e in creds:
            typer.echo(f"{e.service}: {e.url}")
            if e.login:
                typer.echo(f"  login:    {e.login}")
            typer.echo(f"  password: {_password_display(e, reveal=show)}")

    app.add_typer(creds_app, name="creds")
