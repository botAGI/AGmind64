"""Phase J.1.6: SummaryScreen — финальный экран после wizard/deploy.

Заменяет post-exit Rich Panel в typer command. Всё в одном TUI app:
    wizard → (deploy progress) → SummaryScreen → Quit → exit

Три mode'а:
    - next_steps: после Apply без auto-deploy (показывает manual commands)
    - deploy_success: deploy прошёл, all services healthy
    - deploy_failure: deploy упал (показывает error + rollback status)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from agmind.deploy.runner import DeployResult

SummaryMode = Literal["next_steps", "deploy_success", "deploy_failure"]


class SummaryScreen(Screen[None]):
    """Полноэкранный finals — всё в одном TUI."""

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("enter", "quit", "Close", show=True),
    ]

    DEFAULT_CSS = """
    SummaryScreen {
        background: $surface;
    }

    #summary-title {
        text-style: bold;
        padding: 1 2;
        text-align: center;
    }

    #summary-title.success { color: $success; }
    #summary-title.error { color: $error; }
    #summary-title.info { color: $accent; }

    #summary-body {
        margin: 1 2;
        padding: 1 2;
        height: auto;
    }

    #summary-body.success { border: solid $success; }
    #summary-body.error { border: solid $error; }
    #summary-body.info { border: solid $accent; }

    .config-line {
        padding: 0 1;
    }

    Label.section {
        text-style: bold underline;
        margin-top: 1;
    }

    #next-steps {
        margin: 1 2;
        padding: 1 2;
        border: solid $warning;
        height: auto;
    }

    #button-row {
        align: center middle;
        height: 3;
        margin: 1 0;
    }
    """

    def __init__(
        self,
        mode: SummaryMode,
        domain: str,
        profiles: list[str],
        backend: str,
        model_tier: str,
        state_path: Path,
        token_path: Path,
        install_dir: Path,
        services: list[str] | None = None,
        deploy_result: DeployResult | None = None,
    ) -> None:
        super().__init__()
        if isinstance(services, DeployResult) and deploy_result is None:
            deploy_result = services
            services = None
        self.mode = mode
        self.domain = domain
        self.profiles = profiles
        self.services = services or []
        self.backend = backend
        self.model_tier = model_tier
        self.state_path = state_path
        self.token_path = token_path
        self.install_dir = install_dir
        self.deploy_result = deploy_result

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)

        # Title varies по mode
        if self.mode == "deploy_success":
            title = "✓ Deployment Successful"
            title_class = "success"
            body_class = "success"
        elif self.mode == "deploy_failure":
            title = "✗ Deployment Failed"
            title_class = "error"
            body_class = "error"
        else:
            title = "✓ Wizard saved your config"
            title_class = "info"
            body_class = "info"

        yield Static(title, id="summary-title", classes=title_class)

        with VerticalScroll():
            # Config summary (common)
            with Vertical(id="summary-body", classes=body_class):
                yield Static(self._config_summary(), id="config-summary")

            # Next steps (varies)
            with Vertical(id="next-steps"):
                yield Static(self._next_steps_text(), id="next-steps-text")

            # Deploy result (если есть)
            if self.deploy_result is not None:
                yield Static(self._deploy_result_text(), id="deploy-result")

        with Horizontal(id="button-row"):
            yield Button("Close", id="close-btn", variant="success")

        yield Footer()

    def _config_summary(self) -> str:
        selection_line = (
            f"  Services:     {', '.join(self.services)}"
            if self.services
            else f"  Profiles:     {', '.join(self.profiles)}"
        )
        return (
            f"  Domain:       {self.domain}\n"
            f"{selection_line}\n"
            f"  Backend:      {self.backend}\n"
            f"  Model tier:   {self.model_tier}\n"
            f"  Install dir:  {self.install_dir}\n"
            f"  Runtime env:  {self.install_dir / '.env'} (chmod 600)\n"
            f"  State:        {self.state_path}\n"
            f"  Token:        {self.token_path} (chmod 600)\n"
            "  Credentials are not printed in summary; read the env file on host."
        )

    def _endpoint_lines(self) -> list[str]:
        """Real per-service endpoint lines from descriptors + the rendered .env (best-effort)."""
        try:
            from agmind.core.env import parse_env_file
            from agmind.services.access import build_access_report, render_endpoint_lines
            from agmind.services.renderer import load_descriptors

            selected = set(self.services)
            descriptors = {n: d for n, d in load_descriptors().items() if n in selected}
            env_path = self.install_dir / ".env"
            env = parse_env_file(env_path) if env_path.exists() else {}
            return render_endpoint_lines(build_access_report(descriptors, env, domain=self.domain))
        except Exception:  # noqa: BLE001 — summary must never crash on a presentation helper
            return []

    def _next_steps_text(self) -> str:
        profiles_csv = ",".join(self.profiles)
        selection_flags = (
            " \\\n".join(f"        --service {service}" for service in self.services)
            if self.services
            else f"        --profile {profiles_csv}"
        )
        if self.mode == "deploy_success":
            endpoint_lines = self._endpoint_lines()
            endpoints_block = (
                "\n".join(endpoint_lines)
                if endpoint_lines
                else f"      https://<service>.{self.domain}"
            )
            creds_path = self.install_dir / "credentials.txt"
            return (
                "━━━━━━ What to do next ━━━━━━\n"
                "\n"
                "  • Your services (URL — login hint):\n"
                f"{endpoints_block}\n"
                "\n"
                f"  • Credentials (URLs + passwords): {creds_path} (chmod 600)\n"
                "\n"
                "  • Re-view access any time:\n"
                "      agmind endpoints        # reachable services + state\n"
                "      agmind creds show       # logins + passwords (root, masked)\n"
                "\n"
                "  • Status / logs:\n"
                "      agmind status\n"
                "      agmind logs <service> --follow\n"
                "\n"
                "  • Update config — re-run wizard:\n"
                "      agmind setup"
            )
        elif self.mode == "deploy_failure":
            return (
                "━━━━━━ Troubleshoot ━━━━━━\n"
                "\n"
                "  • Check what failed in scrollback above\n"
                "  • View recent service logs:\n"
                "      agmind logs --tail 200\n"
                "  • Rollback to previous state:\n"
                "      agmind rollback\n"
                "  • Or try smaller profile first:\n"
                "      agmind setup   # выбери только 'core'"
            )
        else:
            # next_steps mode
            return (
                "━━━━━━ Next steps ━━━━━━\n"
                "\n"
                "  Option A — quick test (no sudo, user-writable stack):\n"
                "\n"
                f"      agmind deploy --apply \\\n"
                f"        --domain {self.domain} \\\n"
                f"{selection_flags} \\\n"
                f"        --install-dir {self.install_dir} \\\n"
                "        --no-prompt\n"
                "\n"
                "  Option B — full install через AGmind CLI (systemd + secrets):\n"
                "\n"
                "      agmind install --no-tui \\\n"
                f"        --from-state {self.state_path} \\\n"
                f"        --domain {self.domain} \\\n"
                f"        --cf-token-file {self.token_path}\n"
                "\n"
                "  Option C — повторить wizard с auto-deploy:\n"
                "      agmind setup"
            )

    def _deploy_result_text(self) -> str:
        if self.deploy_result is None:
            return ""
        r = self.deploy_result
        icon = "✓" if r.success else "✗"
        lines = [f"  {icon} {r.message}"]
        if r.diff is not None:
            lines.append(f"  📋 changes applied: {r.diff.total_changes}")
        if r.snapshot is not None:
            lines.append(f"  📸 snapshot: {r.snapshot.id}")
        if r.rollback_performed:
            lines.append("  ↩️  rolled back to snapshot")
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.action_quit()

    def action_quit(self) -> None:
        self.app.exit()
