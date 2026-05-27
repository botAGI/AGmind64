"""Phase N: install orchestrator — sequence of steps + progress callback.

Design rules:
- Каждый Step реализует одну операцию (doctor / bootstrap / pull / deploy).
- Step.run() блокирующий, бежит в worker thread из TUI.
- ProgressCallback вызывается из worker thread; TUI обязан использовать
  call_from_thread() чтобы обновить widgets.
- Ошибка в step → orchestrator останавливается, emit'ит ProgressEvent(error).
  Уже выполненные shaги остаются как success — пользователь видит на каком
  именно step упало.
- secrets (sudo password, CF token) живут в memory только время одного
  step; orchestrator затирает их после step_done.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path

from agmind.core.logging import logger

log = logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/agmind")
DEFAULT_MODELS_DIR = Path("/var/lib/agmind/models")
DEFAULT_CONFIG_DIR = Path("/etc/agmind")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class ProgressKind(str, Enum):
    """Тип события ProgressCallback."""

    STEP_START = "step_start"
    STEP_DONE = "step_done"
    STEP_ERROR = "step_error"
    LOG = "log"  # raw stdout/stderr line
    PROGRESS = "progress"  # percent update for current step (0-100)


@dataclass(frozen=True)
class ProgressEvent:
    """One event emitted by a Step to the TUI/CLI listener."""

    step_id: str
    kind: ProgressKind
    text: str = ""
    progress_pct: int | None = None  # 0..100 if kind == PROGRESS


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True)
class InstallStepResult:
    """Outcome of one Step.run()."""

    step_id: str
    success: bool
    message: str = ""
    elapsed: timedelta = timedelta(0)


@dataclass(frozen=True)
class InstallResult:
    """Overall install outcome."""

    success: bool
    steps: tuple[InstallStepResult, ...]
    message: str = ""

    @property
    def failed_step(self) -> InstallStepResult | None:
        for s in self.steps:
            if not s.success:
                return s
        return None


@dataclass
class InstallConfig:
    """Все данные нужные orchestrator'у. Собираются в TUI wizard."""

    domain: str
    cf_api_token: str  # secret — будет очищен после bootstrap step
    services: list[str]
    backend: str = "auto"
    # LLM model (legacy field names preserved для backward compat).
    model_repo: str | None = None  # HF repo id, e.g. "0xSero/Qwen3.6-35B-A3B-GGUF-Strix"
    model_file: str | None = None  # e.g. "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    install_dir: Path = DEFAULT_INSTALL_DIR
    models_dir: Path = DEFAULT_MODELS_DIR
    config_dir: Path = DEFAULT_CONFIG_DIR
    sudo_password: str | None = None  # secret — очищается после bootstrap
    # Phase N.G: LLM inference settings passed to llama-llm via env vars
    # (compose template reads AGMIND_MODEL_FILE / AGMIND_LLM_CTX_SIZE / AGMIND_LLM_KV_CACHE).
    ctx_size: int = 16384
    kv_cache_type: str = "q8_0"
    threads: int = -1
    parallel_slots: int = 1
    # Phase M5.1: separate embed/rerank model + per-service settings.
    embed_repo: str | None = None
    embed_file: str | None = None
    embed_ctx_size: int = 8192
    embed_kv_cache: str = "f16"
    embed_parallel: int = 4
    rerank_repo: str | None = None
    rerank_file: str | None = None
    rerank_ctx_size: int = 2048

    def redact(self) -> dict[str, object]:
        """Safe dict for logging — secrets replaced with ***."""
        return {
            "domain": self.domain,
            "cf_api_token": f"*** ({len(self.cf_api_token)} chars)",
            "services": self.services,
            "backend": self.backend,
            "model_repo": self.model_repo,
            "model_file": self.model_file,
            "embed_repo": self.embed_repo,
            "embed_file": self.embed_file,
            "rerank_repo": self.rerank_repo,
            "rerank_file": self.rerank_file,
            "install_dir": str(self.install_dir),
            "models_dir": str(self.models_dir),
            "config_dir": str(self.config_dir),
            "ctx_size": self.ctx_size,
            "kv_cache_type": self.kv_cache_type,
            "threads": self.threads,
            "parallel_slots": self.parallel_slots,
            "embed_ctx_size": self.embed_ctx_size,
            "embed_kv_cache": self.embed_kv_cache,
            "embed_parallel": self.embed_parallel,
            "rerank_ctx_size": self.rerank_ctx_size,
            "sudo_password": "*** (set)" if self.sudo_password else "(unset)",
        }

    def wipe_secrets(self) -> None:
        """Best-effort zero-out для cf_token + sudo_password в памяти."""
        self.cf_api_token = ""
        self.sudo_password = None


class InstallStep(ABC):
    """Abstract install step."""

    step_id: str = ""
    label: str = ""

    @abstractmethod
    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        """Execute step. Should emit ProgressEvent(step_start) first, return result."""

    def _emit(
        self,
        callback: ProgressCallback,
        kind: ProgressKind,
        text: str = "",
        progress_pct: int | None = None,
    ) -> None:
        try:
            callback(
                ProgressEvent(
                    step_id=self.step_id,
                    kind=kind,
                    text=text,
                    progress_pct=progress_pct,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("progress callback raised: %s (ignored)", exc)


@dataclass
class InstallOrchestrator:
    """Run a sequence of InstallSteps under one progress callback."""

    config: InstallConfig
    steps: list[InstallStep]
    callback: ProgressCallback = field(default=lambda _ev: None)

    def run(self) -> InstallResult:
        log.info("install starting: %s", self.config.redact())
        results: list[InstallStepResult] = []
        for step in self.steps:
            start = time.monotonic()
            self._emit_step_start(step)
            try:
                result = step.run(self._emit_progress, self.config)
            except Exception as exc:  # noqa: BLE001
                elapsed = timedelta(seconds=time.monotonic() - start)
                log.error("step %s crashed: %s", step.step_id, self._redact_text(str(exc)))
                result = InstallStepResult(
                    step_id=step.step_id,
                    success=False,
                    message=f"unhandled exception: {self._redact_text(str(exc))}",
                    elapsed=elapsed,
                )
            result = self._redact_result(result)
            results.append(result)
            if not result.success:
                self._emit_step_error(step, result.message)
                self.config.wipe_secrets()
                return InstallResult(
                    success=False,
                    steps=tuple(results),
                    message=f"failed at step '{step.step_id}': {result.message}",
                )
            self._emit_step_done(step, result)
        self.config.wipe_secrets()
        return InstallResult(
            success=True,
            steps=tuple(results),
            message=f"install complete ({len(results)} steps)",
        )

    def _redact_text(self, text: str) -> str:
        redacted = text
        for secret in (self.config.cf_api_token, self.config.sudo_password):
            if secret:
                redacted = redacted.replace(secret, "***")
        return redacted

    def _redact_event(self, event: ProgressEvent) -> ProgressEvent:
        text = self._redact_text(event.text)
        if text == event.text:
            return event
        return ProgressEvent(
            step_id=event.step_id,
            kind=event.kind,
            text=text,
            progress_pct=event.progress_pct,
        )

    def _redact_result(self, result: InstallStepResult) -> InstallStepResult:
        message = self._redact_text(result.message)
        if message == result.message:
            return result
        return InstallStepResult(
            step_id=result.step_id,
            success=result.success,
            message=message,
            elapsed=result.elapsed,
        )

    def _emit_progress(self, event: ProgressEvent) -> None:
        try:
            self.callback(self._redact_event(event))
        except Exception as exc:  # noqa: BLE001
            log.debug("progress callback raised: %s", exc)

    def _emit_step_start(self, step: InstallStep) -> None:
        self._emit_progress(
            ProgressEvent(
                step_id=step.step_id,
                kind=ProgressKind.STEP_START,
                text=step.label,
            )
        )

    def _emit_step_done(self, step: InstallStep, result: InstallStepResult) -> None:
        self._emit_progress(
            ProgressEvent(
                step_id=step.step_id,
                kind=ProgressKind.STEP_DONE,
                text=result.message or step.label,
            )
        )

    def _emit_step_error(self, step: InstallStep, message: str) -> None:
        self._emit_progress(
            ProgressEvent(
                step_id=step.step_id,
                kind=ProgressKind.STEP_ERROR,
                text=message,
            )
        )


# Re-export для удобства concrete step modules.
__all__ = [
    "DEFAULT_INSTALL_DIR",
    "DEFAULT_MODELS_DIR",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_REPO_ROOT",
    "InstallConfig",
    "InstallOrchestrator",
    "InstallResult",
    "InstallStep",
    "InstallStepResult",
    "ProgressCallback",
    "ProgressEvent",
    "ProgressKind",
]
