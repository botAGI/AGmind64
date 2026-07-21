"""k6-backed chat load test (Phase 4.2).

Pure-ish business logic so the CLI stays a thin wrapper and the load-test logic
is unit-testable WITHOUT k6 or a live LLM:

  * ``script_path`` resolves the static, shipped k6 ``.js`` (via ``data_root()`` so it
    works in both an editable checkout and a wheel install).
  * ``build_env`` maps CLI options to the ``__ENV.*`` knobs the script reads.
  * ``run_chat_loadtest`` shells out to ``k6 run`` and parses the JSON summary.
  * ``parse_summary`` / ``LoadTestMetrics`` extract p50/p95 latency, req/s, errors
    from the k6 end-of-test summary object — the part covered by a fixture in CI.

The ``.js`` is parameterized entirely through ``__ENV`` so it never hard-codes an
endpoint/model/load profile and ships static.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from agmind.core.paths import data_root

DEFAULT_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_VUS = 5
DEFAULT_DURATION = "30s"
# k6 has no fixed default timeout; cap the subprocess generously above DURATION.
_RUN_TIMEOUT_HEADROOM_S = 120.0


class LoadTestError(RuntimeError):
    """A managed load-test failure (k6 missing / k6 run failed / summary unreadable).

    The CLI turns this into an actionable message + non-zero exit, never a traceback.
    """


def which_k6() -> str | None:
    """Absolute path to the ``k6`` binary, or ``None`` if it is not on PATH.

    Seam (monkeypatchable in tests) — k6 is intentionally NOT a Python dependency
    (it is a Go binary the operator installs), so the wrapper fail-fasts when absent.
    """
    return shutil.which("k6")


def script_path() -> Path:
    """Path to the shipped, static k6 chat script (``templates/loadtest/chat.js``)."""
    return data_root() / "templates" / "loadtest" / "chat.js"


def build_env(
    *,
    endpoint: str,
    model: str,
    vus: int,
    duration: str,
    api_key: str | None = None,
    prompt: str | None = None,
    summary_path: str | None = None,
) -> dict[str, str]:
    """Map options to the ``__ENV.*`` vars the k6 script reads (all values are strings)."""
    env: dict[str, str] = {
        "ENDPOINT": endpoint,
        "MODEL": model,
        "VUS": str(vus),
        "DURATION": duration,
    }
    if api_key is not None:
        env["API_KEY"] = api_key
    if prompt is not None:
        env["PROMPT"] = prompt
    if summary_path is not None:
        env["SUMMARY"] = summary_path
    return env


@dataclass(frozen=True)
class LoadTestMetrics:
    """Headline figures lifted from the k6 end-of-test summary."""

    p50_ms: float
    p95_ms: float
    requests_per_sec: float
    total_requests: int
    error_rate: float  # 0.0–1.0 fraction (http_req_failed rate)
    tokens_per_sec: float = 0.0  # generated completion tokens/sec (0.0 if summary omits it)

    @property
    def error_pct(self) -> float:
        return self.error_rate * 100.0

    def to_dict(self) -> dict[str, float | int]:
        d: dict[str, float | int] = dict(asdict(self))
        d["error_pct"] = self.error_pct
        return d


def _metric_value(data: dict[str, object], metric: str, key: str, default: float) -> float:
    """Safely read ``data.metrics.<metric>.values.<key>`` (k6 summary shape)."""
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        return default
    entry = metrics.get(metric)
    if not isinstance(entry, dict):
        return default
    values = entry.get("values")
    if not isinstance(values, dict):
        return default
    val = values.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def parse_summary(data: dict[str, object]) -> LoadTestMetrics:
    """Extract headline metrics from a k6 ``handleSummary(data)`` object.

    Units: ``http_req_duration`` values are milliseconds; ``http_req_failed.values.rate``
    is a 0.0–1.0 fraction. The p95 key is the literal bracket key ``"p(95)"``. Missing
    metrics (e.g. zero requests issued) default to 0 rather than KeyError-ing.
    """
    return LoadTestMetrics(
        p50_ms=_metric_value(data, "http_req_duration", "med", 0.0),
        p95_ms=_metric_value(data, "http_req_duration", "p(95)", 0.0),
        requests_per_sec=_metric_value(data, "http_reqs", "rate", 0.0),
        total_requests=int(_metric_value(data, "http_reqs", "count", 0.0)),
        error_rate=_metric_value(data, "http_req_failed", "rate", 0.0),
        # chat.js (SPEC-16.4) injects tokens_per_second into the summary; older summaries
        # (pre-token-metric) lack it and default to 0.0 so they still parse.
        tokens_per_sec=_metric_value(data, "tokens_per_second", "rate", 0.0),
    )


def format_metrics_text(metrics: LoadTestMetrics) -> str:
    """Plain-text (no rich/ANSI) metrics block — readable over a pipe / no-TTY."""
    return "\n".join(
        [
            "k6 chat load test results:",
            f"  p50 latency:  {metrics.p50_ms:.1f} ms",
            f"  p95 latency:  {metrics.p95_ms:.1f} ms",
            f"  throughput:   {metrics.requests_per_sec:.2f} req/s",
            f"  requests:     {metrics.total_requests}",
            f"  error rate:   {metrics.error_pct:.2f} %",
        ]
    )


def _duration_seconds(duration: str) -> float:
    """Best-effort parse of a k6 duration (e.g. ``30s`` / ``2m`` / ``1h``) to seconds.

    Only used to size the subprocess timeout headroom; unparseable → a safe 60s floor.
    """
    units = {"s": 1.0, "m": 60.0, "h": 3600.0}
    text = duration.strip().lower()
    if text and text[-1] in units:
        try:
            return float(text[:-1]) * units[text[-1]]
        except ValueError:
            return 60.0
    try:
        return float(text)
    except ValueError:
        return 60.0


def run_chat_loadtest(
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str,
    vus: int = DEFAULT_VUS,
    duration: str = DEFAULT_DURATION,
    api_key: str | None = None,
) -> LoadTestMetrics:
    """Run ``k6 run`` against an OpenAI-compatible chat endpoint and parse its summary.

    Raises :class:`LoadTestError` (managed, not a traceback) when k6 is missing, the
    run exits non-zero, or the summary cannot be read. The script writes the JSON
    summary to a temp file (``__ENV.SUMMARY``) which is read back and parsed here.
    """
    k6_bin = which_k6()
    if k6_bin is None:
        raise LoadTestError(
            "k6 is not installed (not on PATH). Install it to run load tests: "
            "https://grafana.com/docs/k6/latest/set-up/install-k6/ "
            "(e.g. `sudo apt install k6` or download the static binary)."
        )

    script = script_path()
    if not script.is_file():
        raise LoadTestError(f"k6 chat script missing at {script}")

    with tempfile.TemporaryDirectory(prefix="agmind-loadtest-") as tmp:
        summary_file = Path(tmp) / "summary.json"
        env = build_env(
            endpoint=endpoint,
            model=model,
            vus=vus,
            duration=duration,
            api_key=api_key,
            summary_path=str(summary_file),
        )
        argv = [k6_bin, "run", "--quiet"]
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv.append(str(script))

        timeout = _duration_seconds(duration) + _RUN_TIMEOUT_HEADROOM_S
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:  # k6 vanished between which() and run()
            raise LoadTestError(f"failed to execute k6: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LoadTestError(f"k6 run timed out after {timeout:.0f}s") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise LoadTestError(
                f"k6 run exited non-zero (rc={proc.returncode}): {detail or 'no output'}"
            )

        if not summary_file.is_file():
            raise LoadTestError("k6 run produced no summary.json (handleSummary did not write)")
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoadTestError(f"could not read k6 summary: {exc}") from exc

    return parse_summary(data)
