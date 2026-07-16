"""Phase J.1.5: DeployProgressScreen — live deployment view внутри TUI wizard.

User flow:
    agmind setup → wizard form → Apply → DeployProgressScreen
        ├─ Live RichLog с output каждого step (render/snapshot/up/healthcheck)
        ├─ Service status table (⏳ pending → 🔄 starting → ✅ healthy / ❌ failed)
        ├─ Cancel button (graceful)
        └─ On done → SuccessSummary или FailureSummary + rollback offer

Архитектура decoupled: deploy() runner emits progress events через callback;
TUI screen subscribes и обновляет widgets. Same runner используется
для CLI `agmind deploy --apply` без TUI (callback=None).
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, RichLog, Static

from agmind.deploy.runner import DeployResult, ProgressCallback, deploy


class DeployProgressScreen(Screen[DeployResult]):
    """Full-screen modal с live deploy progress."""

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=True),
        Binding("escape", "dismiss_if_done", "Close (после finish)", show=True),
    ]

    DEFAULT_CSS = """
    DeployProgressScreen {
        background: $surface;
    }

    #deploy-title {
        color: $accent;
        text-style: bold;
        padding: 1 2;
        text-align: center;
    }

    #steps-panel {
        border: solid $primary;
        margin: 1 2;
        padding: 1 2;
        height: auto;
    }

    #log-panel {
        border: solid $accent;
        margin: 1 2;
        padding: 0 1;
        height: 1fr;
    }

    RichLog {
        height: 100%;
    }

    #status-line {
        margin: 1 2;
        padding: 0 1;
        text-style: bold;
    }

    #status-line.success { color: $success; }
    #status-line.error { color: $error; }
    #status-line.running { color: $warning; }

    #button-row {
        align: center middle;
        height: 3;
        margin: 1 0;
    }
    """

    # Шаги deploy в визуальном порядке
    STEPS = [
        ("render", "Render compose"),
        ("diff", "Compute diff"),
        ("snapshot", "Snapshot current state"),
        ("pull", "Pull images"),
        ("compose_up", "Docker compose up"),
        ("wait_healthy", "Wait for healthy services"),
    ]

    def __init__(
        self,
        profiles: list[str],
        domain: str,
        install_dir: Path,
        healthcheck_timeout: int | None = None,
        services: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.profiles = profiles
        self.services = services
        self.domain = domain
        self.install_dir = install_dir
        self.healthcheck_timeout = healthcheck_timeout
        self.result: DeployResult | None = None
        self._cancelled = False
        # Watched by the deploy runner's healthcheck wait so Cancel breaks out of the
        # long (up to healthcheck_timeout) poll instead of hanging the worker thread.
        self.cancel_event = threading.Event()
        self._step_states: dict[str, str] = {step: "pending" for step, _ in self.STEPS}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f"Deploying AGmind\n"
            f"  domain: {self.domain}\n"
            f"  profiles: {', '.join(self.profiles)}\n"
            f"  services: {', '.join(self.services or []) or '-'}\n"
            f"  install_dir: {self.install_dir}",
            id="deploy-title",
        )

        with Vertical(id="steps-panel"):
            yield Label("Steps:", classes="section")
            for step_id, label in self.STEPS:
                yield Static(self._step_line(step_id, label), id=f"step-{step_id}")

        with Vertical(id="log-panel"):
            yield Label("Live output:", classes="section")
            yield RichLog(id="deploy-log", wrap=True, highlight=True, markup=True)

        yield Static("Starting deploy...", id="status-line", classes="running")

        with Horizontal(id="button-row"):
            yield Button("Close", id="close-btn", variant="default", disabled=True)
            yield Button("Cancel", id="cancel-btn", variant="error")

        yield Footer()

    def _step_line(self, step_id: str, label: str) -> str:
        state = self._step_states.get(step_id, "pending")
        icon = {
            "pending": "⏳",
            "running": "🔄",
            "success": "✅",
            "error": "❌",
        }.get(state, "⏳")
        return f"  {icon}  {label}"

    def _update_step(self, step_id: str, state: str) -> None:
        if step_id not in self._step_states:
            return
        self._step_states[step_id] = state
        for step, label in self.STEPS:
            if step == step_id:
                widget = self.query_one(f"#step-{step_id}", Static)
                widget.update(self._step_line(step_id, label))
                break

    def on_mount(self) -> None:
        self._run_deploy()

    @work(exclusive=True, thread=True)
    def _run_deploy(self) -> None:
        """Run deploy в worker thread чтобы не блокировать UI loop."""

        def progress_cb(step: str, msg: str) -> None:
            if not self.is_mounted:
                # Screen dismissed while the worker was still streaming pull/up lines —
                # query_one would raise on a torn-down screen; drop the late update.
                return
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_widget = self.query_one("#deploy-log", RichLog)
            color = {
                "render": "cyan",
                "diff": "blue",
                "snapshot": "yellow",
                "pull": "magenta",
                "compose_up": "magenta",
                "wait_healthy": "yellow",
                "success": "green",
                "error": "red",
                "rollback": "red",
            }.get(step, "white")
            # call_from_thread обязателен — мы из worker thread пишем в UI widget
            self.app.call_from_thread(
                log_widget.write,
                f"[dim]{timestamp}[/dim] [{color}][{step}][/{color}] {msg}",
            )

            # Map deploy steps к visual step states
            if step in self._step_states:
                self.app.call_from_thread(self._update_step, step, "running")
                # mark previous steps done
                done = False
                for prev_step, _ in self.STEPS:
                    if prev_step == step:
                        break
                    if not done:
                        self.app.call_from_thread(self._update_step, prev_step, "success")
            elif step == "success":
                # mark all running steps as success
                for s, _ in self.STEPS:
                    if self._step_states.get(s) in ("running", "pending"):
                        self.app.call_from_thread(self._update_step, s, "success")
            elif step in ("error", "rollback"):
                # mark current running step as error
                for s, _ in self.STEPS:
                    if self._step_states.get(s) == "running":
                        self.app.call_from_thread(self._update_step, s, "error")

        try:
            self.result = self._deploy(progress_cb)
        except Exception as exc:
            self.result = DeployResult(success=False, message=f"unhandled error: {exc}")

        # Finalize UI from worker thread
        self.app.call_from_thread(self._finalize)

    def _deploy(self, progress_cb: ProgressCallback) -> DeployResult:
        return deploy(
            profiles=self.profiles,
            services=self.services,
            install_dir=self.install_dir,
            domain=self.domain,
            apply=True,
            no_prompt=True,
            healthcheck_timeout=self.healthcheck_timeout,
            progress=progress_cb,
            cancel_event=self.cancel_event,
        )

    def _finalize(self) -> None:
        result = self.result
        status = self.query_one("#status-line", Static)
        close_btn = self.query_one("#close-btn", Button)
        cancel_btn = self.query_one("#cancel-btn", Button)

        if result is None:
            status.update("⚠️  Deploy finished without result")
            status.set_classes("status-line error")
        elif result.success:
            status.update(f"✅ {result.message}")
            status.set_classes("status-line success")
        else:
            extra = ""
            if result.rollback_performed:
                extra = " (rolled back to snapshot)"
            status.update(f"❌ {result.message}{extra}")
            status.set_classes("status-line error")

        close_btn.disabled = False
        cancel_btn.disabled = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(self.result)
        elif event.button.id == "cancel-btn":
            self.action_cancel()

    def action_cancel(self) -> None:
        # Signal the runner (its healthcheck wait polls cancel_event) so the worker
        # unblocks promptly, then dismiss.
        self._cancelled = True
        self.cancel_event.set()
        if self.result is None:
            self.result = DeployResult(success=False, message="cancelled by user")
        self.dismiss(self.result)

    def on_unmount(self) -> None:
        # Catch-all so any teardown path unblocks a running deploy worker.
        self.cancel_event.set()

    def action_dismiss_if_done(self) -> None:
        if self.result is not None:
            self.dismiss(self.result)


# Re-export для тестов / clear public API
__all__ = ["DeployProgressScreen"]
