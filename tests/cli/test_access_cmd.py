"""Tests for the post-install access CLI: ``agmind endpoints`` / ``agmind open`` /
``agmind creds show``. These re-derive the access report live from descriptors + the rendered
``.env`` so the operator can review access at any time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.backend_any

runner = CliRunner()


def _app():  # type: ignore[no-untyped-def]
    from agmind.cli import _make_app

    return _make_app()


def _setup(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "AGMIND_DOMAIN=lab.test\nGRAFANA_PASSWORD=topsecret\n", encoding="utf-8"
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  grafana: {}\n  llama-llm: {}\n", encoding="utf-8"
    )


def test_endpoints_table(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "grafana" in res.output
    assert "https://grafana.lab.test" in res.output
    assert "https://llama.lab.test" in res.output
    assert "topsecret" not in res.output  # endpoints never print secrets


def test_endpoints_json(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    rows = {r["service"]: r for r in json.loads(res.output)}
    assert rows["grafana"]["url"] == "https://grafana.lab.test"
    assert "state" in rows["grafana"]


def test_open_prints_url(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = runner.invoke(_app(), ["open", "grafana", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == "https://grafana.lab.test"


def test_open_unknown_service_errors(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = runner.invoke(_app(), ["open", "does-not-exist", "--install-dir", str(tmp_path)])
    assert res.exit_code == 1


def test_creds_show_requires_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    res = runner.invoke(_app(), ["creds", "show", "--install-dir", str(tmp_path)])
    assert res.exit_code == 1
    assert "root" in res.output.lower()


def test_creds_show_masks_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = runner.invoke(_app(), ["creds", "show", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "grafana" in res.output
    assert "admin" in res.output
    assert "topsecret" not in res.output  # masked by default


def test_creds_show_reveals_with_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = runner.invoke(_app(), ["creds", "show", "--install-dir", str(tmp_path), "--show"])
    assert res.exit_code == 0, res.output
    assert "topsecret" in res.output


def test_endpoints_warns_when_llm_skipped(tmp_path: Path) -> None:
    """live-audit 2026-06-05 (llm-skip-unsurfaced): a deployed llm_inference consumer
    (openwebui) with NO llama-llm must surface that chat/generation is disabled."""
    (tmp_path / ".env").write_text("AGMIND_DOMAIN=lab.test\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  openwebui: {}\n  grafana: {}\n", encoding="utf-8"
    )
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    out = res.output.lower()
    assert "openwebui" in out
    assert "disabled" in out and "chat" in out


def test_endpoints_no_llm_warning_when_llama_llm_present(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AGMIND_DOMAIN=lab.test\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  openwebui: {}\n  llama-llm: {}\n", encoding="utf-8"
    )
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "disabled" not in res.output.lower()
