"""Lane C — full install pipeline, run TWICE (orchestrator-level re-run safety).

CLEAN-INSTALL-HARDENING §3 lane C. The exact operator scenario that bit serially: an install
that fails at a LATER step (model download / deploy) leaves earlier-step state on disk, and the
operator RE-RUNS — at which point a non-idempotent earlier step (EnvWrite / Compose / Credentials)
must NOT leave unrecoverable state (the RANK-1 ``OSError`` / ``Could not install`` class).

This lane drives ``InstallOrchestrator.run(default_steps()-shaped pipeline, config)`` against a
fresh ``tmp_path`` prefix with the HEAVY / host-coupled steps mocked rc=0 (Doctor, Cloudflare,
Bootstrap-subprocess, ModelDownload/HF, Deploy/docker) but EnvWrite / ComposeConfig / Credentials
REAL, then runs the WHOLE pipeline AGAIN with the same config + prefix. Asserts:

- both whole-pipeline runs succeed,
- ``.env`` + ``credentials.txt`` exist after each run,
- the 2nd run's events contain no ``OSError`` / ``Could not install`` / ``Rolling back uninstall``
  (proves no step leaves unrecoverable state between runs).

Per §3: because BootstrapStep's subprocess is mocked here, this lane does NOT catch the *real*
ansible pip race (that's lane A's clean-container job). Lane C catches Python-side re-run state
bugs. HERMETIC: every path under ``tmp_path``; ``sudo_password=None`` → local write path; the
docker ``compose config`` subprocess is mocked, so no daemon is touched.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from agmind.install.orchestrator import (
    InstallConfig,
    InstallOrchestrator,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressEvent,
)
from agmind.install.steps import ComposeConfigStep, CredentialsStep, EnvWriteStep

pytestmark = pytest.mark.backend_any

_FORBIDDEN_RERUN_SUBSTRINGS = (
    "OSError",
    "Could not install",
    "Rolling back uninstall",
    "Errno 2",
)


class _FakeStep(InstallStep):
    """A heavy step replaced by an rc=0 no-op (Doctor / Cloudflare / Bootstrap / Model / Deploy).

    Mirrors the success-result shape of the real steps so the orchestrator advances exactly
    as in production, without spawning ansible / hitting HF / talking to docker.
    """

    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        self.label = step_id

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"{self.step_id} ok (mocked)",
            elapsed=timedelta(0),
        )


def _make_config(tmp_path: Path, services: list[str]) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="cf-token-" + "X" * 40,
        services=services,
        install_dir=tmp_path / "opt" / "agmind",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
        model_file="model-Q4_K_M.gguf",
        sudo_password=None,
    )


def _pipeline() -> list[InstallStep]:
    """default_steps() shape: heavy steps faked rc=0, EnvWrite/Compose/Credentials REAL."""
    return [
        _FakeStep("doctor"),
        _FakeStep("cloudflare_token"),
        _FakeStep("bootstrap"),
        EnvWriteStep(),
        ComposeConfigStep(),
        _FakeStep("model_pull"),
        _FakeStep("deploy"),
        CredentialsStep(),
    ]


def _run_pipeline(cfg: InstallConfig) -> tuple[bool, list[ProgressEvent]]:
    events: list[ProgressEvent] = []
    result = InstallOrchestrator(
        config=cfg,
        steps=_pipeline(),
        callback=events.append,
    ).run()
    return result.success, events


@pytest.mark.parametrize(
    "services",
    [
        pytest.param(["prometheus", "grafana", "loki"], id="observability"),
        pytest.param(["traefik", "llama-llm"], id="traefik-llm"),
    ],
)
def test_full_pipeline_double_run_is_rerun_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    services: list[str],
) -> None:
    """Run the whole install pipeline TWICE against one prefix; the re-run is clean."""
    import agmind.install.steps as steps

    # Mock ONLY the docker subprocess (ComposeConfigStep's `docker compose config`) to rc=0;
    # render / env-file staging stay real. Bootstrap/model/deploy are faked at the step level.
    monkeypatch.setattr(steps, "_stream_subprocess", lambda *a, **k: (0, []))

    cfg = _make_config(tmp_path, services)
    env_path = cfg.install_dir / ".env"
    creds_path = cfg.install_dir / "credentials.txt"

    # ---- run 1 ----
    ok1, _events1 = _run_pipeline(cfg)
    assert ok1, "first full-pipeline run failed"
    assert env_path.exists(), ".env missing after first run"
    assert creds_path.exists(), "credentials.txt missing after first run"

    # The orchestrator wipes cf_api_token after each run() — re-supply it as a real
    # re-invocation would (the operator re-enters the wizard).
    cfg.cf_api_token = "cf-token-" + "X" * 40

    # ---- run 2 (operator retry over existing state) ----
    ok2, events2 = _run_pipeline(cfg)
    assert ok2, "second full-pipeline run failed (re-run not idempotent)"
    assert env_path.exists(), ".env missing after second run"
    assert creds_path.exists(), "credentials.txt missing after second run"

    # No step left unrecoverable state surfacing as the reported pip-corruption class.
    offenders = [
        e.text for e in events2 if any(bad in e.text for bad in _FORBIDDEN_RERUN_SUBSTRINGS)
    ]
    assert offenders == [], f"re-run surfaced unrecoverable-state errors: {offenders}"


def test_full_pipeline_succeeds_after_simulated_late_step_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First run fails at deploy (after EnvWrite/Compose ran real); retry then succeeds clean.

    This is the precise serial-bug scenario: a later step fails, leaving earlier-step output on
    disk, and the operator re-runs. The retry must complete without the re-run touching
    unrecoverable state — proving the real EnvWrite/Compose are safe to run over their own
    prior output.
    """
    import agmind.install.steps as steps

    monkeypatch.setattr(steps, "_stream_subprocess", lambda *a, **k: (0, []))

    cfg = _make_config(tmp_path, ["prometheus", "grafana", "loki"])
    env_path = cfg.install_dir / ".env"

    class _FailingDeploy(InstallStep):
        step_id = "deploy"
        label = "deploy"

        def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="deploy failed: transient (e.g. HF/Xet glitch)",
                elapsed=timedelta(0),
            )

    failing_pipeline: list[InstallStep] = [
        _FakeStep("doctor"),
        _FakeStep("cloudflare_token"),
        _FakeStep("bootstrap"),
        EnvWriteStep(),
        ComposeConfigStep(),
        _FakeStep("model_pull"),
        _FailingDeploy(),
        CredentialsStep(),  # never reached on run 1
    ]

    first = InstallOrchestrator(config=cfg, steps=failing_pipeline, callback=lambda _e: None).run()
    assert first.success is False, "first run was supposed to fail at deploy"
    assert env_path.exists(), "EnvWrite output should be on disk after the partial first run"

    cfg.cf_api_token = "cf-token-" + "X" * 40

    # Retry with a clean pipeline (deploy now succeeds) — must complete over the dirty prefix.
    ok2, events2 = _run_pipeline(cfg)
    assert ok2, "retry after a late-step failure did not succeed"
    assert (cfg.install_dir / "credentials.txt").exists(), "retry never reached credentials.txt"
    offenders = [
        e.text for e in events2 if any(bad in e.text for bad in _FORBIDDEN_RERUN_SUBSTRINGS)
    ]
    assert offenders == [], f"retry surfaced unrecoverable-state errors: {offenders}"
