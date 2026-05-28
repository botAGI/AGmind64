"""Phase H'.E: `agmind service` CLI subcommands.

    agmind service list                                # все сервисы
    agmind service status [<name>]                     # tier breakdown / детали
    agmind service validate [<name>]                   # JSON Schema check
    agmind service scaffold <name> --tier <T>          # новый descriptor из шаблона

Под капотом: `agmind.schemas.ServiceDescriptor` + `agmind.services.renderer`.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import typer
import yaml

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import DEFAULT_SERVICES_DIR, load_descriptors


def cmd_list(services_dir: Path = DEFAULT_SERVICES_DIR) -> int:
    """Print список всех service descriptors."""
    descriptors = load_descriptors(services_dir)
    if not descriptors:
        print(f"No services found in {services_dir}", file=sys.stderr)
        return 1
    width = max(len(n) for n in descriptors) + 2
    print(f"{'NAME':<{width}} {'TIER':<10} {'PROFILES':<25} IMAGE")
    print("-" * (width + 50))
    for name in sorted(descriptors):
        d = descriptors[name]
        profiles = ",".join(d.profiles) or "(none)"
        print(f"{name:<{width}} {d.tier:<10} {profiles:<25} {d.image}")
    return 0


def cmd_status(
    name: str | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> int:
    """Print tier breakdown или детали одного сервиса."""
    descriptors = load_descriptors(services_dir)
    if not descriptors:
        print(f"No services found in {services_dir}", file=sys.stderr)
        return 1

    if name is None:
        # Aggregate by tier
        tier_count = Counter(d.tier for d in descriptors.values())
        print(f"Total services: {len(descriptors)}")
        print()
        for tier, count in sorted(tier_count.items()):
            print(f"  {tier:<10} {count} services")
        public = [n for n, d in descriptors.items() if d.routing is not None]
        scrape = [n for n, d in descriptors.items() if d.observability.prometheus_scrape]
        print()
        print(f"  routing (public via Traefik):  {len(public)} — {', '.join(sorted(public))}")
        print(f"  prometheus_scrape:             {len(scrape)} — {', '.join(sorted(scrape))}")
        return 0

    if name not in descriptors:
        print(f"Service '{name}' not found. Run `agmind service list`.", file=sys.stderr)
        return 1
    d = descriptors[name]
    print(f"Name:    {d.name}")
    print(f"Image:   {d.fq_image()}")
    print(f"Tier:    {d.tier}")
    print(f"Owner:   {d.owner}")
    print(f"Purpose: {d.purpose}")
    print(f"Profiles: {', '.join(d.profiles) or '(none)'}")
    if d.routing:
        print(
            f"Routing: https://{d.routing.host} (chain={d.routing.middleware_chain}, sse={d.routing.sse})"
        )
    else:
        print("Routing: (internal-only)")
    if d.observability.prometheus_scrape:
        print(f"Metrics: scraped at {d.observability.metrics_path}")
    print(f"Logs:    {'tailed by Loki' if d.observability.loki_scrape else 'not collected'}")
    return 0


def cmd_validate(
    name: str | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> int:
    """Валидировать descriptors против Pydantic schema."""
    if name is not None:
        path = services_dir / f"{name}.yaml"
        if not path.exists():
            print(f"File {path} not found", file=sys.stderr)
            return 1
        targets = [path]
    else:
        targets = sorted(services_dir.glob("*.yaml"))

    if not targets:
        print(f"No descriptors in {services_dir}", file=sys.stderr)
        return 1

    errors = 0
    for path in targets:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            ServiceDescriptor.model_validate(data)
            print(f"✓ {path.name}")
        except Exception as exc:
            print(f"✗ {path.name}: {exc}", file=sys.stderr)
            errors += 1
    if errors:
        print(f"\n{errors} validation error(s)", file=sys.stderr)
        return 1
    return 0


_SCAFFOLD_TEMPLATE = """# yaml-language-server: $schema=../schemas/service.json
name: {name}
image: example/{name}:0.1.0
tier: {tier}
purpose: TODO — короткое описание чем занимается
profiles:
- core
ports:
- 127.0.0.1:8080:8080  # TODO: host:container port mapping
resources:
  cpus: 1.0
  mem_limit: 1g
health:
  test:
  - CMD
  - curl
  - -f
  - http://localhost:8080/health  # TODO: реальный healthcheck URL
"""

_TIERS: tuple[str, ...] = ("edge", "inference", "app", "storage", "ops")


def cmd_scaffold(
    name: str,
    tier: Literal["edge", "inference", "app", "storage", "ops"],
    services_dir: Path = DEFAULT_SERVICES_DIR,
    force: bool = False,
) -> int:
    """Сгенерировать новый templates/services/<name>.yaml из шаблона."""
    if tier not in _TIERS:
        print(f"Invalid tier '{tier}'. Choose: {', '.join(_TIERS)}", file=sys.stderr)
        return 1
    path = services_dir / f"{name}.yaml"
    if path.exists() and not force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    services_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(_SCAFFOLD_TEMPLATE.format(name=name, tier=tier), encoding="utf-8")
    print(f"✓ created {path}")
    print(f"  → review image/ports, затем `agmind service validate {name}`")
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``service`` command group to ``app``."""

    # ---- service subcommand group (Phase H'.E) ----
    service_app = typer.Typer(
        name="service",
        help="Manage service descriptors (templates/services/*.yaml)",
        no_args_is_help=True,
    )
    app.add_typer(service_app)

    @service_app.command("list")
    def service_list() -> None:
        """List all service descriptors with tier и profiles."""
        raise typer.Exit(code=cmd_list())

    @service_app.command("status")
    def service_status(
        name: str | None = typer.Argument(None, help="Service name (omit for summary)"),
    ) -> None:
        """Show tier breakdown или детали одного сервиса."""
        raise typer.Exit(code=cmd_status(name))

    @service_app.command("validate")
    def service_validate(
        name: str | None = typer.Argument(None, help="Service name (omit to validate all)"),
    ) -> None:
        """Validate descriptors against Pydantic schema."""
        raise typer.Exit(code=cmd_validate(name))

    @service_app.command("scaffold")
    def service_scaffold(
        name: str = typer.Argument(..., help="New service name (a-z0-9-)"),
        tier: str = typer.Option(..., "--tier", "-t", help="edge|inference|app|storage|ops"),
        force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
    ) -> None:
        """Scaffold new templates/services/<name>.yaml из шаблона."""
        raise typer.Exit(code=cmd_scaffold(name, tier, force=force))  # type: ignore[arg-type]
