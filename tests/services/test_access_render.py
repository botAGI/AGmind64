"""Tests for the access-report renderers: the sectioned ``credentials.txt`` body and the
endpoint lines used by the post-install summary. Both are pure functions over an
``AccessEntry`` list so they are deterministic and leak-checkable.
"""

from __future__ import annotations

import pytest

from agmind.services.access import (
    AccessEntry,
    render_credentials_txt,
    render_endpoint_lines,
)

pytestmark = pytest.mark.backend_any


def _entry(
    service: str,
    url: str,
    *,
    login: str | None = None,
    password: str | None = None,
    password_env: str | None = None,
    first_login_register: bool = False,
    lan_only: bool = False,
    api_kind: str | None = None,
    internal_url: str | None = None,
) -> AccessEntry:
    return AccessEntry(
        service=service,
        url=url,
        login=login,
        password=password,
        password_env=password_env,
        first_login_register=first_login_register,
        lan_only=lan_only,
        api_kind=api_kind,
        internal_url=internal_url,
    )


# ---- credentials.txt (the secrets file → shows the real password) ----


def test_credentials_txt_shows_login_and_real_password() -> None:
    report = [
        _entry(
            "grafana",
            "https://grafana.lab.test",
            login="admin",
            password="s3cret",
            password_env="GRAFANA_PASSWORD",
        ),
    ]
    txt = render_credentials_txt(report)
    assert "grafana" in txt
    assert "https://grafana.lab.test" in txt
    assert "admin" in txt
    assert "s3cret" in txt  # this IS the secrets file (chmod 600) — show it


def test_credentials_txt_register_on_first_login() -> None:
    report = [_entry("openwebui", "https://chat.lab.test", first_login_register=True)]
    txt = render_credentials_txt(report)
    assert "openwebui" in txt
    assert "first login" in txt.lower()


def test_credentials_txt_password_env_fallback_when_value_missing() -> None:
    report = [
        _entry(
            "grafana",
            "https://g.lab.test",
            login="admin",
            password=None,
            password_env="GRAFANA_PASSWORD",
        ),
    ]
    txt = render_credentials_txt(report)
    assert "GRAFANA_PASSWORD" in txt  # point operator at the env var when value unknown


def test_credentials_txt_model_endpoint_block() -> None:
    # no in-stack URL known → falls back to the public URL (legacy behaviour)
    report = [_entry("llama-llm", "https://llama.lab.test", api_kind="openai")]
    txt = render_credentials_txt(report, llama_model="Qwen3.6-35B.gguf")
    assert "https://llama.lab.test/v1" in txt
    assert "Qwen3.6-35B.gguf" in txt
    assert "none" in txt.lower()  # API key: none


def test_credentials_txt_model_endpoint_uses_in_stack_url() -> None:
    """The 'API endpoint URL' an operator pastes into Dify must be the in-stack docker URL — Dify
    reaches the model container directly on the shared network. The public ``https://…`` route is
    behind Authelia (302s API calls) and needs DNS/TLS, so it is shown only as a secondary
    host/LAN line, never as the primary endpoint."""
    report = [
        _entry(
            "llama-llm",
            "https://llama.lab.test",
            api_kind="openai",
            internal_url="http://llama-llm:8080",
        )
    ]
    txt = render_credentials_txt(report, llama_model="Qwen3.6-35B.gguf")
    assert "API endpoint URL: http://llama-llm:8080/v1" in txt
    assert "Qwen3.6-35B.gguf" in txt
    # public URL still surfaced for host/LAN clients, but clearly secondary + auth-gated
    assert "https://llama.lab.test/v1" in txt
    assert "Authelia" in txt
    # the public route must NOT be presented as the endpoint to paste into Dify
    assert "API endpoint URL: https://llama.lab.test/v1" not in txt


def test_credentials_txt_generated_at_header_optional() -> None:
    report = [_entry("grafana", "https://g.test", login="admin", password="x")]
    assert "2026-06-03T00:00:00Z" in render_credentials_txt(
        report, generated_at="2026-06-03T00:00:00Z"
    )
    # omitted when not provided (deterministic body)
    assert "generated" not in render_credentials_txt(report).lower()


def test_credentials_txt_lan_only_ssh_hint() -> None:
    report = [_entry("portainer", "https://p.lab.test", first_login_register=True, lan_only=True)]
    txt = render_credentials_txt(report, server_ip="192.168.1.33")
    assert "ssh -L" in txt
    assert "192.168.1.33" in txt


# ---- endpoint lines for the summary (NO secrets) ----


def test_endpoint_lines_have_urls_and_hints_no_secrets() -> None:
    report = [
        _entry(
            "grafana",
            "https://grafana.lab.test",
            login="admin",
            password="s3cret",
            password_env="GRAFANA_PASSWORD",
        ),
        _entry("openwebui", "https://chat.lab.test", first_login_register=True),
        _entry("llama-llm", "https://llama.lab.test", api_kind="openai"),
    ]
    lines = render_endpoint_lines(report)
    joined = "\n".join(lines)
    assert "https://grafana.lab.test" in joined
    assert "https://chat.lab.test" in joined
    assert "admin" in joined  # login hint is fine
    assert "s3cret" not in joined  # NEVER leak the password into the summary


def test_endpoint_lines_model_endpoint_marked() -> None:
    report = [_entry("llama-llm", "https://llama.lab.test", api_kind="openai")]
    lines = render_endpoint_lines(report)
    assert any("llama-llm" in line and "llama.lab.test" in line for line in lines)
