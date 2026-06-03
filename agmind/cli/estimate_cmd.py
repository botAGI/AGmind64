"""`agmind estimate` — sum profile mem_limit caps vs host RAM / GTT pool.

Registration only; the heavy compute/detect import stays lazy inside the
command body so building the app does not probe hardware.
"""

from __future__ import annotations

import json
import sys

import typer


def register(app: typer.Typer) -> None:
    """Attach the ``estimate`` command to ``app``."""

    @app.command()
    def estimate(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --services is used)",
        ),
        services: str | None = typer.Option(
            None,
            "--services",
            "-s",
            help="Comma-separated explicit service names (overrides --profile)",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        ram: float | None = typer.Option(
            None, "--ram", help="Override system RAM in GiB (default: autodetect)"
        ),
        gtt: float | None = typer.Option(
            None, "--gtt", help="Override GPU GTT pool in GiB (default: autodetect)"
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help="Exit non-zero when the cap sum over-commits RAM or GTT",
        ),
    ) -> None:
        """Estimate the memory ceiling of a profile/service set vs this host.

        Sums each service's mem_limit (a hard CAP, not a reservation — the total
        is a worst-case ceiling) and compares it to system RAM and the GPU GTT
        pool. GTT is the real limit for GPU work on Strix Halo unified memory.
        """
        from agmind.diagnostics.estimate import GIB, estimate_memory

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        service_names = [s.strip() for s in services.split(",") if s.strip()] if services else []

        # Resolve host figures: explicit overrides win, otherwise autodetect.
        # A missing GPU / non-Strix host yields gtt_bytes=0 ("unknown").
        ram_bytes = int(ram * GIB) if ram is not None else 0
        gtt_bytes = int(gtt * GIB) if gtt is not None else 0
        if ram is None or gtt is None:
            from agmind.compute.detect import detect_host

            host = detect_host()
            if ram is None:
                ram_bytes = host.system_ram_bytes
            if gtt is None:
                gtt_bytes = host.gpu.gtt_total_bytes if host.gpu is not None else 0

        try:
            est = estimate_memory(
                profiles=profiles,
                services=service_names,
                ram_bytes=ram_bytes,
                gtt_bytes=gtt_bytes,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise typer.Exit(code=2) from None

        if as_json:
            typer.echo(json.dumps(est.to_payload(), indent=2, ensure_ascii=False))
        else:
            _print_human(est)

        if strict and (est.over_ram or est.over_gtt):
            raise typer.Exit(code=1)

    def _print_human(est) -> None:  # type: ignore[no-untyped-def]
        from agmind.diagnostics.estimate import GIB

        def gib(n: int) -> str:
            return f"{n / GIB:.1f}g" if n else "—"

        selector = (
            "services=" + ",".join(s.name for s in est.services)
            if not est.profiles
            else "profile=" + ",".join(est.profiles)
        )
        typer.echo(f"Memory estimate ({selector}):")
        typer.echo(f"  {'SERVICE':<24} MEM_LIMIT")
        for row in est.services:
            typer.echo(f"  {row.name:<24} {row.mem_limit or 'unlimited'}")
        typer.echo(f"  {'-' * 36}")
        typer.echo(f"  {'TOTAL':<24} {gib(est.total_bytes)}")
        typer.echo(f"  vs system RAM {gib(est.ram_bytes)}" + ("  [OVER]" if est.over_ram else ""))
        typer.echo(f"  vs GPU GTT    {gib(est.gtt_bytes)}" + ("  [OVER]" if est.over_gtt else ""))
        for warn in est.warnings:
            typer.echo(f"  ! {warn}")
        typer.echo("  note: mem_limit is a hard cap, not a reservation — this is a ceiling.")
