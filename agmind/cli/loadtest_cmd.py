"""`agmind loadtest` — load-test deployed endpoints (Phase 4.2).

Thin CLI wrapper: argument parsing + output formatting only. The k6 logic lives in
``agmind.loadtest.k6`` (importable + unit-testable without k6 or a live LLM).
"""

from __future__ import annotations

import json

import typer

from agmind.loadtest.k6 import (
    DEFAULT_DURATION,
    DEFAULT_ENDPOINT,
    DEFAULT_VUS,
    LoadTestError,
    format_metrics_text,
    run_chat_loadtest,
)


def register(app: typer.Typer) -> None:
    """Attach the ``loadtest`` group to ``app``."""

    loadtest_app = typer.Typer(
        name="loadtest",
        help="Run load tests against deployed endpoints (requires the k6 binary).",
        no_args_is_help=True,
    )
    app.add_typer(loadtest_app)

    @loadtest_app.command("chat")
    def loadtest_chat(
        model: str = typer.Option(
            ..., "--model", help="Served model id sent in the chat-completions payload."
        ),
        endpoint: str = typer.Option(
            DEFAULT_ENDPOINT,
            "--endpoint",
            help="OpenAI-compatible chat-completions URL.",
        ),
        vus: int = typer.Option(
            DEFAULT_VUS, "--vus", help="Concurrent virtual users (constant load)."
        ),
        duration: str = typer.Option(
            DEFAULT_DURATION, "--duration", help="Load duration, e.g. 30s / 2m."
        ),
        api_key: str = typer.Option(
            "dummy", "--api-key", help="Bearer token (local llama needs none)."
        ),
        as_json: bool = typer.Option(False, "--json", help="Machine-parseable metrics."),
    ) -> None:
        """Load-test an OpenAI-compatible chat endpoint with k6.

        Spins up ``--vus`` virtual users hitting ``--endpoint`` for ``--duration`` and
        reports p50/p95 latency, throughput, and error rate. Needs the ``k6`` binary on
        PATH — if it is missing you get an actionable install hint, not a traceback.
        """
        try:
            metrics = run_chat_loadtest(
                endpoint=endpoint,
                model=model,
                vus=vus,
                duration=duration,
                api_key=api_key,
            )
        except LoadTestError as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(metrics.to_dict(), indent=2))
        else:
            typer.echo(format_metrics_text(metrics))
