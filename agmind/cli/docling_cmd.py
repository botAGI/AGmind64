"""`agmind docling bench <pdf>` — cold/warm/per-page timing of docling presets.

Times sync POST /v1/convert/file across N iterations: run 1 is "cold" (model
load / first-call overhead), the mean of the rest is "warm". Records BOTH the
wall-clock and the server-reported ``processing_time`` (the latter is the
accurate per-conversion figure; wall-clock includes HTTP + serialization).

Registration only; the docling client import stays lazy in the command body.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import typer

from agmind.compute.clients.docling import DoclingClient

_DEFAULT_URL = "http://localhost:5002"

# A page object is `/Type /Page` (optional whitespace) NOT followed by `s`
# (so the `/Type /Pages` page-tree root is excluded). Heuristic — exact only
# for uncompressed PDFs, but tolerant of the missing-space form.
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![s/])")


def count_pdf_pages(data: bytes) -> int:
    """Best-effort page count from raw PDF bytes (no PDF library)."""
    return len(_PAGE_RE.findall(data))


class _ConvertClient(Protocol):
    def convert_file(
        self, pdf_path: Any, *, to_formats: tuple[str, ...], preset: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BenchRun:
    wall_s: float
    server_s: float


@dataclass(frozen=True)
class BenchResult:
    preset: str
    to_format: str
    iterations: int
    pages: int
    runs: tuple[BenchRun, ...]

    @property
    def cold_wall_s(self) -> float:
        return self.runs[0].wall_s

    @property
    def cold_server_s(self) -> float:
        return self.runs[0].server_s

    @property
    def _warm_runs(self) -> tuple[BenchRun, ...]:
        return self.runs[1:] or self.runs  # single iteration: warm == cold

    @property
    def warm_wall_mean_s(self) -> float:
        warm = self._warm_runs
        return sum(r.wall_s for r in warm) / len(warm)

    @property
    def warm_server_mean_s(self) -> float:
        warm = self._warm_runs
        return sum(r.server_s for r in warm) / len(warm)

    @property
    def per_page_warm_server_s(self) -> float:
        return self.warm_server_mean_s / self.pages if self.pages else 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "to_format": self.to_format,
            "iterations": self.iterations,
            "pages": self.pages,
            "cold_wall_s": round(self.cold_wall_s, 4),
            "cold_server_s": round(self.cold_server_s, 4),
            "warm_wall_mean_s": round(self.warm_wall_mean_s, 4),
            "warm_server_mean_s": round(self.warm_server_mean_s, 4),
            "per_page_warm_server_s": round(self.per_page_warm_server_s, 4),
            "runs": [
                {"wall_s": round(r.wall_s, 4), "server_s": round(r.server_s, 4)} for r in self.runs
            ],
        }


def run_bench(
    client: _ConvertClient,
    pdf_path: str | Path,
    *,
    iterations: int,
    to_format: str,
    preset: str,
) -> BenchResult:
    """Run ``iterations`` conversions, recording wall + server time for each."""
    path = Path(pdf_path)
    pages = count_pdf_pages(path.read_bytes())
    runs: list[BenchRun] = []
    for _ in range(max(1, iterations)):
        start = time.monotonic()
        resp = client.convert_file(path, to_formats=(to_format,), preset=preset)
        wall = time.monotonic() - start
        server = float(resp.get("processing_time") or 0.0)
        runs.append(BenchRun(wall_s=wall, server_s=server))
    return BenchResult(
        preset=preset,
        to_format=to_format,
        iterations=len(runs),
        pages=pages,
        runs=tuple(runs),
    )


def register(app: typer.Typer) -> None:
    """Attach the ``docling`` command group to ``app``."""

    docling_app = typer.Typer(
        name="docling",
        help="Document-parsing (docling-serve) utilities.",
        no_args_is_help=True,
    )
    app.add_typer(docling_app)

    @docling_app.command("bench")
    def bench(
        pdf: Path = typer.Argument(..., help="PDF file to convert repeatedly"),
        iterations: int = typer.Option(3, "--iter", "-n", min=1, help="Number of runs"),
        to_format: str = typer.Option("md", "--format", "-f", help="Output format"),
        preset: str = typer.Option("balanced", "--preset", "-p", help="fast | balanced | scan"),
        url: str | None = typer.Option(
            None,
            "--url",
            help=f"docling-serve URL (default: $AGMIND_DOCLING_URL or {_DEFAULT_URL})",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Benchmark a docling preset: cold/warm/per-page conversion timing."""
        from agmind.compute.clients.docling import DoclingError

        if not pdf.is_file():
            print(f"ERROR: PDF not found: {pdf}", file=sys.stderr)
            raise typer.Exit(code=2)

        target = url or os.environ.get("AGMIND_DOCLING_URL", _DEFAULT_URL)
        client = DoclingClient(target)
        try:
            result = run_bench(
                client, pdf, iterations=iterations, to_format=to_format, preset=preset
            )
        except (DoclingError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise typer.Exit(code=1) from None

        if as_json:
            typer.echo(json.dumps(result.to_payload(), indent=2, ensure_ascii=False))
            return
        typer.echo(
            f"docling bench — preset={result.preset} format={result.to_format} pages={result.pages}"
        )
        typer.echo(f"  iterations:   {result.iterations}  (URL {target})")
        typer.echo(f"  cold:  wall {result.cold_wall_s:.3f}s  server {result.cold_server_s:.3f}s")
        typer.echo(
            f"  warm:  wall {result.warm_wall_mean_s:.3f}s  server {result.warm_server_mean_s:.3f}s"
        )
        typer.echo(f"  per-page (warm, server): {result.per_page_warm_server_s:.3f}s")
        typer.echo(
            "  note: server time is the accurate per-conversion figure; wall adds HTTP overhead."
        )
