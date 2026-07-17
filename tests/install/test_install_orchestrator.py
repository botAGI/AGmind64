"""Phase N: tests for agmind.install.orchestrator + step contract."""

from __future__ import annotations

import os
import stat
import subprocess
import urllib.error
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from agmind.core.env import parse_env_file
from agmind.install.orchestrator import (
    InstallConfig,
    InstallOrchestrator,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressEvent,
    ProgressKind,
)

pytestmark = pytest.mark.backend_any


# ---------- fake step ----------


@dataclass
class FakeStep(InstallStep):
    step_id: str = "fake"
    label: str = "fake step"
    should_fail: bool = False
    fail_message: str = "boom"
    log_lines: list[str] = field(default_factory=list)
    raised: Exception | None = None

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        for line in self.log_lines:
            callback(ProgressEvent(step_id=self.step_id, kind=ProgressKind.LOG, text=line))
        if self.raised is not None:
            raise self.raised
        if self.should_fail:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=self.fail_message,
                elapsed=timedelta(seconds=0.1),
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"{self.step_id} ok",
            elapsed=timedelta(seconds=0.1),
        )


def _make_config(tmp_path: Path) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["traefik", "llama-llm"],
        install_dir=tmp_path / "opt" / "agmind",
        models_dir=tmp_path / "var" / "models",
        sudo_password="sup3rs3cret",
    )


# ---------- InstallConfig ----------


def test_config_redact_hides_secrets(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    payload = cfg.redact()
    assert payload["cf_api_token"] == "*** (40 chars)"
    assert payload["sudo_password"] == "*** (set)"
    assert "sup3rs3cret" not in str(payload)
    assert "X" * 40 not in str(payload)


def test_config_wipe_secrets(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.wipe_secrets()
    assert cfg.cf_api_token == ""
    assert cfg.sudo_password is None


# ---------- Orchestrator happy path ----------


def test_orchestrator_runs_all_steps(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    steps: list[InstallStep] = [
        FakeStep(step_id="a", label="A"),
        FakeStep(step_id="b", label="B"),
        FakeStep(step_id="c", label="C"),
    ]
    orchestrator = InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=steps,
        callback=events.append,
    )
    result = orchestrator.run()
    assert result.success is True
    assert len(result.steps) == 3
    assert [s.step_id for s in result.steps] == ["a", "b", "c"]
    starts = [e for e in events if e.kind is ProgressKind.STEP_START]
    dones = [e for e in events if e.kind is ProgressKind.STEP_DONE]
    assert len(starts) == 3
    assert len(dones) == 3


def test_orchestrator_stops_at_first_failure(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    steps: list[InstallStep] = [
        FakeStep(step_id="a"),
        FakeStep(step_id="b", should_fail=True, fail_message="kaboom"),
        FakeStep(step_id="never-runs"),
    ]
    result = InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=steps,
        callback=events.append,
    ).run()
    assert result.success is False
    assert len(result.steps) == 2  # никогда не дошли до step c
    failed = result.failed_step
    assert failed is not None
    assert failed.step_id == "b"
    assert "kaboom" in failed.message
    error_events = [e for e in events if e.kind is ProgressKind.STEP_ERROR]
    assert len(error_events) == 1
    assert error_events[0].step_id == "b"


def test_orchestrator_catches_unhandled_exception(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    steps: list[InstallStep] = [
        FakeStep(step_id="crash", raised=RuntimeError("blow up")),
    ]
    result = InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=steps,
        callback=events.append,
    ).run()
    assert result.success is False
    assert "blow up" in result.failed_step.message


def test_orchestrator_emits_step_logs(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    steps = [FakeStep(step_id="a", log_lines=["hello", "world"])]
    InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=steps,
        callback=events.append,
    ).run()
    logs = [e.text for e in events if e.kind is ProgressKind.LOG]
    assert logs == ["hello", "world"]


def test_orchestrator_redacts_secrets_from_events_and_results(tmp_path: Path) -> None:
    token = "cf-secret-token-" + "X" * 32
    sudo_password = "sudo-secret-password"
    leak = f"token={token} sudo={sudo_password}"
    events: list[ProgressEvent] = []

    class SecretSuccessStep(InstallStep):
        step_id = "success"
        label = "success"

        def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
            callback(ProgressEvent(step_id=self.step_id, kind=ProgressKind.LOG, text=leak))
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message=f"done {leak}",
                elapsed=timedelta(seconds=0.1),
            )

    cfg = _make_config(tmp_path)
    cfg.cf_api_token = token
    cfg.sudo_password = sudo_password
    result = InstallOrchestrator(
        config=cfg,
        steps=[
            SecretSuccessStep(),
            FakeStep(step_id="failure", should_fail=True, fail_message=f"failed {leak}"),
        ],
        callback=events.append,
    ).run()

    payload = "\n".join(
        [
            *(event.text for event in events),
            *(step.message for step in result.steps),
            result.message,
        ]
    )
    assert result.success is False
    assert token not in payload
    assert sudo_password not in payload
    assert "***" in payload


def test_orchestrator_wipes_sudo_password_after_bootstrap(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert cfg.sudo_password == "sup3rs3cret"
    steps = [FakeStep(step_id="bootstrap", label="Bootstrap")]
    InstallOrchestrator(config=cfg, steps=steps, callback=lambda _e: None).run()
    assert cfg.sudo_password is None


def test_orchestrator_keeps_sudo_password_available_after_bootstrap(tmp_path: Path) -> None:
    seen: list[str | None] = []

    class NeedsDockerSudoStep(FakeStep):
        step_id: str = "compose_config"
        label: str = "Compose"

        def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
            seen.append(config.sudo_password)
            return super().run(callback, config)

    cfg = _make_config(tmp_path)
    steps: list[InstallStep] = [
        FakeStep(step_id="bootstrap", label="Bootstrap"),
        NeedsDockerSudoStep(),
    ]

    result = InstallOrchestrator(config=cfg, steps=steps, callback=lambda _e: None).run()

    assert result.success is True
    assert seen == ["sup3rs3cret"]
    assert cfg.sudo_password is None


def test_orchestrator_wipes_secrets_on_failure(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    steps = [FakeStep(step_id="x", should_fail=True)]
    InstallOrchestrator(config=cfg, steps=steps, callback=lambda _e: None).run()
    assert cfg.sudo_password is None
    assert cfg.cf_api_token == ""


def test_orchestrator_wipes_secrets_on_success(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    steps = [FakeStep(step_id="x")]
    InstallOrchestrator(config=cfg, steps=steps, callback=lambda _e: None).run()
    assert cfg.cf_api_token == ""


def test_orchestrator_callback_exception_swallowed(tmp_path: Path) -> None:
    def bad_cb(_event: ProgressEvent) -> None:
        raise RuntimeError("listener error")

    steps = [FakeStep(step_id="a")]
    result = InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=steps,
        callback=bad_cb,
    ).run()
    # Step should still succeed — callback errors are logged but ignored.
    assert result.success is True


# ---------- ProgressEvent ----------


def test_progress_event_default_progress_pct_none() -> None:
    ev = ProgressEvent(step_id="x", kind=ProgressKind.LOG, text="hi")
    assert ev.progress_pct is None


def test_progress_event_with_pct() -> None:
    ev = ProgressEvent(step_id="x", kind=ProgressKind.PROGRESS, text="42%", progress_pct=42)
    assert ev.progress_pct == 42


# ---------- default_steps composition ----------


def test_default_steps_list_is_stable() -> None:
    from agmind.install.steps import default_steps

    s = default_steps()
    ids = [step.step_id for step in s]
    assert ids == [
        "doctor",
        "cloudflare_token",
        "bootstrap",
        "env_write",
        "compose_config",
        "model_pull",
        "deploy",
        "gpu_metrics",
        "boot_unit",
        "credentials",
    ]


def test_env_write_step_materializes_runtime_files(tmp_path: Path) -> None:
    """Clean TUI install must create config/secret files consumed by Compose mounts."""
    from agmind.install.steps import EnvWriteStep

    token = "cf-token-" + "X" * 40
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token=token,
        services=["traefik", "prometheus", "grafana", "loki", "alloy", "alertmanager"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success
    data_dir = cfg.models_dir.parent
    secret_file = data_dir / "secrets" / "cf_dns_api_token"
    secret_dir = secret_file.parent
    assert secret_file.read_text(encoding="utf-8") == token
    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert token not in (cfg.install_dir / ".env").read_text(encoding="utf-8")

    assert (data_dir / "traefik" / "dynamic" / "middlewares.yml").exists()
    assert (data_dir / "traefik" / "letsencrypt").is_dir()
    assert (cfg.config_dir / "prometheus" / "prometheus.yml").exists()
    assert (cfg.config_dir / "prometheus" / "rules" / "llama.yml").exists()
    assert (cfg.config_dir / "grafana" / "provisioning" / "datasources" / "agmind.yml").exists()
    assert (cfg.config_dir / "loki" / "loki.yml").exists()
    assert (cfg.config_dir / "alloy" / "config.alloy").exists()
    assert (cfg.config_dir / "alertmanager" / "alertmanager.yml").exists()


def test_cloudflare_token_step_skips_when_traefik_not_selected(tmp_path: Path) -> None:
    from agmind.install.steps import CloudflareTokenStep

    cfg = _make_config(tmp_path)
    cfg.services = ["qdrant"]
    result = CloudflareTokenStep().run(lambda _event: None, cfg)

    assert result.success
    assert "not selected" in result.message


def test_cloudflare_token_step_fails_invalid_token_without_leaking_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import steps
    from agmind.install.steps import CloudflareTokenStep

    token = "cf-secret-token-" + "X" * 32

    def fake_urlopen(request: Any, timeout: float) -> Any:
        del timeout
        assert token in request.headers["Authorization"]
        body = b'{"success":false,"errors":[{"code":1000,"message":"Invalid API Token"}]}'
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(body),
        )

    monkeypatch.setattr(steps.urllib.request, "urlopen", fake_urlopen)
    cfg = _make_config(tmp_path)
    cfg.cf_api_token = token

    result = CloudflareTokenStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "Cloudflare token validation failed" in result.message
    assert "Invalid API Token" in result.message
    assert token not in result.message


def test_cloudflare_token_step_requires_access_to_domain_zone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import steps
    from agmind.install.steps import CloudflareTokenStep

    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request.full_url)
        if request.full_url.endswith("/user/tokens/verify"):
            return FakeResponse(b'{"success":true,"result":{"status":"active"}}')
        if "name=lab.example.com" in request.full_url:
            return FakeResponse(b'{"success":true,"result":[]}')
        if "name=example.com" in request.full_url:
            return FakeResponse(b'{"success":true,"result":[{"id":"zone","name":"example.com"}]}')
        raise AssertionError(request.full_url)

    monkeypatch.setattr(steps.urllib.request, "urlopen", fake_urlopen)
    cfg = _make_config(tmp_path)

    result = CloudflareTokenStep().run(lambda _event: None, cfg)

    assert result.success
    assert "zone access OK (example.com)" in result.message
    assert any("name=lab.example.com" in call for call in calls)
    assert any("name=example.com" in call for call in calls)


def test_cloudflare_token_step_fails_when_no_zone_candidate_is_accessible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import steps
    from agmind.install.steps import CloudflareTokenStep

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return b'{"success":true,"result":[]}'

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        del request, timeout
        return FakeResponse()

    monkeypatch.setattr(steps.urllib.request, "urlopen", fake_urlopen)
    cfg = _make_config(tmp_path)

    result = CloudflareTokenStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot access an active Cloudflare zone" in result.message
    assert "lab.example.com" in result.message
    assert "example.com" in result.message


def test_env_write_step_preserves_runtime_config_on_copytree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["grafana"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    datasources = cfg.config_dir / "grafana" / "provisioning" / "datasources"
    datasources.mkdir(parents=True)
    existing = datasources / "agmind.yml"
    existing.write_text("old datasource\n", encoding="utf-8")
    original_copytree = steps.shutil.copytree

    def flaky_copytree(source: Path, destination: Path, *args: object, **kwargs: object) -> None:
        del args, kwargs
        if source.name == "datasources":
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "BROKEN.yml").write_text("partial\n", encoding="utf-8")
            raise OSError("disk full")
        original_copytree(source, destination)

    monkeypatch.setattr(steps.shutil, "copytree", flaky_copytree)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing.read_text(encoding="utf-8") == "old datasource\n"
    assert not (datasources / "BROKEN.yml").exists()
    assert not datasources.with_name(".datasources.tmp").exists()


def test_env_write_step_preserves_grafana_provisioning_on_partial_subdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["grafana"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    provisioning = cfg.config_dir / "grafana" / "provisioning"
    dashboards = provisioning / "dashboards"
    datasources = provisioning / "datasources"
    dashboards.mkdir(parents=True)
    datasources.mkdir(parents=True)
    existing_dashboard = dashboards / "dashboards.yml"
    existing_datasource = datasources / "agmind.yml"
    existing_dashboard.write_text("old dashboard provider\n", encoding="utf-8")
    existing_datasource.write_text("old datasource\n", encoding="utf-8")
    original_copytree = steps.shutil.copytree

    def flaky_copytree(source: Path, destination: Path, *args: object, **kwargs: object) -> None:
        del args, kwargs
        if source.name == "datasources":
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "BROKEN.yml").write_text("partial\n", encoding="utf-8")
            raise OSError("disk full")
        original_copytree(source, destination)

    monkeypatch.setattr(steps.shutil, "copytree", flaky_copytree)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing_dashboard.read_text(encoding="utf-8") == "old dashboard provider\n"
    assert existing_datasource.read_text(encoding="utf-8") == "old datasource\n"
    assert not (datasources / "BROKEN.yml").exists()
    assert not provisioning.with_name(".provisioning.tmp").exists()


def test_env_write_step_fails_and_preserves_grafana_when_template_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["grafana"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    provisioning = cfg.config_dir / "grafana" / "provisioning"
    dashboards = provisioning / "dashboards"
    datasources = provisioning / "datasources"
    dashboards.mkdir(parents=True)
    datasources.mkdir(parents=True)
    existing_dashboard = dashboards / "dashboards.yml"
    existing_datasource = datasources / "agmind.yml"
    existing_dashboard.write_text("old dashboard provider\n", encoding="utf-8")
    existing_datasource.write_text("old datasource\n", encoding="utf-8")
    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path / "missing-repo")

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files:" in result.message
    assert "required runtime template directory missing" in result.message
    assert existing_dashboard.read_text(encoding="utf-8") == "old dashboard provider\n"
    assert existing_datasource.read_text(encoding="utf-8") == "old datasource\n"
    assert not provisioning.with_name(".provisioning.tmp").exists()


@pytest.mark.parametrize(
    ("service_name", "config_file"),
    [
        ("loki", "loki.yml"),
        ("alloy", "config.alloy"),
    ],
)
def test_env_write_step_preserves_observability_config_dir_on_partial_file_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
    config_file: str,
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=[service_name],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    runtime_dir = cfg.config_dir / service_name
    runtime_dir.mkdir(parents=True)
    existing_config = runtime_dir / config_file
    existing_extra = runtime_dir / "extra.yml"
    existing_config.write_text("old config\n", encoding="utf-8")
    existing_extra.write_text("old extra\n", encoding="utf-8")

    fake_repo = tmp_path / "repo"
    template_dir = fake_repo / "templates" / "observability" / service_name
    template_dir.mkdir(parents=True)
    (template_dir / config_file).write_text("new config\n", encoding="utf-8")
    (template_dir / "extra.yml").write_text("new extra\n", encoding="utf-8")
    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", fake_repo)
    original_copy_file_atomic = steps._copy_file_atomic

    def flaky_copy_file_atomic(source: Path, target: Path) -> None:
        if source.name == "extra.yml":
            target.parent.mkdir(parents=True, exist_ok=True)
            (target.parent / "BROKEN.yml").write_text("partial\n", encoding="utf-8")
            raise OSError("disk full")
        original_copy_file_atomic(source, target)

    monkeypatch.setattr(steps, "_copy_file_atomic", flaky_copy_file_atomic)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing_config.read_text(encoding="utf-8") == "old config\n"
    assert existing_extra.read_text(encoding="utf-8") == "old extra\n"
    assert not (runtime_dir / "BROKEN.yml").exists()
    assert not runtime_dir.with_name(f".{service_name}.tmp").exists()


def test_env_write_step_preserves_prometheus_config_on_rules_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["prometheus"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    prometheus_dir = cfg.config_dir / "prometheus"
    rules_dir = prometheus_dir / "rules"
    rules_dir.mkdir(parents=True)
    existing_config = prometheus_dir / "prometheus.yml"
    existing_rule = rules_dir / "llama.yml"
    existing_config.write_text("old prometheus config\n", encoding="utf-8")
    existing_rule.write_text("old llama rule\n", encoding="utf-8")
    original_copytree_contents = steps._copytree_contents

    def flaky_copytree_contents(source: Path, target: Path) -> None:
        if source.name == "rules":
            target.mkdir(parents=True, exist_ok=True)
            (target / "BROKEN.yml").write_text("partial\n", encoding="utf-8")
            raise OSError("disk full")
        original_copytree_contents(source, target)

    monkeypatch.setattr(steps, "_copytree_contents", flaky_copytree_contents)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing_config.read_text(encoding="utf-8") == "old prometheus config\n"
    assert existing_rule.read_text(encoding="utf-8") == "old llama rule\n"
    assert not (rules_dir / "BROKEN.yml").exists()
    assert not prometheus_dir.with_name(".prometheus.tmp").exists()


def test_env_write_step_preserves_alertmanager_config_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["alertmanager"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    alertmanager_dir = cfg.config_dir / "alertmanager"
    alertmanager_dir.mkdir(parents=True)
    existing_config = alertmanager_dir / "alertmanager.yml"
    existing_config.write_text("old alertmanager config\n", encoding="utf-8")
    original_replace = steps._replace_path_atomic

    # The config is rendered+written into a staged .tmp dir, then atomically
    # swapped in via _replace_path_atomic. Fail at the swap so the staged dir is
    # fully populated but never committed — the rollback must clean it up and
    # leave the existing config untouched.
    def flaky_replace(staged: Path, target: Path) -> None:
        if target.name == "alertmanager":
            raise OSError("disk full")
        original_replace(staged, target)

    monkeypatch.setattr(steps, "_replace_path_atomic", flaky_replace)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing_config.read_text(encoding="utf-8") == "old alertmanager config\n"
    assert not alertmanager_dir.with_name(".alertmanager.tmp").exists()


def test_env_write_step_preserves_traefik_dynamic_config_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["traefik"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    dynamic_dir = cfg.models_dir.parent / "traefik" / "dynamic"
    dynamic_dir.mkdir(parents=True)
    existing_transport = dynamic_dir / "transport.yml"
    existing_middleware = dynamic_dir / "middlewares.yml"
    existing_transport.write_text("old transport\n", encoding="utf-8")
    existing_middleware.write_text("old middleware\n", encoding="utf-8")
    original_copy_file_atomic = steps._copy_file_atomic

    def flaky_copy_file_atomic(source: Path, target: Path) -> None:
        if source.name == "middlewares.yml":
            target.parent.mkdir(parents=True, exist_ok=True)
            (target.parent / "BROKEN.yml").write_text("partial\n", encoding="utf-8")
            raise OSError("disk full")
        original_copy_file_atomic(source, target)

    monkeypatch.setattr(steps, "_copy_file_atomic", flaky_copy_file_atomic)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert not result.success
    assert "cannot write runtime files: disk full" in result.message
    assert existing_transport.read_text(encoding="utf-8") == "old transport\n"
    assert existing_middleware.read_text(encoding="utf-8") == "old middleware\n"
    assert not (dynamic_dir / "BROKEN.yml").exists()
    assert not dynamic_dir.with_name(".dynamic.tmp").exists()


def test_env_write_step_unlinks_stale_runtime_stage_directory_symlink(tmp_path: Path) -> None:
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["traefik"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    dynamic_dir = cfg.models_dir.parent / "traefik" / "dynamic"
    stale_stage = dynamic_dir.with_name(".dynamic.tmp")
    attacker_dir = tmp_path / "attacker-controlled-stage"
    attacker_dir.mkdir(parents=True)
    stale_stage.parent.mkdir(parents=True)
    stale_stage.symlink_to(attacker_dir, target_is_directory=True)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success
    assert dynamic_dir.is_dir()
    assert not dynamic_dir.is_symlink()
    assert not stale_stage.exists()
    assert not any(attacker_dir.iterdir())


def test_env_write_step_uses_sudo_when_runtime_paths_are_root_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean TUI install must survive root-owned /opt, /var/lib, and /etc paths."""
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="cf-token-" + "X" * 40,
        services=["traefik", "prometheus", "grafana", "loki", "alloy", "alertmanager"],
        install_dir=tmp_path / "root" / "opt" / "agmind",
        models_dir=tmp_path / "root" / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "root" / "etc" / "agmind",
        sudo_password="sup3rs3cret",
    )
    calls: list[dict[str, object]] = []

    def deny_local_write(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("root-owned path")

    def fake_stream_subprocess(
        cmd: list[str],
        callback: ProgressCallback,
        step_id: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_payload: bytes | None = None,
        extra_emit: object | None = None,
        cancel_event: object | None = None,
    ) -> tuple[int, list[str]]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "stdin_payload": stdin_payload,
                "extra_emit": extra_emit,
            }
        )
        return 0, []

    monkeypatch.setattr(steps, "_write_runtime_payload_local", deny_local_write)
    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream_subprocess)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success
    assert calls
    assert all(call["cmd"][:5] == ["sudo", "-S", "-p", "", "--"] for call in calls)
    assert all(call["stdin_payload"] == b"sup3rs3cret\n" for call in calls)
    assert all("sup3rs3cret" not in str(part) for call in calls for part in call["cmd"])


def test_env_write_step_sudo_rejects_existing_runtime_secret_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-owned runtime writes must not copy secrets through existing symlinks."""
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="cf-token-" + "X" * 40,
        services=["traefik"],
        install_dir=tmp_path / "root" / "opt" / "agmind",
        models_dir=tmp_path / "root" / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "root" / "etc" / "agmind",
        sudo_password="sup3rs3cret",
    )
    secret_dir = cfg.models_dir.parent / "secrets"
    calls: list[list[str]] = []

    def deny_local_write(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("root-owned path")

    def fake_stream_subprocess(
        cmd: list[str],
        callback: ProgressCallback,
        step_id: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_payload: bytes | None = None,
        extra_emit: object | None = None,
        cancel_event: object | None = None,
    ) -> tuple[int, list[str]]:
        del callback, step_id, cwd, env, stdin_payload, extra_emit
        calls.append(cmd)
        if str(secret_dir) in cmd and "agmind-runtime-target-guard" in cmd:
            return 1, [f"runtime directory target must not be a symlink: {secret_dir}"]
        return 0, []

    monkeypatch.setattr(steps, "_write_runtime_payload_local", deny_local_write)
    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream_subprocess)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert "runtime directory target must not be a symlink" in result.message
    assert not any(call[5:7] == ["cp", "-R"] for call in calls)


def test_env_write_step_reads_existing_env_via_sudo_when_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rerun must preserve existing runtime secrets even when .env is root-owned."""
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
        sudo_password="sup3rs3cret",
    )
    calls: list[dict[str, object]] = []

    def fake_parse_env_file(path: object) -> dict[str, str]:
        if Path(path) == cfg.install_dir / ".env":
            raise PermissionError("root-owned env")
        return {}

    def fake_run(
        cmd: list[str],
        cwd: Path | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "check": check,
                "input": input,
            }
        )
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="POSTGRES_PASSWORD=existing-postgres\nREDIS_PASSWORD=existing-redis\n",
            stderr="",
        )

    monkeypatch.setattr(steps, "parse_env_file", fake_parse_env_file)
    monkeypatch.setattr(steps.subprocess, "run", fake_run)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success
    text = (cfg.install_dir / ".env").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=existing-postgres" in text
    assert "REDIS_PASSWORD=existing-redis" in text
    assert calls[0]["cmd"] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "cat",
        str(cfg.install_dir / ".env"),
    ]
    assert calls[0]["input"] == "sup3rs3cret\n"
    assert "sup3rs3cret" not in calls[0]["cmd"]


def test_env_write_step_writes_file(tmp_path: object) -> None:
    """EnvWriteStep creates .env с правильными vars."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
        model_repo="r",
        model_file="model.gguf",
        ctx_size=32768,
        kv_cache_type="q4_0",
    )
    events: list[ProgressEvent] = []
    result = EnvWriteStep().run(events.append, cfg)
    assert result.success
    env_file = cfg.install_dir / ".env"
    text = env_file.read_text()
    assert "AGMIND_DOMAIN=lab.example.com" in text
    assert "AGMIND_MODEL_FILE=model.gguf" in text
    assert "AGMIND_CTX_SIZE=32768" in text
    assert "AGMIND_KV_CACHE=q4_0" in text
    version_env = cfg.install_dir / "version.env"
    version_text = version_env.read_text(encoding="utf-8")
    assert "LLAMA_LLM_VERSION=server-vulkan-b9049" in version_text
    assert "LLAMA_LLM_VERSION_IMAGE=ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049" in version_text
    assert "UPTIME_KUMA_VERSION=" not in version_text
    assert "HOMARR_VERSION=" not in version_text
    assert "WATCHTOWER_VERSION=" not in version_text
    assert "DOZZLE_VERSION=" not in version_text
    assert "NETDATA_VERSION=" not in version_text
    parsed = parse_env_file(env_file)
    for key in (
        "POSTGRES_PASSWORD",
        "GRAFANA_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "REDIS_PASSWORD",
        "N8N_ENCRYPTION_KEY",
        "HOMARR_SECRET_ENCRYPTION_KEY",
    ):
        assert parsed[key]
    # homarr aborts at boot unless SECRET_ENCRYPTION_KEY is EXACTLY 64 hex chars
    # (the base64 token_urlsafe output is 43 non-hex chars -> "Invalid environment
    # variables" 500). Live-deploy regression 2026-06-02.
    homarr_key = parsed["HOMARR_SECRET_ENCRYPTION_KEY"]
    assert len(homarr_key) == 64, f"homarr key must be 64 chars, got {len(homarr_key)}"
    assert all(c in "0123456789abcdef" for c in homarr_key), "homarr key must be hex"
    assert parsed["MINIO_ROOT_USER"] == "agmind"
    assert parsed["N8N_TIMEZONE"] == "UTC"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_env_write_step_removes_cloudflare_token_file_on_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="super-secret-cloudflare-token",
        services=["traefik"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "data" / "models",
        config_dir=tmp_path / "config",
    )
    from agmind.core import files as files_mod

    original_chmod = files_mod.os.chmod

    def fail_token_chmod(target: object, mode: int, **kwargs: object) -> None:
        # write_text_atomic chmods the unique temp (".cf_dns_api_token.<rand>.tmp")
        # before the atomic replace; the random suffix keeps the secret name in
        # the path, so match on the substring and fail only for the token file.
        # NB: os.chmod IS the module attribute pathlib.Path.chmod also calls, so
        # forward keyword args (e.g. follow_symlinks) for non-token chmods.
        if "cf_dns_api_token" in Path(os.fspath(target)).name:
            raise PermissionError("chmod denied")
        original_chmod(target, mode, **kwargs)

    monkeypatch.setattr(files_mod.os, "chmod", fail_token_chmod)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    secret_file = tmp_path / "data" / "secrets" / "cf_dns_api_token"
    assert result.success is False
    assert "chmod denied" in result.message
    assert not secret_file.exists()
    assert not any(secret_file.parent.glob("*cf_dns_api_token*"))


def test_env_write_step_rejects_cloudflare_secret_directory_symlink(tmp_path: Path) -> None:
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="super-secret-cloudflare-token",
        services=["traefik"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "data" / "models",
        config_dir=tmp_path / "config",
    )
    real_secret_dir = tmp_path / "attacker-controlled"
    real_secret_dir.mkdir()
    secret_dir = tmp_path / "data" / "secrets"
    secret_dir.parent.mkdir(parents=True)
    secret_dir.symlink_to(real_secret_dir, target_is_directory=True)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert "runtime secret directory must be a real directory" in result.message
    assert not (real_secret_dir / "cf_dns_api_token").exists()
    assert secret_dir.is_symlink()


def test_env_write_step_version_manifest_tracks_selected_operator_services(
    tmp_path: object,
) -> None:
    """version.env records selected operator tools without catalog-wide noise."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["uptime-kuma", "homarr", "watchtower", "dozzle", "netdata"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
    )

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success
    version_text = (cfg.install_dir / "version.env").read_text(encoding="utf-8")
    assert "UPTIME_KUMA_VERSION=2.3.2" in version_text
    assert "HOMARR_VERSION=v1.62.0" in version_text
    assert "WATCHTOWER_VERSION=1.7.1" in version_text
    assert "DOZZLE_VERSION=v10.6.1" in version_text
    assert "NETDATA_VERSION=v2.10.3" in version_text
    assert "LLAMA_LLM_VERSION=" not in version_text


def test_runtime_version_env_example_tracks_operator_service_pins() -> None:
    """Repository example mirrors the generated /opt/agmind/version.env shape."""
    from agmind.install.steps import _image_tag, _version_key
    from agmind.services.renderer import REPO_ROOT, load_descriptors

    example = REPO_ROOT / "templates" / "runtime" / "version.env.example"
    text = example.read_text(encoding="utf-8")
    descriptors = load_descriptors()

    assert "/opt/agmind/version.env" in text
    for service_name in ("uptime-kuma", "homarr", "watchtower", "dozzle", "netdata"):
        descriptor = descriptors[service_name]
        key = _version_key(service_name)
        assert f"{key}={_image_tag(descriptor.image)}" in text
        assert f"{key}_IMAGE={descriptor.image}" in text
        assert f"{key}_DIGEST=sha256:{descriptor.digest}" in text


def test_runtime_version_image_tag_parses_registry_ports_and_inline_digests() -> None:
    from agmind.install.steps import _image_tag

    assert _image_tag("registry.internal:5000/demo/service:v1.2.3") == "v1.2.3"
    assert _image_tag("registry.internal:5000/demo/service") == ""
    assert _image_tag("example/service:v1.2.3@sha256:abc123") == "v1.2.3"
    assert _image_tag("example/service@sha256:abc123") == ""


def test_runtime_version_env_records_inline_image_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.steps import _runtime_version_env
    from agmind.schemas import ServiceDescriptor
    from agmind.services import renderer

    digest = "a" * 64
    descriptor = ServiceDescriptor.model_validate(
        {
            "name": "inline-digest",
            "image": f"example/inline:v1.2.3@sha256:{digest}",
            "tier": "ops",
            "purpose": "Inline digest fixture",
        }
    )

    monkeypatch.setattr(renderer, "load_descriptors", lambda: {"inline-digest": descriptor})

    text = _runtime_version_env(["inline-digest"])

    assert "INLINE_DIGEST_VERSION=v1.2.3" in text
    assert f"INLINE_DIGEST_VERSION_IMAGE=example/inline:v1.2.3@sha256:{digest}" in text
    assert f"INLINE_DIGEST_VERSION_DIGEST=sha256:{digest}" in text


def test_runtime_version_env_keeps_digest_only_selected_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.steps import _runtime_version_env
    from agmind.schemas import ServiceDescriptor
    from agmind.services import renderer

    digest = "b" * 64
    descriptor = ServiceDescriptor.model_validate(
        {
            "name": "digest-only",
            "image": f"example/digest-only@sha256:{digest}",
            "tier": "ops",
            "purpose": "Digest-only fixture",
        }
    )

    monkeypatch.setattr(renderer, "load_descriptors", lambda: {"digest-only": descriptor})

    text = _runtime_version_env(["digest-only"])

    assert "DIGEST_ONLY_VERSION=\n" in text
    assert f"DIGEST_ONLY_VERSION_IMAGE=example/digest-only@sha256:{digest}" in text
    assert f"DIGEST_ONLY_VERSION_DIGEST=sha256:{digest}" in text


def test_env_write_step_reports_unknown_service_without_traceback(tmp_path: object) -> None:
    """Invalid service selection should fail the step cleanly before deploy."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["missing-service"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
    )

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert "unknown selected services" in result.message
    assert "Traceback" not in result.message


def test_env_write_step_validates_services_before_reading_existing_env(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad service selection should not be masked by root-owned runtime .env reads."""
    from agmind.install import steps
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["missing-service"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
    )

    def fail_existing_env_read(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise AssertionError("existing runtime env should not be read")

    monkeypatch.setattr(steps, "_parse_existing_runtime_env", fail_existing_env_read)

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert "unknown selected services" in result.message


def test_env_write_step_rejects_empty_service_selection(tmp_path: object) -> None:
    """Empty service selection must not emit catalog-wide runtime manifests."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=[],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
    )

    result = EnvWriteStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert "no selected services for version.env" in result.message
    assert not (cfg.install_dir / ".env").exists()
    assert not (cfg.install_dir / "version.env").exists()


def test_env_write_step_preserves_existing_runtime_service_secrets(tmp_path: object) -> None:
    """Rerunning install must not rotate database/object-store passwords."""
    from agmind.install.steps import EnvWriteStep

    install_dir = tmp_path / "opt"  # type: ignore[operator]
    install_dir.mkdir()
    env_file = install_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "POSTGRES_PASSWORD=existing-postgres",
                "GRAFANA_PASSWORD=existing-grafana",
                "MYSQL_ROOT_PASSWORD=existing-mysql",
                "MINIO_ROOT_USER=existing-minio",
                "MINIO_ROOT_PASSWORD=existing-minio-password",
                "REDIS_PASSWORD=existing-redis",
                "N8N_ENCRYPTION_KEY=existing-n8n-key",
                "HOMARR_SECRET_ENCRYPTION_KEY=existing-homarr-key",
                "N8N_TIMEZONE=Europe/Bucharest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=install_dir,
    )
    events: list[ProgressEvent] = []
    result = EnvWriteStep().run(events.append, cfg)

    assert result.success
    parsed = parse_env_file(env_file)
    assert parsed["POSTGRES_PASSWORD"] == "existing-postgres"
    assert parsed["GRAFANA_PASSWORD"] == "existing-grafana"
    assert parsed["MYSQL_ROOT_PASSWORD"] == "existing-mysql"
    assert parsed["MINIO_ROOT_USER"] == "existing-minio"
    assert parsed["MINIO_ROOT_PASSWORD"] == "existing-minio-password"
    assert parsed["REDIS_PASSWORD"] == "existing-redis"
    assert parsed["N8N_ENCRYPTION_KEY"] == "existing-n8n-key"
    assert parsed["HOMARR_SECRET_ENCRYPTION_KEY"] == "existing-homarr-key"
    assert parsed["N8N_TIMEZONE"] == "Europe/Bucharest"


def test_env_write_step_writes_each_runtime_key_once(tmp_path: object) -> None:
    """Duplicate .env keys make production config reviews ambiguous."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
    )
    events: list[ProgressEvent] = []
    result = EnvWriteStep().run(events.append, cfg)

    assert result.success
    keys = [
        line.split("=", 1)[0]
        for line in (cfg.install_dir / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicates == []


def test_install_config_carries_ctx_kv(tmp_path: object) -> None:
    cfg = _make_config(tmp_path)  # type: ignore[arg-type]
    assert cfg.ctx_size == 16384
    assert cfg.kv_cache_type == "q8_0"
    payload = cfg.redact()
    assert payload["ctx_size"] == 16384
    assert payload["kv_cache_type"] == "q8_0"


# ---------- M5.1: embed/rerank на InstallConfig ----------


def test_install_config_has_embed_rerank_defaults(tmp_path: object) -> None:
    cfg = _make_config(tmp_path)  # type: ignore[arg-type]
    assert cfg.embed_ctx_size == 8192
    assert cfg.embed_kv_cache == "f16"
    assert cfg.embed_parallel == 4
    assert cfg.rerank_ctx_size == 2048
    # repo/file = None по дефолту = skip download
    assert cfg.embed_repo is None
    assert cfg.embed_file is None
    assert cfg.rerank_repo is None
    assert cfg.rerank_file is None


def test_env_write_step_separates_llm_embed_rerank(tmp_path: object) -> None:
    """M5.2: EnvWriteStep пишет separate AGMIND_LLM_* / EMBED_* / RERANK_* vars."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
        model_repo="llmrepo",
        model_file="llm.gguf",
        ctx_size=32768,
        kv_cache_type="q4_0",
        threads=8,
        parallel_slots=2,
        embed_repo="embedrepo",
        embed_file="bge.gguf",
        embed_ctx_size=8192,
        embed_kv_cache="f16",
        embed_parallel=8,
        rerank_repo="rerankrepo",
        rerank_file="rr.gguf",
        rerank_ctx_size=1024,
    )
    events: list[ProgressEvent] = []
    result = EnvWriteStep().run(events.append, cfg)
    assert result.success
    env_file = cfg.install_dir / ".env"
    text = env_file.read_text()
    # LLM
    assert "AGMIND_MODEL_FILE=llm.gguf" in text
    assert "AGMIND_LLM_CTX_SIZE=32768" in text
    assert "AGMIND_LLM_KV_CACHE=q4_0" in text
    assert "AGMIND_LLM_THREADS=8" in text
    assert "AGMIND_LLM_PARALLEL=2" in text
    # Embed
    assert "AGMIND_EMBED_FILE=bge.gguf" in text
    assert "AGMIND_EMBED_CTX_SIZE=8192" in text
    assert "AGMIND_EMBED_KV_CACHE=f16" in text
    assert "AGMIND_EMBED_PARALLEL=8" in text
    # Rerank
    assert "AGMIND_RERANK_FILE=rr.gguf" in text
    assert "AGMIND_RERANK_CTX_SIZE=1024" in text
    # Backward-compat legacy aliases preserved
    assert "AGMIND_CTX_SIZE=32768" in text
    assert "AGMIND_KV_CACHE=q4_0" in text


def test_model_download_step_handles_empty_embed_rerank(tmp_path: object) -> None:
    """Download step должен skip embed/rerank когда file пуст — без curl call."""
    from agmind.install.steps import ModelDownloadStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
        models_dir=tmp_path / "models",  # type: ignore[operator]
        model_repo=None,
        model_file=None,
        embed_repo=None,
        embed_file=None,
        rerank_repo=None,
        rerank_file=None,
    )
    events: list[ProgressEvent] = []
    result = ModelDownloadStep().run(events.append, cfg)
    assert result.success
    assert "llm: no model" in result.message
    assert "embed: no model" in result.message
    assert "rerank: no model" in result.message


def test_image_pull_step_uses_runtime_env_from_install_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose pull parses ${VAR:?} guards and must see install .env values."""
    from agmind.install import steps
    from agmind.install.steps import ImagePullStep

    install_dir = tmp_path / "opt"
    install_dir.mkdir()
    (install_dir / ".env").write_text(
        "\n".join(
            [
                "POSTGRES_PASSWORD=existing-postgres",
                "REDIS_PASSWORD=existing-redis",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["postgres"],
        install_dir=install_dir,
    )
    calls: list[dict[str, object]] = []
    env_texts: list[str] = []

    monkeypatch.setattr(
        "agmind.services.renderer.render_to_string",
        lambda **_kwargs: (
            "services:\n"
            "  postgres:\n"
            "    image: postgres:17.6-alpine\n"
            "    environment:\n"
            "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}\n"
        ),
    )

    def fake_stream_subprocess(
        cmd: list[str],
        callback: ProgressCallback,
        step_id: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_payload: bytes | None = None,
        extra_emit: object | None = None,
        cancel_event: object | None = None,
    ) -> tuple[int, list[str]]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "stdin_payload": stdin_payload,
                "extra_emit": extra_emit,
            }
        )
        assert cwd is not None
        env_texts.append((cwd / ".env").read_text(encoding="utf-8"))
        return 0, []

    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream_subprocess)

    result = ImagePullStep().run(lambda _event: None, cfg)

    assert result.success
    # Streamed pull: no --quiet (it froze the bar), --progress plain as a global flag.
    assert calls[0]["cmd"] == [
        "docker",
        "compose",
        "--progress",
        "plain",
        "--env-file",
        str(calls[0]["cwd"] / ".env"),  # type: ignore[operator]
        "pull",
        "--policy",
        "missing",
    ]
    assert calls[0]["env"] is None
    assert "POSTGRES_PASSWORD=existing-postgres" in env_texts[0]
    assert "REDIS_PASSWORD=existing-redis" in env_texts[0]


def test_compose_config_step_validates_render_with_runtime_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean TUI install must validate exact Compose config before pulling images."""
    from agmind.install import steps
    from agmind.install.steps import ComposeConfigStep

    install_dir = tmp_path / "opt"
    install_dir.mkdir()
    (install_dir / ".env").write_text(
        "POSTGRES_PASSWORD=existing-postgres\n",
        encoding="utf-8",
    )
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["postgres"],
        install_dir=install_dir,
    )
    calls: list[dict[str, object]] = []
    env_texts: list[str] = []

    monkeypatch.setattr(
        "agmind.services.renderer.render_to_string",
        lambda **_kwargs: "services:\n  postgres:\n    image: postgres:17.6-alpine\n",
    )

    def fake_stream_subprocess(
        cmd: list[str],
        callback: ProgressCallback,
        step_id: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_payload: bytes | None = None,
        extra_emit: object | None = None,
        cancel_event: object | None = None,
    ) -> tuple[int, list[str]]:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "stdin_payload": stdin_payload,
                "extra_emit": extra_emit,
            }
        )
        assert cwd is not None
        env_texts.append((cwd / ".env").read_text(encoding="utf-8"))
        return 0, []

    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream_subprocess)

    result = ComposeConfigStep().run(lambda _event: None, cfg)

    assert result.success
    assert calls[0]["cmd"] == [
        "docker",
        "compose",
        "--env-file",
        str(calls[0]["cwd"] / ".env"),  # type: ignore[operator]
        "config",
        "--quiet",
    ]
    assert calls[0]["env"] is None
    assert env_texts[0] == "POSTGRES_PASSWORD=existing-postgres\n"


def test_compose_config_and_image_pull_use_sudo_safe_runtime_env_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import ComposeConfigStep, ImagePullStep

    install_dir = tmp_path / "opt"
    install_dir.mkdir()
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["postgres"],
        install_dir=install_dir,
        sudo_password="sup3rs3cret",
    )
    calls: list[dict[str, object]] = []
    env_paths: list[tuple[str | None, Path]] = []
    env_texts: list[str] = []

    monkeypatch.setattr(
        "agmind.services.renderer.render_to_string",
        lambda **_kwargs: "services: {}",
    )

    def fake_runtime_env(config: InstallConfig, path: Path) -> dict[str, str]:
        env_paths.append((config.sudo_password, path))
        return {"POSTGRES_PASSWORD": "existing-postgres"}

    def fake_stream_subprocess(
        cmd: list[str],
        callback: ProgressCallback,
        step_id: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_payload: bytes | None = None,
        extra_emit: object | None = None,
        cancel_event: object | None = None,
    ) -> tuple[int, list[str]]:
        calls.append({"cmd": cmd, "cwd": cwd, "env": env, "stdin_payload": stdin_payload})
        assert cwd is not None
        env_texts.append((cwd / ".env").read_text(encoding="utf-8"))
        return 0, []

    monkeypatch.setattr(steps, "_parse_existing_runtime_env", fake_runtime_env)
    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream_subprocess)
    monkeypatch.setattr(steps, "_user_docker_config_dir", lambda: None)

    assert ComposeConfigStep().run(lambda _event: None, cfg).success
    assert ImagePullStep().run(lambda _event: None, cfg).success

    assert env_paths == [
        ("sup3rs3cret", install_dir / ".env"),
        ("sup3rs3cret", install_dir / ".env"),
    ]
    assert calls[0]["cmd"] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "docker",
        "compose",
        "--env-file",
        str(calls[0]["cwd"] / ".env"),  # type: ignore[operator]
        "config",
        "--quiet",
    ]
    assert calls[1]["cmd"] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "docker",
        "compose",
        "--progress",
        "plain",
        "--env-file",
        str(calls[1]["cwd"] / ".env"),  # type: ignore[operator]
        "pull",
        "--policy",
        "missing",
    ]
    assert calls[0]["env"] is None
    assert calls[1]["env"] is None
    assert env_texts == [
        "POSTGRES_PASSWORD=existing-postgres\n",
        "POSTGRES_PASSWORD=existing-postgres\n",
    ]
    assert calls[0]["stdin_payload"] is not None
    assert calls[1]["stdin_payload"] is not None


def test_compose_config_and_image_pull_reject_empty_selection_before_runtime_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone install steps must not treat services=[] as the full catalog."""
    from agmind.install import steps
    from agmind.install.steps import ComposeConfigStep, ImagePullStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
        install_dir=tmp_path / "opt",
    )

    def fail_render(**_kwargs: object) -> str:
        raise AssertionError("compose render should not run")

    def fail_env_read(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise AssertionError("runtime env should not be read")

    def fail_stream(*_args: object, **_kwargs: object) -> tuple[int, list[str]]:
        raise AssertionError("docker compose should not run")

    monkeypatch.setattr("agmind.services.renderer.render_to_string", fail_render)
    monkeypatch.setattr(steps, "_parse_existing_runtime_env", fail_env_read)
    monkeypatch.setattr(steps, "_stream_subprocess", fail_stream)

    compose_result = ComposeConfigStep().run(lambda _event: None, cfg)
    pull_result = ImagePullStep().run(lambda _event: None, cfg)

    assert compose_result.success is False
    assert compose_result.message == "no selected services for compose config"
    assert pull_result.success is False
    assert pull_result.message == "no selected services for image pull"


def test_default_steps_validate_compose_before_real_image_pull() -> None:
    from agmind.install.steps import default_steps

    step_ids = [step.step_id for step in default_steps()]

    assert "compose_config" in step_ids
    assert step_ids.index("env_write") < step_ids.index("compose_config")
    assert "image_pull" not in step_ids
    assert step_ids.index("compose_config") < step_ids.index("deploy")


def test_default_steps_all_have_label() -> None:
    from agmind.install.steps import default_steps

    for step in default_steps():
        assert step.label, f"step {step.step_id} missing label"


def test_deploy_step_uses_selected_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.deploy.runner import DeployResult
    from agmind.install.steps import DeployStep

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm", "qdrant"],
        install_dir=tmp_path / "opt",
    )
    events: list[ProgressEvent] = []

    result = DeployStep().run(events.append, cfg)

    assert result.success
    assert calls["profiles"] == []
    assert calls["services"] == ["llama-llm", "qdrant"]
    # D-02 (Phase 13): the deploy-state.json writer only fires when apply=True — this
    # guards against a future DeployStep regression accidentally passing apply=False,
    # which would silently skip recording the deploy-state for the install path.
    assert calls["apply"] is True


def test_deploy_step_uses_generous_healthcheck_timeout_for_model_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-run deploy must wait long enough for a multi-GB LLM to load before
    declaring the stack unhealthy and rolling back (BREA02). The runner default of
    300s is too short for a 35B GGUF load on first start, causing a false rollback of
    an otherwise-healthy deploy."""
    from agmind.deploy.runner import DeployResult
    from agmind.install.steps import DeployStep

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm", "qdrant"],
        install_dir=tmp_path / "opt",
    )

    result = DeployStep().run(lambda _e: None, cfg)

    assert result.success
    timeout = calls.get("healthcheck_timeout")
    assert isinstance(timeout, int), "DeployStep must pass an explicit healthcheck_timeout"
    assert timeout >= 900, f"healthcheck_timeout={timeout} too short for first model load"


def test_healthcheck_timeout_for_heavy_model_selection() -> None:
    """A selection containing a 600s-start_period llama server must drive the
    deploy healthcheck budget to start_period + 600s load margin (>= 1200s),
    well past the 900s floor, with the slow service named as the driver."""
    from agmind.install.steps import _healthcheck_timeout_for

    timeout, driver = _healthcheck_timeout_for(["llama-llm", "qdrant"])
    assert timeout == 1200, timeout
    assert driver == "llama-llm"


def test_healthcheck_timeout_for_light_selection_uses_floor() -> None:
    """A light selection whose slowest start_period + 600 is below the 900s floor
    falls back to the floor (qdrant start_period is 10s -> 610 < 900)."""
    from agmind.install.steps import _healthcheck_timeout_for

    timeout, _driver = _healthcheck_timeout_for(["qdrant"])
    assert timeout == 900, timeout


def test_healthcheck_timeout_for_empty_or_unknown_uses_floor() -> None:
    """No registered service / no start_period -> the 900s floor still applies."""
    from agmind.install.steps import _healthcheck_timeout_for

    timeout, driver = _healthcheck_timeout_for([])
    assert timeout == 900
    assert driver is None
    timeout, _driver = _healthcheck_timeout_for(["not-a-real-service"])
    assert timeout == 900


def test_deploy_step_logs_data_driven_healthcheck_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeployStep sizes the healthcheck budget from the slowest selected service's
    start_period and LOGs the chosen value + driver for operator visibility."""
    from agmind.deploy.runner import DeployResult
    from agmind.install.steps import DeployStep

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["llama-llm", "qdrant"],
        install_dir=tmp_path / "opt",
    )
    events: list[ProgressEvent] = []

    result = DeployStep().run(events.append, cfg)

    assert result.success
    assert calls.get("healthcheck_timeout") == 1200
    logs = "\n".join(event.text for event in events if event.kind is ProgressKind.LOG)
    assert "healthcheck timeout: 1200 s" in logs
    assert "llama-llm" in logs


def test_deploy_step_rejects_empty_service_selection_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.steps import DeployStep

    def fail_deploy(**_kwargs: object) -> object:
        raise AssertionError("deploy runner should not run")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fail_deploy)
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
        install_dir=tmp_path / "opt",
    )

    result = DeployStep().run(lambda _event: None, cfg)

    assert result.success is False
    assert result.message == "no selected services for deploy"


def test_deploy_step_redacts_install_secrets_from_progress_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.deploy.runner import DeployResult
    from agmind.install.steps import DeployStep

    token = "cf-secret-token-" + "X" * 32
    sudo_password = "sudo-secret-password"

    def fake_deploy(**kwargs: object) -> DeployResult:
        progress = kwargs["progress"]
        assert callable(progress)
        progress("apply", f"using token={token} sudo={sudo_password}")
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)
    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token=token,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
        sudo_password=sudo_password,
    )
    events: list[ProgressEvent] = []

    result = DeployStep().run(events.append, cfg)

    logs = "\n".join(event.text for event in events if event.kind is ProgressKind.LOG)
    assert result.success
    assert token not in logs
    assert sudo_password not in logs
    assert "***" in logs


# ---------- DoctorStep — runs real preflight (always present) ----------


def test_doctor_step_returns_result(tmp_path: Path) -> None:
    from agmind.install.steps import DoctorStep

    events: list[ProgressEvent] = []
    result = DoctorStep().run(events.append, _make_config(tmp_path))
    assert result.step_id == "doctor"
    # Не assertим success/failure — зависит от железа теста.
    assert isinstance(result.message, str)
    assert isinstance(result.elapsed.total_seconds(), float)


# ---------- ModelDownloadStep — skip path ----------


def test_model_download_skipped_when_no_repo(tmp_path: Path) -> None:
    from agmind.install.steps import ModelDownloadStep

    cfg = _make_config(tmp_path)
    cfg.model_repo = None
    cfg.model_file = None
    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success is True
    assert "skip" in result.message.lower()


def test_model_download_rejects_model_path_traversal(tmp_path: Path) -> None:
    from agmind.install.steps import ModelDownloadStep

    cfg = _make_config(tmp_path)
    cfg.model_repo = "example/repo"
    cfg.model_file = "../../escape.gguf"

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert result.success is False
    assert "model file" in result.message
    assert not (cfg.models_dir.parent / "escape.gguf").exists()


def test_model_download_rejects_unsafe_repo(tmp_path: Path) -> None:
    from agmind.install.steps import ModelDownloadStep

    cfg = _make_config(tmp_path)
    cfg.model_repo = "https://evil.example/repo"
    cfg.model_file = "model.gguf"

    result = ModelDownloadStep().run(lambda _e: None, cfg)

    assert result.success is False
    assert "HF repo" in result.message


def test_model_download_idempotent_if_present(tmp_path: Path) -> None:
    from agmind.install.steps import ModelDownloadStep

    cfg = _make_config(tmp_path)
    cfg.model_repo = "fake/repo"
    cfg.model_file = "model.gguf"
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    (cfg.models_dir / "model.gguf").write_bytes(b"\x00" * (200 * 1024 * 1024))
    result = ModelDownloadStep().run(lambda _e: None, cfg)
    assert result.success is True
    # Phase N.H rename: "already present" → "reused" (semantic same)
    assert "reused" in result.message.lower() or "already present" in result.message


# ---------- BootstrapStep — no sudo password rejected ----------


def test_bootstrap_rejects_missing_sudo_password(tmp_path: Path) -> None:
    from agmind.install.steps import BootstrapStep

    cfg = _make_config(tmp_path)
    cfg.sudo_password = None
    result = BootstrapStep().run(lambda _e: None, cfg)
    assert result.success is False
    assert "sudo password" in result.message.lower()


def test_bootstrap_redacts_install_secrets_from_playbook_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "install.yml").write_text("---\n", encoding="utf-8")
    token = "cf-secret-token-" + "X" * 32
    sudo_password = "sudo-secret-password"

    class FakeProc:
        stdout = _FakeStdout(
            [
                f"TASK token={token}\n",
                f"BECOME password={sudo_password}\n",
                f"COMBINED token={token} sudo={sudo_password}\n",
            ]
        )

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(*_args: object, **_kwargs: object) -> FakeProc:
        return FakeProc()

    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        steps,
        "resolve_ansible_command",
        lambda name: f"/venv/bin/{name}",
    )
    cfg = _make_config(tmp_path)
    cfg.cf_api_token = token
    cfg.sudo_password = sudo_password
    events: list[ProgressEvent] = []

    result = BootstrapStep().run(events.append, cfg)

    logs = "\n".join(event.text for event in events if event.kind is ProgressKind.LOG)
    assert result.success is True
    assert token not in logs
    assert sudo_password not in logs
    assert "***" in logs


def test_bootstrap_passes_cf_token_outside_process_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CF token must not be visible in process argv while ansible is running."""
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "install.yml").write_text("---\n", encoding="utf-8")
    token = "cf-secret-token-" + "X" * 32
    captured: dict[str, object] = {}

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        extra_vars = cmd[cmd.index("--extra-vars") + 1]
        captured["extra_vars_arg"] = extra_vars
        if isinstance(extra_vars, str) and extra_vars.startswith("@"):
            path = extra_vars[1:]
            captured["extra_vars_path"] = path
            captured["extra_vars_payload"] = Path(path).read_text(encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(steps, "resolve_ansible_command", lambda name: f"/venv/bin/{name}")

    cfg = _make_config(tmp_path)
    cfg.domain = "lab.example.com"
    cfg.cf_api_token = token

    result = BootstrapStep().run(lambda _e: None, cfg)

    argv = "\0".join(str(part) for part in captured["cmd"])
    kwargs = str(captured["kwargs"])
    assert result.success is True
    assert token not in argv
    assert token not in kwargs
    assert "--extra-vars" in captured["cmd"]
    # extra-vars must be a REAL file path, never a /dev/fd/N pipe: Ansible
    # realpath-canonicalizes the @file and cannot resolve a pipe FD.
    extra_vars_arg = str(captured["extra_vars_arg"])
    assert extra_vars_arg.startswith("@")
    assert "/dev/fd/" not in argv
    payload = str(captured["extra_vars_payload"])
    assert "agmind_cf_api_token" in payload
    assert token in payload
    assert "lab.example.com" in payload
    # the secret-bearing temp file is removed once the step finishes
    assert not Path(captured["extra_vars_path"]).exists()


def test_bootstrap_extra_vars_payload_edge_enabled_with_traefik(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agmind_edge_enabled must be true in the extra-vars payload when traefik is selected,
    so ansible/install.yml's domain/CF-token asserts stay active for an edge install."""
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "install.yml").write_text("---\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        extra_vars = cmd[cmd.index("--extra-vars") + 1]
        if isinstance(extra_vars, str) and extra_vars.startswith("@"):
            captured["extra_vars_payload"] = Path(extra_vars[1:]).read_text(encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(steps, "resolve_ansible_command", lambda name: f"/venv/bin/{name}")

    cfg = _make_config(tmp_path)  # services already includes "traefik"

    result = BootstrapStep().run(lambda _e: None, cfg)

    assert result.success is True
    payload = str(captured["extra_vars_payload"])
    assert '"agmind_edge_enabled": true' in payload


def test_bootstrap_extra_vars_payload_edge_disabled_without_traefik(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-traefik headless install must carry agmind_edge_enabled=false in the extra-vars
    payload, so ansible/install.yml's domain/CF-token asserts skip instead of dying on an
    intentionally empty domain/token (install_cmd.py already permits this for --no-tui)."""
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "install.yml").write_text("---\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        extra_vars = cmd[cmd.index("--extra-vars") + 1]
        if isinstance(extra_vars, str) and extra_vars.startswith("@"):
            captured["extra_vars_payload"] = Path(extra_vars[1:]).read_text(encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(steps, "resolve_ansible_command", lambda name: f"/venv/bin/{name}")

    cfg = _make_config(tmp_path)
    cfg.services = ["llama-llm"]  # no traefik
    cfg.domain = ""
    cfg.cf_api_token = ""

    result = BootstrapStep().run(lambda _e: None, cfg)

    assert result.success is True
    payload = str(captured["extra_vars_payload"])
    assert '"agmind_edge_enabled": false' in payload


def test_bootstrap_passes_sudo_password_via_extra_vars_not_become_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sudo password must reach Ansible as ``ansible_become_password`` inside the
    extra-vars file, never via ``--become-password-file`` and never via a pipe FD.

    Ansible applies ``type=unfrack_path()`` (i.e. ``os.path.realpath``) to
    ``--become-password-file`` at argparse time, and ALSO canonicalizes the
    ``--extra-vars @<file>`` path. realpath cannot resolve a ``/dev/fd/N`` pipe —
    it mangles it to ``/proc/<pid>/fd/pipe:[inode]`` (nonexistent) — so BOTH a piped
    become-password-file and a piped extra-vars file fail. The secret therefore rides
    a real 0600 temp file (on tmpfs) passed as ``--extra-vars @<path>``.
    """
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    (ansible_dir / "install.yml").write_text("---\n", encoding="utf-8")
    sudo_password = "sudo-secret-password"
    captured: dict[str, object] = {}

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        extra_vars = cmd[cmd.index("--extra-vars") + 1]
        captured["extra_vars_arg"] = extra_vars
        if isinstance(extra_vars, str) and extra_vars.startswith("@"):
            path = extra_vars[1:]
            captured["extra_vars_path"] = path
            # the file must really exist while ansible runs, with 0600 perms
            st = os.stat(path)
            captured["extra_vars_mode"] = stat.S_IMODE(st.st_mode)
            captured["extra_vars_payload"] = Path(path).read_text(encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(steps, "DEFAULT_REPO_ROOT", tmp_path)
    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(steps, "resolve_ansible_command", lambda name: f"/venv/bin/{name}")

    cfg = _make_config(tmp_path)
    cfg.sudo_password = sudo_password

    result = BootstrapStep().run(lambda _e: None, cfg)

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    argv = "\0".join(str(part) for part in cmd)
    kwargs = str(captured["kwargs"])
    assert result.success is True
    # the realpath-mangled channels must be gone entirely
    assert "--become-password-file" not in cmd
    assert "--become-pass-file" not in cmd
    assert "/dev/fd/" not in argv  # no pipe FD passed as any Ansible file arg
    # sudo password never appears in argv or Popen kwargs
    assert sudo_password not in argv
    assert sudo_password not in kwargs
    # it travels through the extra-vars file as ansible_become_password
    payload = str(captured["extra_vars_payload"])
    assert "ansible_become_password" in payload
    assert sudo_password in payload
    # secret file is 0600 while live and removed once the step finishes
    assert captured["extra_vars_mode"] == 0o600
    assert not Path(captured["extra_vars_path"]).exists()


def test_bootstrap_installs_ansible_collections_before_playbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        calls.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        steps,
        "resolve_ansible_command",
        lambda name: f"/venv/bin/{name}",
    )

    cfg = _make_config(tmp_path)
    result = BootstrapStep().run(lambda _e: None, cfg)

    assert result.success is True
    assert calls[0][0][:4] == ["/venv/bin/ansible-galaxy", "collection", "install", "-r"]
    assert calls[0][0][-2:] == ["-p", str(steps.DEFAULT_REPO_ROOT / "ansible" / ".galaxy")]
    assert "--offline" not in calls[0][0]  # online install: no --offline
    assert calls[1][0][0] == "/venv/bin/ansible-playbook"
    assert calls[0][1]["cwd"] == str(steps.DEFAULT_REPO_ROOT / "ansible")
    assert calls[1][1]["cwd"] == str(steps.DEFAULT_REPO_ROOT / "ansible")


def test_bootstrap_galaxy_install_is_offline_under_air_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review HIGH galaxy-collection-no-offline: under AGMIND_OFFLINE the galaxy install must
    pass --offline so it uses ONLY the pre-staged ansible/.galaxy collections and never hits
    galaxy.ansible.com (BootstrapStep runs before the offline-aware DeployStep)."""
    from agmind.install import steps
    from agmind.install.steps import BootstrapStep

    calls: list[list[str]] = []

    class FakeProc:
        stdout = _FakeStdout(["ok\n"])

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: object) -> FakeProc:
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(steps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(steps, "resolve_ansible_command", lambda name: f"/venv/bin/{name}")
    monkeypatch.setattr(steps, "_offline_install_enabled", lambda: True)

    assert BootstrapStep().run(lambda _e: None, _make_config(tmp_path)).success is True
    assert "--offline" in calls[0], "air-gap galaxy install must pass --offline"


def test_envwrite_fails_fast_when_proxmox_config_absent(tmp_path: Path) -> None:
    """Review MEDIUM proxmox-pve-config-not-staged: selecting proxmox-exporter without a staged
    pve.yml must fail fast with guidance, NOT ship a :ro single-file mount that Docker turns
    into a directory → crash-loop."""
    from agmind.install.orchestrator import InstallConfig
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["proxmox-exporter"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    cfg.install_dir.mkdir(parents=True)
    result = EnvWriteStep().run(lambda _e: None, cfg)
    assert not result.success
    assert "proxmox" in result.message.lower() and "pve.yml" in result.message


def test_envwrite_proceeds_when_proxmox_config_staged(tmp_path: Path) -> None:
    from agmind.install.orchestrator import InstallConfig
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["proxmox-exporter"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    cfg.install_dir.mkdir(parents=True)
    pve = cfg.config_dir / "proxmox-exporter" / "pve.yml"
    pve.parent.mkdir(parents=True)
    pve.write_text("default:\n  user: u@pve\n", encoding="utf-8")
    assert EnvWriteStep().run(lambda _e: None, cfg).success


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def __iter__(self) -> object:
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = None
        self.stdout = _FakeStdout(["line-1\n", "line-2\n"])
        self._alive = True
        self.killed = False
        self.waited = 0

    def poll(self) -> int | None:
        return None if self._alive else 0

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self) -> int:
        self.waited += 1
        self._alive = False
        return 0


def test_stream_subprocess_reaps_process_when_callback_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import steps

    fake = _FakeProc()
    monkeypatch.setattr(steps.subprocess, "Popen", lambda *a, **k: fake)

    def boom(_event: object) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        steps._stream_subprocess(["echo", "hi"], boom, "step")

    assert fake.killed is True
    assert fake.waited >= 1
    assert fake.stdout.closed is True


# ---------- cancellation (freeze fix) ----------


def test_orchestrator_assigns_cancel_event_to_steps(tmp_path: Path) -> None:
    """Orchestrator must hand its cancel_event to each step so the step can pass it
    to _stream_subprocess and have the child killed on Cancel."""
    import threading

    ev = threading.Event()
    step = FakeStep(step_id="s1", label="s1")
    InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=[step],
        callback=lambda _e: None,
        cancel_event=ev,
    ).run()
    assert step.cancel_event is ev


def test_orchestrator_reports_cancellation_when_event_already_set(tmp_path: Path) -> None:
    """If the cancel_event is set, the orchestrator stops without running further steps
    and reports a cancellation (not a generic failure)."""
    import threading

    ev = threading.Event()
    ev.set()
    ran: list[str] = []

    @dataclass
    class _RecordStep(FakeStep):
        def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
            ran.append(self.step_id)
            return super().run(callback, config)

    result = InstallOrchestrator(
        config=_make_config(tmp_path),
        steps=[_RecordStep(step_id="s1", label="s1")],
        callback=lambda _e: None,
        cancel_event=ev,
    ).run()

    assert result.success is False
    assert "cancel" in result.message.lower()
    assert ran == []  # never started the step


def test_stream_subprocess_killed_promptly_by_cancel_event() -> None:
    """A pre-set cancel_event must terminate a long-running child fast, instead of
    blocking the worker thread (and thus the TUI/VS Code) until it finishes."""
    import threading
    import time as _time

    from agmind.install import steps

    ev = threading.Event()
    ev.set()  # already cancelled before the child starts
    t0 = _time.monotonic()
    rc, _lines = steps._stream_subprocess(
        ["sleep", "30"], lambda _e: None, "cancel-test", cancel_event=ev
    )
    elapsed = _time.monotonic() - t0
    assert elapsed < 5.0, f"cancel did not kill the child promptly (took {elapsed:.1f}s)"
    assert rc != 0
