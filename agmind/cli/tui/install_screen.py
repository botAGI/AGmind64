"""Phase N: InstallProgressScreen — live progress для `agmind install`.

Шаги показываются как list с глифами (⏳ pending / 🔄 running / ✅ done /
❌ error), под ними live log от всех subprocess'ов. Optional progress bar
для текущего шага (model download).

Запускается из AgmindSetupApp.action_submit() в install mode (Phase N).
Изолирован от Phase J.1.5 DeployProgressScreen — DeployScreen покрывает
только deploy step; InstallScreen оркестрирует всю N.A-D последовательность.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ProgressBar, RichLog, Static

from agmind.install.orchestrator import (
    InstallConfig,
    InstallOrchestrator,
    InstallResult,
    InstallStep,
    ProgressEvent,
    ProgressKind,
)


class InstallProgressScreen(Screen[InstallResult]):
    """Full-screen modal с live install progress."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=True),
        Binding("escape", "dismiss_if_done", "Close (after finish)", show=True),
    ]

    DEFAULT_CSS = """
    InstallProgressScreen {
        background: $surface;
    }

    #install-title {
        color: $accent;
        text-style: bold;
        padding: 0 2;
        text-align: center;
        height: 3;
    }

    #steps-panel {
        border: tall $primary 40%;
        margin: 0 1;
        padding: 0 1;
        height: auto;
    }

    #step-progress-bar {
        margin: 0 1;
        height: 1;
    }

    #log-panel {
        border: tall $accent 40%;
        margin: 0 1;
        padding: 0 1;
        height: 1fr;
    }

    RichLog {
        height: 100%;
    }

    #status-line {
        margin: 0 1;
        padding: 0 1;
        text-style: bold;
    }

    #status-line.success { color: $success; }
    #status-line.error { color: $error; }
    #status-line.running { color: $warning; }

    #install-buttons {
        align: center middle;
        height: 3;
    }
    """

    def __init__(
        self,
        config: InstallConfig,
        steps: list[InstallStep],
    ) -> None:
        super().__init__()
        self.config = config
        self.steps = steps
        # Signalled on cancel/close/unmount; the orchestrator + steps watch it and a
        # daemon watchdog kills the running subprocess so the worker unblocks (otherwise
        # Textual blocks app exit waiting on the thread-worker -> TUI/VS Code freeze).
        self.cancel_event = threading.Event()
        self.result: InstallResult | None = None
        self._step_states: dict[str, str] = {step.step_id: "pending" for step in steps}
        self._step_start_ts: dict[str, datetime] = {}
        self._step_elapsed: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f"AGmind · Install\n"
            f"domain={self.config.domain}  ·  services={len(self.config.services)}",
            id="install-title",
        )

        with Vertical(id="steps-panel"):
            for step in self.steps:
                yield Static(self._step_line(step), id=f"install-step-{step.step_id}")

        # Phase M3.S.1: show_eta=True — ETA полезно для long-running steps
        # (model download / docker pull).
        yield ProgressBar(total=100, id="step-progress-bar", show_eta=True)

        with Vertical(id="log-panel"):
            yield Label("Live log:", classes="section")
            yield RichLog(id="install-log", wrap=True, highlight=True, markup=True)

        yield Static("Starting…", id="status-line", classes="running")

        with Horizontal(id="install-buttons"):
            yield Button("Close", id="install-close-btn", variant="default", disabled=True)
            yield Button("Cancel", id="install-cancel-btn", variant="error")

        yield Footer()

    def _step_line(self, step: InstallStep) -> str:
        state = self._step_states.get(step.step_id, "pending")
        glyph = {
            "pending": "[dim]⏳[/dim]",
            "running": "[yellow]🔄[/yellow]",
            "success": "[green]✓[/green]",
            "error": "[red]✗[/red]",
        }.get(state, "·")
        suffix = ""
        if step.step_id in self._step_elapsed:
            suffix = f"  [dim]{self._step_elapsed[step.step_id]}[/dim]"
        return f"  {glyph}  {step.label}{suffix}"

    def _update_step(self, step_id: str, state: str) -> None:
        if step_id not in self._step_states:
            return
        self._step_states[step_id] = state
        if state == "running":
            self._step_start_ts[step_id] = datetime.now()
        elif state in ("success", "error"):
            start = self._step_start_ts.get(step_id)
            if start is not None:
                delta = datetime.now() - start
                self._step_elapsed[step_id] = f"{delta.total_seconds():.0f}s"
        for s in self.steps:
            if s.step_id == step_id:
                widget = self.query_one(f"#install-step-{step_id}", Static)
                widget.update(self._step_line(s))
                break

    def _final_operator_hint(self) -> str:
        return (
            f"Runtime credentials: {self.config.install_dir / '.env'} (chmod 600)\n"
            "Values are not printed in the installer summary."
        )

    def on_mount(self) -> None:
        self._run_install()

    @work(exclusive=True, thread=True)
    def _run_install(self) -> None:
        """Run orchestrator в worker thread, emit events через call_from_thread."""

        def progress_cb(event: ProgressEvent) -> None:
            # Все ниже-уровневые widget updates → main thread.
            self.app.call_from_thread(self._handle_event, event)

        orchestrator = InstallOrchestrator(
            config=self.config,
            steps=self.steps,
            callback=progress_cb,
            cancel_event=self.cancel_event,
        )
        try:
            self.result = orchestrator.run()
        except Exception as exc:  # noqa: BLE001
            from agmind.install.orchestrator import InstallResult as _IR

            self.result = _IR(
                success=False,
                steps=(),
                message=f"unhandled orchestrator error: {exc}",
            )
        self.app.call_from_thread(self._finalize)

    def _handle_event(self, event: ProgressEvent) -> None:
        if not self.is_mounted:
            # Screen was dismissed/cancelled while the worker was still draining a
            # subprocess; its widgets are gone — drop the late update instead of raising.
            return
        log_widget = self.query_one("#install-log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        if event.kind is ProgressKind.STEP_START:
            self._update_step(event.step_id, "running")
            log_widget.write(f"[dim]{ts}[/dim] [yellow]▶ {event.text}[/yellow]")
            # Reset progress bar для нового шага
            self.query_one("#step-progress-bar", ProgressBar).update(progress=0)
        elif event.kind is ProgressKind.STEP_DONE:
            self._update_step(event.step_id, "success")
            log_widget.write(f"[dim]{ts}[/dim] [green]✓ {event.step_id}: {event.text}[/green]")
            self.query_one("#step-progress-bar", ProgressBar).update(progress=100)
        elif event.kind is ProgressKind.STEP_ERROR:
            self._update_step(event.step_id, "error")
            log_widget.write(f"[dim]{ts}[/dim] [red]✗ {event.step_id}: {event.text}[/red]")
        elif event.kind is ProgressKind.PROGRESS:
            if event.progress_pct is not None:
                self.query_one("#step-progress-bar", ProgressBar).update(
                    progress=event.progress_pct
                )
        else:  # LOG
            log_widget.write(f"[dim]{ts}[/dim] {event.text}")

    def _finalize(self) -> None:
        if not self.is_mounted:
            return
        result = self.result
        status = self.query_one("#status-line", Static)
        log_widget = self.query_one("#install-log", RichLog)
        close_btn = self.query_one("#install-close-btn", Button)
        cancel_btn = self.query_one("#install-cancel-btn", Button)

        if result is None:
            status.update("⚠️  Install finished without result")
            status.set_classes("status-line error")
        elif result.success:
            status.update(f"✅ {result.message}")
            status.set_classes("status-line success")
            log_widget.write(f"[green]{self._final_operator_hint()}[/green]")
        else:
            status.update(f"❌ {result.message}")
            status.set_classes("status-line error")
            log_widget.write(f"[yellow]{self._final_operator_hint()}[/yellow]")

        close_btn.disabled = False
        cancel_btn.disabled = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install-close-btn":
            self.dismiss(self.result)
        elif event.button.id == "install-cancel-btn":
            self.action_cancel()

    def action_cancel(self) -> None:
        from agmind.install.orchestrator import InstallResult as _IR

        # Signal the worker FIRST so the running subprocess is killed and the thread
        # unblocks — otherwise dismissing here leaves the worker stuck and the app hangs
        # on exit waiting to join it.
        self.cancel_event.set()
        if self.result is None:
            self.result = _IR(
                success=False,
                steps=(),
                message="cancelled by user",
            )
        self.dismiss(self.result)

    def on_unmount(self) -> None:
        # Catch-all: whatever tears the screen down (Cancel/Close/Escape/ctrl+c/quit),
        # make sure a still-running subprocess is killed so app exit does not block.
        self.cancel_event.set()

    def action_dismiss_if_done(self) -> None:
        if self.result is not None:
            self.dismiss(self.result)


__all__ = ["InstallProgressScreen"]
