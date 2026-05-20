"""Phase N: tests for agmind.install.orchestrator + step contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from agmind.install.orchestrator import (
    InstallConfig,
    InstallOrchestrator,
    InstallResult,
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

    def run(
        self, callback: ProgressCallback, config: InstallConfig
    ) -> InstallStepResult:
        for line in self.log_lines:
            callback(ProgressEvent(step_id=self.step_id, kind=ProgressKind.LOG, text=line))
        if self.raised is not None:
            raise self.raised
        if self.should_fail:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=self.fail_message, elapsed=timedelta(seconds=0.1),
            )
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message=f"{self.step_id} ok", elapsed=timedelta(seconds=0.1),
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
        config=_make_config(tmp_path), steps=steps, callback=events.append,
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
        config=_make_config(tmp_path), steps=steps, callback=events.append,
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
        config=_make_config(tmp_path), steps=steps, callback=events.append,
    ).run()
    assert result.success is False
    assert "blow up" in result.failed_step.message


def test_orchestrator_emits_step_logs(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    steps = [FakeStep(step_id="a", log_lines=["hello", "world"])]
    InstallOrchestrator(
        config=_make_config(tmp_path), steps=steps, callback=events.append,
    ).run()
    logs = [e.text for e in events if e.kind is ProgressKind.LOG]
    assert logs == ["hello", "world"]


def test_orchestrator_wipes_sudo_password_after_bootstrap(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert cfg.sudo_password == "sup3rs3cret"
    steps = [FakeStep(step_id="bootstrap", label="Bootstrap")]
    InstallOrchestrator(config=cfg, steps=steps, callback=lambda _e: None).run()
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
        config=_make_config(tmp_path), steps=steps, callback=bad_cb,
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
    assert ids == ["doctor", "bootstrap", "image_pull", "model_pull", "env_write", "deploy"]


def test_env_write_step_writes_file(tmp_path: object) -> None:
    """EnvWriteStep creates .env с правильными vars."""
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com", cf_api_token="X" * 40, services=["llama-llm"],
        install_dir=tmp_path / "opt",  # type: ignore[operator]
        model_repo="r", model_file="model.gguf",
        ctx_size=32768, kv_cache_type="q4_0",
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


def test_install_config_carries_ctx_kv(tmp_path: object) -> None:
    cfg = _make_config(tmp_path)  # type: ignore[arg-type]
    assert cfg.ctx_size == 16384
    assert cfg.kv_cache_type == "q8_0"
    payload = cfg.redact()
    assert payload["ctx_size"] == 16384
    assert payload["kv_cache_type"] == "q8_0"


def test_default_steps_all_have_label() -> None:
    from agmind.install.steps import default_steps

    for step in default_steps():
        assert step.label, f"step {step.step_id} missing label"


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
