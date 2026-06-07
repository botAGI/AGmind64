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


def _setup_real_domain_compose(tmp_path: Path) -> None:
    """A compose with a traefik Host(`...`) router rule using the REAL domain, but an .env that
    carries NO AGMIND_DOMAIN — mimics the non-root case where root-owned .env is unreadable."""
    (tmp_path / ".env").write_text("GRAFANA_PASSWORD=topsecret\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  grafana:\n"
        "    labels:\n"
        "      traefik.http.routers.grafana.rule: Host(`grafana.lab.agmind.dev`)\n",
        encoding="utf-8",
    )


def test_endpoints_recovers_real_domain_from_compose_when_env_lacks_domain(
    tmp_path: Path,
) -> None:
    """live-audit 2026-06-08 UX-1: with no AGMIND_DOMAIN in .env (root-owned/unreadable), the
    real domain must be recovered from the world-readable compose, NOT the agmind.dev placeholder."""
    _setup_real_domain_compose(tmp_path)
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "https://grafana.lab.agmind.dev" in res.output
    assert "agmind.dev\n" not in res.output  # never the bare placeholder
    assert "grafana.agmind.dev" not in res.output


def test_open_recovers_real_domain_from_compose(tmp_path: Path) -> None:
    _setup_real_domain_compose(tmp_path)
    res = runner.invoke(_app(), ["open", "grafana", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == "https://grafana.lab.agmind.dev"


def test_open_unknown_service_lists_available(tmp_path: Path) -> None:
    """live-audit 2026-06-08 M4: a bad service name must list the valid choices."""
    _setup(tmp_path)
    res = runner.invoke(_app(), ["open", "nope", "--install-dir", str(tmp_path)])
    assert res.exit_code == 1
    assert "available:" in res.output
    assert "grafana" in res.output


def test_endpoints_shows_kind_tag_and_creds_footer(tmp_path: Path) -> None:
    """M3: UI vs OpenAI-API kind tag; L4: footer points at credentials.txt."""
    _setup(tmp_path)
    res = runner.invoke(_app(), ["endpoints", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "[UI]" in res.output  # grafana is a UI
    assert "[OpenAI API" in res.output  # llama-llm is a model endpoint
    assert "credentials.txt" in res.output


def test_creds_show_includes_model_endpoints_and_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """live-audit 2026-06-08 H2/UX-3: creds show must include model endpoints (the /v1 URL) and
    point at credentials.txt — not just logins/passwords."""
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = runner.invoke(_app(), ["creds", "show", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "/v1" in res.output  # model endpoint section present
    assert "Model endpoints" in res.output
    assert "credentials.txt" in res.output
    assert "--show to reveal" in res.output  # L1 mask hint


def test_creds_show_json_includes_note_and_model_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = runner.invoke(_app(), ["creds", "show", "--install-dir", str(tmp_path), "--json"])
    assert res.exit_code == 0, res.output
    rows = json.loads(res.output)
    # every row carries the new keys (even if None)
    assert all("note" in r and "model_name" in r and "api_kind" in r for r in rows)


def test_creds_refresh_writes_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """live-audit 2026-06-08 H1/UX-3: creds refresh regenerates credentials.txt (chmod 600)."""
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    res = runner.invoke(_app(), ["creds", "refresh", "--install-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    creds = tmp_path / "credentials.txt"
    assert creds.exists()
    import stat as _stat

    assert _stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert "topsecret" in creds.read_text(encoding="utf-8")  # it IS the secrets file


def test_creds_refresh_requires_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    res = runner.invoke(_app(), ["creds", "refresh", "--install-dir", str(tmp_path)])
    assert res.exit_code == 1
    assert "root" in res.output.lower()
