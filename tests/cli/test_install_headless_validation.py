"""Phase 09-02 (M8): a `--no-tui` install must require a Cloudflare token and a public
domain ONLY when traefik (the TLS edge, DNS-01 via Cloudflare) is in the effective
selection. A local / non-traefik install needs neither — the previous code validated both
unconditionally, blocking headless automation (and the proven lean local path)."""

from __future__ import annotations

import pytest

from agmind.cli.install_cmd import _headless_validation_errors, _traefik_in_selection

pytestmark = pytest.mark.backend_any


def test_traefik_in_selection_explicit_services() -> None:
    assert _traefik_in_selection(["traefik", "llama-llm"], []) is True
    assert _traefik_in_selection(["llama-llm", "qdrant"], []) is False


def test_traefik_in_selection_via_core_profile() -> None:
    # traefik ships in the core (and full) profiles.
    assert _traefik_in_selection([], ["core"]) is True


def test_headless_no_traefik_needs_no_cf_token_or_domain() -> None:
    errors, _domain = _headless_validation_errors(["llama-llm", "qdrant"], [], "", "")
    assert errors == []


def test_headless_with_traefik_requires_cf_token() -> None:
    errors, _domain = _headless_validation_errors(
        ["traefik", "llama-llm"], [], "lab.example.com", ""
    )
    assert any("CF API token" in e for e in errors)


def test_headless_with_traefik_requires_domain() -> None:
    errors, _domain = _headless_validation_errors(["traefik"], [], "", "X" * 40)
    assert any("domain" in e for e in errors)


def test_headless_empty_selection_errors() -> None:
    errors, _domain = _headless_validation_errors([], [], "", "")
    assert any("at least one service" in e for e in errors)


def test_headless_traefik_valid_passes_and_normalizes_domain() -> None:
    errors, domain = _headless_validation_errors(["traefik"], [], "Lab.Example.COM.", "X" * 40)
    assert errors == []
    assert domain == "lab.example.com"


# ---------- P0.4/D-06: plain --no-tui prior-state closure expansion ----------


def test_no_tui_prior_state_reaches_install_config_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain `--no-tui` re-run (prior-state path, no --from-state) must run the prior
    selection through the dependency closure before InstallConfig — mirrors the
    --from-state fix (1fdf201) so a component's siblings are not silently dropped by
    `compose up --remove-orphans`."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.cli.tui.setup_wizard import SetupState
    from agmind.install.orchestrator import InstallResult

    monkeypatch.setattr(
        "agmind.cli.install_state.load_prior_setup_state",
        lambda _path: SetupState(domain="lab.example.com", services=["dify-api"]),
    )
    monkeypatch.setattr("agmind.cli.install_cmd._sudo_nopasswd_available", lambda: True)
    monkeypatch.setattr("agmind.install.steps.default_steps", lambda: [])

    captured: dict[str, object] = {}

    class FakeOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> InstallResult:
            return InstallResult(success=True, steps=(), message="install ok")

    monkeypatch.setattr("agmind.install.orchestrator.InstallOrchestrator", FakeOrchestrator)

    result = CliRunner().invoke(_make_app(), ["install", "--no-tui"])

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert "dify-api" in config.services  # type: ignore[attr-defined]
    # closure siblings a raw prior.services echo would NOT carry
    assert "postgres" in config.services  # type: ignore[attr-defined]
    assert "dify-worker" in config.services  # type: ignore[attr-defined]


def test_no_tui_prior_state_resolver_failure_exits_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolver bug (ValueError) on the prior-state closure must fail loudly (exit 2),
    not silently continue with the unexpanded selection."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.cli.tui.setup_wizard import SetupState

    monkeypatch.setattr(
        "agmind.cli.install_state.load_prior_setup_state",
        lambda _path: SetupState(domain="lab.example.com", services=["dify-api"]),
    )
    monkeypatch.setattr("agmind.cli.install_cmd._sudo_nopasswd_available", lambda: True)
    monkeypatch.setattr("agmind.install.steps.default_steps", lambda: [])

    def _boom(_services: list[str]) -> list[str]:
        raise ValueError("unknown selected service: nope-svc")

    monkeypatch.setattr("agmind.cli.tui.setup_wizard.expand_selected_services_for_setup", _boom)

    def _must_not_run(**_kwargs: object) -> object:
        raise AssertionError("orchestrator must not run when the closure resolver fails")

    monkeypatch.setattr("agmind.install.orchestrator.InstallOrchestrator", _must_not_run)

    result = CliRunner().invoke(_make_app(), ["install", "--no-tui"])

    assert result.exit_code == 2
    assert "invalid prior selected services" in result.output
    assert "nope-svc" in result.output
