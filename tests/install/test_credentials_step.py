"""Tests for the final install step that persists ``credentials.txt`` (chmod 600) from the
service descriptors + the rendered ``.env``. Backs the operator's post-install access info.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import CredentialsStep, default_steps

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path, services: list[str]) -> InstallConfig:
    return InstallConfig(
        domain="lab.test",
        cf_api_token="x" * 20,
        services=services,
        install_dir=tmp_path,
        model_file="Qwen3.6-35B.gguf",
    )


def test_credentials_step_writes_chmod_600_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GRAFANA_PASSWORD=topsecret\n", encoding="utf-8")
    res = CredentialsStep().run(lambda _e: None, _cfg(tmp_path, ["grafana", "llama-llm"]))
    assert res.success

    creds = tmp_path / "credentials.txt"
    assert creds.exists()
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600

    text = creds.read_text(encoding="utf-8")
    assert "grafana" in text
    assert "admin" in text
    assert "topsecret" in text  # this IS the secrets file
    # domain is substituted (descriptor host agmind.dev → install domain lab.test)
    assert "https://grafana.lab.test" in text
    assert "https://llama.lab.test/v1" in text  # model endpoint block
    assert "agmind.dev" not in text
    assert "Qwen3.6-35B.gguf" in text


def test_credentials_step_nonfatal_without_env(tmp_path: Path) -> None:
    # No .env present → password unknown, but the file is still written (best-effort).
    res = CredentialsStep().run(lambda _e: None, _cfg(tmp_path, ["grafana"]))
    assert res.success
    creds = tmp_path / "credentials.txt"
    assert creds.exists()
    text = creds.read_text(encoding="utf-8")
    assert "GRAFANA_PASSWORD" in text  # fallback: point at the env var


def test_credentials_step_is_final_in_default_pipeline() -> None:
    assert [s.step_id for s in default_steps()][-1] == "credentials"
