"""Tests for `agmind docling bench` — cold/warm/per-page timing of docling presets.

The HTTP client is faked (no live docling-serve). Bench timing logic is asserted
on its structure (run count, cold==first run, warm==mean of the rest, page count)
rather than wall-clock values, which are environment-dependent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli import _HAS_TYPER
from agmind.cli.docling_cmd import BenchResult, count_pdf_pages, run_bench

pytestmark = pytest.mark.backend_any


class _FakeClient:
    def __init__(self, server_time: float = 0.1) -> None:
        self.calls = 0
        self.server_time = server_time

    def convert_file(self, pdf_path, *, to_formats, preset):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"status": "success", "processing_time": self.server_time}


def _write_pdf(path: Path, pages: int) -> None:
    body = b"%PDF-1.4\n" + b"/Type /Page\n" * pages + b"%%EOF\n"
    path.write_bytes(body)


# ---- page counting ----


def test_count_pdf_pages_counts_page_objects() -> None:
    assert count_pdf_pages(b"%PDF\n/Type /Page\n/Type /Page\n") == 2


def test_count_pdf_pages_tolerates_missing_space() -> None:
    assert count_pdf_pages(b"/Type/Page /Type /Page /Type/Page") == 3


def test_count_pdf_pages_does_not_count_pages_object() -> None:
    # /Type /Pages (the page-tree root) must not be miscounted as a page.
    assert count_pdf_pages(b"/Type /Pages\n/Type /Page\n") == 1


# ---- run_bench ----


def test_run_bench_records_cold_and_warm(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    _write_pdf(pdf, pages=3)
    client = _FakeClient(server_time=0.2)

    result = run_bench(client, pdf, iterations=3, to_format="md", preset="fast")

    assert isinstance(result, BenchResult)
    assert client.calls == 3
    assert len(result.runs) == 3
    assert result.pages == 3
    assert result.cold_server_s == pytest.approx(0.2)
    assert result.warm_server_mean_s == pytest.approx(0.2)
    payload = result.to_payload()
    assert payload["iterations"] == 3
    assert payload["pages"] == 3
    assert payload["preset"] == "fast"


def test_run_bench_single_iteration_warm_equals_cold(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    _write_pdf(pdf, pages=1)
    client = _FakeClient(server_time=0.5)

    result = run_bench(client, pdf, iterations=1, to_format="md", preset="balanced")
    assert client.calls == 1
    assert result.cold_server_s == pytest.approx(0.5)
    assert result.warm_server_mean_s == pytest.approx(0.5)


# ---- CLI ----


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_docling_registered_in_help() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    result = CliRunner().invoke(_make_app(), ["--help"])
    assert result.exit_code == 0
    assert "docling" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_docling_bench_runs_with_faked_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app, docling_cmd

    pdf = tmp_path / "in.pdf"
    _write_pdf(pdf, pages=2)

    monkeypatch.setattr(docling_cmd, "DoclingClient", lambda *a, **k: _FakeClient(0.1))

    result = CliRunner().invoke(
        _make_app(),
        ["docling", "bench", str(pdf), "--iter", "2", "--url", "http://x:5002"],
    )
    assert result.exit_code == 0, result.output
    assert "cold" in result.output.lower()
    assert "warm" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_docling_bench_missing_file_errors_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    result = CliRunner().invoke(
        _make_app(),
        ["docling", "bench", "/no/such/file.pdf", "--url", "http://x:5002"],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
