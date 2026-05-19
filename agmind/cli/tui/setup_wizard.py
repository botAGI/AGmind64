"""Phase J: Textual setup wizard — `agmind setup`.

Заменяет задротские CLI команды:
    agmind render compose --profile core,observability --domain X.com --output /opt/agmind/docker-compose.yml
    docker compose -f /opt/agmind/docker-compose.yml config --quiet
    agmind deploy --apply --domain X.com --profile core,observability --no-prompt

на ОДНУ команду:
    agmind setup

→ интерактивный wizard в терминале с auto-detect железа, валидацией ввода
   (домен contains '.', CF token ≥20 chars), preview изменений, one-click apply.

Сохраняет state в `/var/lib/agmind/setup-state.json` для re-run + позволяет
non-interactive deploy через `agmind setup --from-state file.json`.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static

from agmind.cli.tui.logo import AnimatedLogo
from agmind.log import logger

log = logger(__name__)

# User-writable locations (no sudo нужен для setup wizard).
# Ansible playbook потом копирует token в /var/lib/agmind/secrets/cf_dns_api_token.
_USER_DATA_DIR = Path.home() / ".local" / "share" / "agmind"
STATE_PATH = _USER_DATA_DIR / "setup-state.json"
TOKEN_PATH = _USER_DATA_DIR / "cf_dns_api_token"
DEFAULT_INSTALL_DIR = Path("/opt/agmind")


@dataclass
class SetupState:
    """Все собранные ответы wizard'а."""

    domain: str = ""
    cf_api_token: str = ""
    profiles: list[str] = field(default_factory=lambda: ["core", "observability"])
    backend: str = "auto"
    model_tier: str = "auto"
    install_dir: str = str(DEFAULT_INSTALL_DIR)

    def to_json(self, path: Path) -> None:
        """Save state (БЕЗ cf_api_token — он в secret file)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("cf_api_token")  # секреты НЕ в state.json
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> SetupState:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


@dataclass(frozen=True)
class DetectedHardware:
    """Auto-detected с этой машины."""

    ram_gb: float
    gpu_name: str | None
    is_strix_halo: bool
    vulkan_present: bool
    rocm_present: bool
    docker_present: bool
    recommended_tier: str
    """S | M | L | XL по RAM."""


def detect_hardware() -> DetectedHardware:
    """Quick host detection — не требует root."""
    ram_bytes = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    ram_bytes = int(line.split()[1]) * 1024
                    break
    except OSError:
        pass
    ram_gb = ram_bytes / (1024**3)

    # Tier по RAM (см. agmind/models.py::_TIER_RAM_THRESHOLDS_GB)
    if ram_gb >= 128:
        recommended_tier = "XL"
    elif ram_gb >= 64:
        recommended_tier = "L"
    elif ram_gb >= 32:
        recommended_tier = "M"
    elif ram_gb >= 16:
        recommended_tier = "S"
    else:
        recommended_tier = "S"  # минимум

    # GPU detection через lspci (без нужды в root)
    gpu_name = None
    is_strix_halo = False
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line_low = line.lower()
            if "vga" in line_low or "display" in line_low or "3d" in line_low:
                gpu_name = line.split(":", 2)[-1].strip()
                # AMD device ID 1586 = gfx1151 Strix Halo
                if "1586" in line or "ryzen ai max" in line_low:
                    is_strix_halo = True
                break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    vulkan_present = shutil.which("vulkaninfo") is not None
    rocm_present = (
        shutil.which("rocminfo") is not None
        or Path("/opt/rocm-7.2.3/bin/rocminfo").exists()
    )
    docker_present = shutil.which("docker") is not None

    return DetectedHardware(
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        is_strix_halo=is_strix_halo,
        vulkan_present=vulkan_present,
        rocm_present=rocm_present,
        docker_present=docker_present,
        recommended_tier=recommended_tier,
    )


# ---- Available profiles + backends (для UI choices) ----

PROFILES_AVAILABLE = [
    ("core", "Core inference (llama-server + qdrant + traefik)"),
    ("rag", "RAG stack (Dify + Postgres + Redis + Docling)"),
    ("ragflow", "RAGFlow (Elasticsearch + MySQL + MinIO)"),
    ("ui", "Open WebUI chat frontend"),
    ("observability", "Prometheus + Grafana + Loki + Alertmanager"),
    ("security", "Authelia auth gateway + fail2ban"),
]

# Textual Select tuples format: (prompt, value)
BACKENDS_AVAILABLE = [
    ("Auto-detect (recommended)", "auto"),
    ("Vulkan (RADV) — primary for Strix Halo", "vulkan"),
    ("ROCm — for batch embed / PP-bound workloads", "rocm"),
    ("CPU only — fallback", "cpu"),
]


# ============================================================
# Textual App
# ============================================================

class AgmindSetupApp(App[SetupState | None]):
    """One-screen setup wizard."""

    CSS_PATH: ClassVar[str | None] = "styles.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "submit", "Apply", show=True, priority=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+p", "preview", "Preview diff", show=True),
    ]

    TITLE = "AGmindx86 Setup Wizard"
    SUB_TITLE = "Phase J — TUI replacement для CLI flags"

    def __init__(
        self,
        detected: DetectedHardware | None = None,
        initial_state: SetupState | None = None,
        auto_deploy: bool = False,
    ) -> None:
        super().__init__()
        self.detected = detected or detect_hardware()
        self.state = initial_state or SetupState()
        self.result_state: SetupState | None = None
        self.preview_text: str = ""
        self.auto_deploy = auto_deploy
        """Если True — Apply сразу запускает DeployProgressScreen внутри TUI."""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        # Animated colorful gradient logo (Phase J — pyfiglet + Rich Text)
        yield AnimatedLogo(text="AGMIND", subtitle="x86 · Strix Halo · Setup Wizard")

        # Detected hardware box
        with Vertical(id="detected-box"):
            yield Label("🖥  Detected hardware", classes="label")
            yield Static(self._detected_text(), id="detected-text")

        with VerticalScroll(id="form-container"):
            yield Label("Domain для Traefik TLS", classes="section")
            yield Static(
                "💡 Совет: используй subdomain (e.g. lab.yourdomain.com),\n"
                "    чтобы не конфликтовать с существующими сайтами на apex.",
                classes="hint",
            )
            yield Input(
                placeholder="lab.yourdomain.com",
                id="domain-input",
                value=self.state.domain,
            )

            yield Label("Cloudflare API token (Zone:DNS:Edit)", classes="section")
            yield Input(
                placeholder="paste token (hidden)",
                id="cf-token-input",
                value=self.state.cf_api_token,
                password=True,
            )

            yield Label("Backend", classes="section")
            yield Select(
                BACKENDS_AVAILABLE,
                id="backend-select",
                value=self.state.backend,
                allow_blank=False,
            )

            yield Label("Profiles (выбери что разворачивать)", classes="section")
            with Container(id="profile-checkboxes"):
                for name, descr in PROFILES_AVAILABLE:
                    yield Checkbox(
                        f"{name:<15} — {descr}",
                        id=f"profile-{name}",
                        value=(name in self.state.profiles),
                    )

        with Horizontal(id="button-row"):
            yield Button("Preview", id="preview-btn", variant="primary")
            yield Button("Apply", id="apply-btn", variant="success")
            yield Button("Quit", id="quit-btn", variant="default")

        yield Static("", id="status-msg")
        yield Footer()

    def _detected_text(self) -> str:
        d = self.detected
        gpu = d.gpu_name or "(none detected)"
        strix = "✓ Strix Halo (gfx1151)" if d.is_strix_halo else "✗ not Strix Halo"
        vulkan = "✓" if d.vulkan_present else "✗"
        rocm = "✓" if d.rocm_present else "✗"
        docker = "✓" if d.docker_present else "✗ (требуется!)"
        return (
            f"RAM:     {d.ram_gb:.1f} GB → recommended tier: {d.recommended_tier}\n"
            f"GPU:     {gpu}\n"
            f"Type:    {strix}\n"
            f"Vulkan:  {vulkan}    ROCm: {rocm}    Docker: {docker}\n"
            f"Kernel:  {platform.release()}"
        )

    def _collect_state(self) -> SetupState:
        """Gather inputs from widgets into SetupState."""
        domain = self.query_one("#domain-input", Input).value.strip()
        cf_token = self.query_one("#cf-token-input", Input).value.strip()
        backend_select = self.query_one("#backend-select", Select)
        backend = str(backend_select.value) if backend_select.value is not None else "auto"

        profiles: list[str] = []
        for name, _ in PROFILES_AVAILABLE:
            cb = self.query_one(f"#profile-{name}", Checkbox)
            if cb.value:
                profiles.append(name)

        return SetupState(
            domain=domain,
            cf_api_token=cf_token,
            profiles=profiles,
            backend=backend,
            model_tier=self.detected.recommended_tier,
            install_dir=self.state.install_dir,
        )

    def _validate(self, state: SetupState) -> list[str]:
        """Returns list of error messages (empty = valid)."""
        errors: list[str] = []
        if not state.domain or "." not in state.domain:
            errors.append("domain должен содержать '.' (e.g. lab.yourcompany.com)")
        if len(state.cf_api_token) < 20:
            errors.append("CF API token < 20 chars — неверный")
        if not state.profiles:
            errors.append("Выбери хотя бы один profile")
        if not self.detected.docker_present:
            errors.append("Docker не установлен — apt install docker.io")
        return errors

    def _set_status(self, msg: str, kind: str = "") -> None:
        widget = self.query_one("#status-msg", Static)
        widget.update(msg)
        widget.set_classes(f"status-msg {kind}" if kind else "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit-btn":
            self.exit(None)
        elif event.button.id == "preview-btn":
            self.action_preview()
        elif event.button.id == "apply-btn":
            self.action_submit()

    def action_preview(self) -> None:
        state = self._collect_state()
        errors = self._validate(state)
        if errors:
            self._set_status("❌ " + "; ".join(errors), kind="error")
            return

        try:
            from agmind.services.renderer import render_to_string
            preview = render_to_string(
                profiles=state.profiles,
                domain=state.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"❌ render failed: {exc}", kind="error")
            return

        lines = preview.splitlines()
        self.preview_text = preview
        self._set_status(
            f"✓ Compose rendered ({len(lines)} lines). "
            f"Profiles: {','.join(state.profiles)}. "
            f"Backend: {state.backend}. Domain: {state.domain}",
            kind="success",
        )

    def action_submit(self) -> None:
        state = self._collect_state()
        errors = self._validate(state)
        if errors:
            self._set_status("❌ " + "; ".join(errors), kind="error")
            return

        # Save state (excluded cf_api_token) к ~/.local/share/agmind/
        try:
            state.to_json(STATE_PATH)
        except OSError as exc:
            self._set_status(f"⚠️ couldn't save state: {exc}", kind="error")
            return

        # Save CF token в отдельный файл с chmod 600
        try:
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(state.cf_api_token, encoding="utf-8")
            TOKEN_PATH.chmod(0o600)
        except OSError as exc:
            self._set_status(f"⚠️ couldn't save token: {exc}", kind="error")
            return

        self.result_state = state
        user_stack_dir = Path.home() / ".local" / "share" / "agmind" / "stack"

        # Phase J.1.6: всё в одном TUI app. Apply → (deploy progress?) → SummaryScreen → quit.
        if self.auto_deploy:
            from agmind.cli.tui.deploy_screen import DeployProgressScreen
            from agmind.cli.tui.summary_screen import SummaryScreen

            def _after_deploy(deploy_result: object) -> None:
                # Attach result к state для typer post-exit
                if deploy_result is not None:
                    state.__dict__["_deploy_result"] = deploy_result
                # Push SummaryScreen (success или failure) внутри TUI
                from agmind.deploy.runner import DeployResult as _DR
                mode = "deploy_success" if (
                    isinstance(deploy_result, _DR) and deploy_result.success
                ) else "deploy_failure"
                self.push_screen(
                    SummaryScreen(
                        mode=mode,
                        domain=state.domain,
                        profiles=state.profiles,
                        backend=state.backend,
                        model_tier=state.model_tier,
                        state_path=STATE_PATH,
                        token_path=TOKEN_PATH,
                        install_dir=user_stack_dir,
                        deploy_result=deploy_result if isinstance(deploy_result, _DR) else None,
                    )
                )

            self.push_screen(
                DeployProgressScreen(
                    profiles=state.profiles,
                    domain=state.domain,
                    install_dir=user_stack_dir,
                ),
                callback=_after_deploy,
            )
        else:
            # Manual deploy mode — push SummaryScreen с next-steps инструкциями.
            # User читает internal TUI summary → нажимает Close → exit.
            from agmind.cli.tui.summary_screen import SummaryScreen

            self.push_screen(
                SummaryScreen(
                    mode="next_steps",
                    domain=state.domain,
                    profiles=state.profiles,
                    backend=state.backend,
                    model_tier=state.model_tier,
                    state_path=STATE_PATH,
                    token_path=TOKEN_PATH,
                    install_dir=user_stack_dir,
                )
            )


def run_setup_wizard(
    initial_state: SetupState | None = None,
    auto_deploy: bool = False,
) -> SetupState | None:
    """Launch wizard, return collected state or None если cancelled.

    Если auto_deploy=True — Apply внутри wizard pushes DeployProgressScreen с
    live прогрессом docker compose up + healthcheck wait. Без auto_deploy
    Apply просто сохраняет config и exit'ит.
    """
    detected = detect_hardware()
    app = AgmindSetupApp(
        detected=detected,
        initial_state=initial_state,
        auto_deploy=auto_deploy,
    )
    return app.run()
