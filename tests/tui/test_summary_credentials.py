"""Tests for operator-facing credential path hints."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.tui.install_screen import InstallProgressScreen
from agmind.cli.tui.summary_screen import SummaryScreen
from agmind.install.orchestrator import InstallConfig

pytestmark = pytest.mark.backend_any


def test_summary_screen_points_to_runtime_env_without_secret_values(tmp_path: Path) -> None:
    screen = SummaryScreen(
        mode="deploy_success",
        domain="lab.example.com",
        profiles=["core", "automation"],
        backend="auto",
        model_tier="XL",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=tmp_path / "agmind",
    )

    summary = screen._config_summary()

    assert f"Runtime env:  {tmp_path / 'agmind' / '.env'} (chmod 600)" in summary
    # summary points to the creds file / `creds show` instead of printing secret values
    assert "creds show" in summary
    assert "credentials.txt" in summary
    assert "super-secret" not in summary


def test_summary_screen_next_steps_use_existing_setup_commands(tmp_path: Path) -> None:
    modes = ("next_steps", "deploy_success", "deploy_failure")
    for mode in modes:
        screen = SummaryScreen(
            mode=mode,
            domain="lab.example.com",
            profiles=["core"],
            backend="auto",
            model_tier="XL",
            state_path=tmp_path / "state.json",
            token_path=tmp_path / "cf-token",
            install_dir=tmp_path / "agmind",
        )

        next_steps = screen._next_steps_text()

        assert "agmind setup --deploy" not in next_steps
        assert "agmind setup" in next_steps


def test_summary_screen_next_steps_use_explicit_services_when_available(tmp_path: Path) -> None:
    screen = SummaryScreen(
        mode="next_steps",
        domain="lab.example.com",
        profiles=["stale-profile"],
        services=["traefik", "llama-llm"],
        backend="auto",
        model_tier="XL",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=tmp_path / "agmind",
    )

    summary = screen._config_summary()
    next_steps = screen._next_steps_text()

    assert "Services:     traefik, llama-llm" in summary
    assert "--service traefik" in next_steps
    assert "--service llama-llm" in next_steps
    assert "--profile stale-profile" not in next_steps


def test_summary_screen_keeps_legacy_positional_deploy_result(
    tmp_path: Path,
) -> None:
    from agmind.deploy.runner import DeployResult

    deploy_result = DeployResult(success=True, message="ok")
    screen = SummaryScreen(
        "deploy_success",
        "lab.example.com",
        ["core"],
        "auto",
        "XL",
        tmp_path / "state.json",
        tmp_path / "cf-token",
        tmp_path / "agmind",
        deploy_result,
    )

    assert screen.deploy_result is deploy_result
    assert screen.services == []


def test_summary_screen_prefers_agmind_log_commands(tmp_path: Path) -> None:
    success = SummaryScreen(
        mode="deploy_success",
        domain="lab.example.com",
        profiles=["core"],
        backend="auto",
        model_tier="XL",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=tmp_path / "agmind",
    )._next_steps_text()
    failure = SummaryScreen(
        mode="deploy_failure",
        domain="lab.example.com",
        profiles=["core"],
        backend="auto",
        model_tier="XL",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=tmp_path / "agmind",
    )._next_steps_text()

    assert "agmind logs <service> --follow" in success
    assert "agmind logs --tail 200" in failure
    assert "docker logs" not in success
    assert "docker logs" not in failure


def test_summary_deploy_success_lists_real_endpoints_and_access_cli(tmp_path: Path) -> None:
    install_dir = tmp_path / "agmind"
    install_dir.mkdir()
    (install_dir / ".env").write_text("GRAFANA_PASSWORD=topsecret\n", encoding="utf-8")
    screen = SummaryScreen(
        mode="deploy_success",
        domain="lab.test",
        profiles=["observability"],
        backend="vulkan",
        model_tier="L",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=install_dir,
        services=["grafana", "llama-llm"],
    )

    text = screen._next_steps_text()

    # real endpoints (domain-substituted), not the old hardcoded grafana/chat/llama trio
    assert "https://grafana.lab.test" in text
    assert "https://llama.lab.test" in text
    # re-view CLI + persisted credentials path are surfaced
    assert "agmind endpoints" in text
    assert "agmind creds show" in text
    assert "credentials.txt" in text
    # never leak a secret into the summary
    assert "topsecret" not in text


def test_summary_deploy_success_falls_back_without_services(tmp_path: Path) -> None:
    screen = SummaryScreen(
        mode="deploy_success",
        domain="lab.test",
        profiles=["core"],
        backend="auto",
        model_tier="XL",
        state_path=tmp_path / "state.json",
        token_path=tmp_path / "cf-token",
        install_dir=tmp_path / "agmind",
    )
    text = screen._next_steps_text()
    assert "https://<service>.lab.test" in text  # graceful fallback, no crash


def test_summary_screen_full_install_command_keeps_cf_token_out_of_argv(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    token_path = tmp_path / "cf-token"
    next_steps = SummaryScreen(
        mode="next_steps",
        domain="lab.example.com",
        profiles=["core"],
        backend="auto",
        model_tier="XL",
        state_path=state_path,
        token_path=token_path,
        install_dir=tmp_path / "agmind",
    )._next_steps_text()

    assert "sudo ansible-playbook" not in next_steps
    assert "agmind_cf_api_token=$(cat" not in next_steps
    assert "agmind install --no-tui" in next_steps
    assert f"--from-state {state_path}" in next_steps
    assert f"--cf-token-file {token_path}" in next_steps


def test_install_progress_screen_final_hint_points_to_runtime_env(tmp_path: Path) -> None:
    config = InstallConfig(
        domain="lab.example.com",
        cf_api_token="super-secret-token",
        services=["llama-llm", "n8n"],
        install_dir=tmp_path / "agmind",
    )
    screen = InstallProgressScreen(config=config, steps=[])

    hint = screen._final_operator_hint()

    assert f"Runtime credentials: {tmp_path / 'agmind' / '.env'} (chmod 600)" in hint
    assert "Values are not printed" in hint
    assert "super-secret-token" not in hint
