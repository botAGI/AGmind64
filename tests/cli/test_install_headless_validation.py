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
    assert any("хотя бы один" in e for e in errors)


def test_headless_traefik_valid_passes_and_normalizes_domain() -> None:
    errors, domain = _headless_validation_errors(["traefik"], [], "Lab.Example.COM.", "X" * 40)
    assert errors == []
    assert domain == "lab.example.com"
