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


class AGCheckbox(Checkbox):
    """Compact checkbox with `[✓] / [ ]` glyphs вместо толстых `▐X▌` блоков."""

    BUTTON_LEFT = "["
    BUTTON_RIGHT = "]"
    BUTTON_INNER = "✓"

from agmind.cli.tui.logo import AnimatedLogo
from agmind.log import logger

log = logger(__name__)

# User-writable locations (no sudo нужен для setup wizard).
# Ansible playbook потом копирует token в /var/lib/agmind/secrets/cf_dns_api_token.
_USER_DATA_DIR = Path.home() / ".local" / "share" / "agmind"
STATE_PATH = _USER_DATA_DIR / "setup-state.json"
TOKEN_PATH = _USER_DATA_DIR / "cf_dns_api_token"
DEFAULT_INSTALL_DIR = Path("/opt/agmind")


# Smart defaults — минимальный production set (Phase J.1.8):
# 11 services: traefik (edge) + llama-* (inference) + qdrant (storage) + 6 ops.
_DEFAULT_SERVICES = {
    "traefik",
    "llama-llm", "llama-embed", "llama-rerank",
    "qdrant",
    "prometheus", "grafana", "loki", "alloy", "alertmanager", "node-exporter",
}


@dataclass
class SetupState:
    """Все собранные ответы wizard'а."""

    domain: str = ""
    cf_api_token: str = ""
    profiles: list[str] = field(default_factory=list)
    """Legacy bulk filter (для backward compat). Phase J.1.8 предпочитает services."""
    services: list[str] = field(default_factory=lambda: sorted(_DEFAULT_SERVICES))
    """Phase J.1.8: per-service selection — explicit list of service names."""
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


# ---- Profile + backend discovery (Phase J.1.7: dynamic, не hardcode) ----

# Human-friendly descriptions для known profiles. Если profile not в этом dict —
# generates description "<N services>". Можно расширять без правки TUI.
_PROFILE_DESCRIPTIONS: dict[str, str] = {
    "core": "Core inference (llama + qdrant + traefik)",
    "rag": "RAG stack (Dify + Postgres + Redis + Docling)",
    "ragflow": "RAGFlow (Elasticsearch + MySQL + MinIO)",
    "ui": "Open WebUI chat frontend",
    "observability": "Prometheus + Grafana + Loki + Alertmanager",
    "security": "Authelia + fail2ban",
    "core-caddy": "Core с Caddy вместо Traefik",
    "core-nginx": "Core с Nginx (без публичного домена)",
    "rag-weaviate": "RAG с Weaviate вместо Qdrant",
    "rag-milvus": "RAG с Milvus вместо Qdrant",
    "full": "Все профили вместе",
}

_BACKEND_DESCRIPTIONS: dict[str, str] = {
    "auto": "Auto-detect (recommended)",
    "vulkan": "Vulkan (RADV) — primary для Strix Halo",
    "rocm": "ROCm — для batch embed / PP-bound workloads",
    "cpu": "CPU only — fallback",
    "npu": "NPU (placeholder)",
}


# Phase J.1.8: per-service discovery (replaces profile-based selection в UI).

_TIER_LABELS: dict[str, str] = {
    "edge": "🌐 Edge & Routing",
    "inference": "🧠 Inference (LLM)",
    "app": "📦 Apps",
    "storage": "💾 Storage",
    "ops": "📊 Operations",
}

# Visual tier order top-down в UI
_TIER_ORDER = ["edge", "inference", "app", "storage", "ops"]


def get_services_by_tier() -> dict[str, list[tuple[str, str]]]:
    """Discover все services из templates/services/*.yaml grouped by tier.

    Returns: {tier_name: [(service_name, "purpose"), ...]}
    Sort: tier order matches _TIER_ORDER, services внутри отсортированы по имени.
    """
    try:
        from agmind.services.renderer import load_descriptors
    except ImportError:
        return {}

    from collections import defaultdict

    descriptors = load_descriptors()
    by_tier: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name in sorted(descriptors):
        d = descriptors[name]
        # Краткое описание: tier + первая строка purpose
        purpose = (d.purpose or "").split(".")[0].strip()[:60]
        by_tier[d.tier].append((d.name, purpose or d.image))

    # Order by _TIER_ORDER
    ordered: dict[str, list[tuple[str, str]]] = {}
    for tier in _TIER_ORDER:
        if tier in by_tier:
            ordered[tier] = by_tier[tier]
    # Add любые unknown tiers в конец
    for tier in by_tier:
        if tier not in ordered:
            ordered[tier] = by_tier[tier]
    return ordered


def get_available_profiles() -> list[tuple[str, str]]:
    """Discover profiles из templates/services/*.yaml (32 service descriptors).

    Returns: list of (profile_name, "human description [N services]")
    Sorted: core* first, rag* second, others после.
    """
    try:
        from agmind.services.renderer import load_descriptors
    except ImportError:
        return [("core", "Core (fallback hardcoded)")]

    from collections import defaultdict

    descriptors = load_descriptors()
    profile_services: dict[str, list[str]] = defaultdict(list)
    for d in descriptors.values():
        for prof in d.profiles:
            profile_services[prof].append(d.name)

    # Sort: core* → rag* → ragflow → ui → observability → security → остальные
    def _sort_key(name: str) -> tuple[int, str]:
        if name.startswith("core"):
            return (0, name)
        if name.startswith("rag") and not name.startswith("ragflow"):
            return (1, name)
        if name == "ragflow":
            return (2, name)
        if name == "ui":
            return (3, name)
        if name == "observability":
            return (4, name)
        if name == "security":
            return (5, name)
        return (6, name)

    out: list[tuple[str, str]] = []
    for prof in sorted(profile_services, key=_sort_key):
        services = profile_services[prof]
        desc = _PROFILE_DESCRIPTIONS.get(prof, f"custom profile")
        out.append((prof, f"{desc} [{len(services)} services]"))
    return out


def get_available_backends() -> list[tuple[str, str]]:
    """Discover backends через setuptools entry_points (Phase H'.E plugin system).

    Returns: list of (prompt, value) для Textual Select.
    Always includes 'auto' first.
    """
    try:
        from agmind.compute._registry import discover_backend_names
        names = discover_backend_names()
    except Exception:
        # Graceful degrade — fallback на 4 встроенных
        names = ["cpu", "vulkan", "rocm", "npu"]

    out: list[tuple[str, str]] = [(_BACKEND_DESCRIPTIONS["auto"], "auto")]
    for name in names:
        if name == "auto":
            continue
        desc = _BACKEND_DESCRIPTIONS.get(name, name)
        out.append((desc, name))
    return out


# Backwards-compat module constants — re-evaluated on import.
# В тестах и при настройке monkey-patch'ятся через прямые function calls.
PROFILES_AVAILABLE: list[tuple[str, str]] = []  # populated lazy
BACKENDS_AVAILABLE: list[tuple[str, str]] = []  # populated lazy


# ============================================================
# Textual App
# ============================================================

class AgmindSetupApp(App[SetupState | None]):
    """One-screen setup wizard."""

    CSS_PATH: ClassVar[str | None] = "styles.tcss"

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
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
        # Discover profiles + backends + services-by-tier dynamically.
        self.profiles_available = get_available_profiles()
        self.backends_available = get_available_backends()
        # Phase J.1.8: per-service selection grouped by tier
        self.services_by_tier = get_services_by_tier()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        # Compact gradient logo (Phase J.1.10 — `cybermedium` font: 3 строки
        # вместо 6, без subtitle — он дублировал Header.SUB_TITLE).
        yield AnimatedLogo(text="AGMINDx86", subtitle="", font="cybermedium")

        # Single-line hardware summary
        yield Static(self._detected_text(), id="detected-text")

        with VerticalScroll(id="form-container"):
            yield Label("Domain (Traefik TLS, subdomain рекомендуется)", classes="section")
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
                self.backends_available,
                id="backend-select",
                value=self.state.backend,
                allow_blank=False,
            )

            total = sum(len(svcs) for svcs in self.services_by_tier.values())
            yield Label(f"Services ({total} available — defaults preselected)", classes="section")
            with Container(id="service-checkboxes"):
                for tier, services in self.services_by_tier.items():
                    tier_label = _TIER_LABELS.get(tier, tier)
                    # Each tier — visual bordered card
                    with Container(classes="tier-group"):
                        yield Label(
                            f"{tier_label}  ·  {len(services)} services",
                            classes="tier-section",
                        )
                        for name, _purpose in services:
                            # Compact: just the service name. Purpose редко короче имени
                            # и в 2-колоночном grid не помещается чисто.
                            yield AGCheckbox(
                                name,
                                id=f"svc-{self._slug(name)}",
                                value=(name in self.state.services),
                            )

        with Horizontal(id="button-row"):
            yield Button("Preview", id="preview-btn", variant="primary")
            yield Button("Apply", id="apply-btn", variant="success")
            yield Button("Quit", id="quit-btn", variant="default")

        yield Static("", id="status-msg")
        yield Footer()

    def _detected_text(self) -> str:
        d = self.detected
        gpu = "Strix Halo gfx1151" if d.is_strix_halo else (d.gpu_name or "no GPU")
        vk = "✓" if d.vulkan_present else "✗"
        rc = "✓" if d.rocm_present else "✗"
        dk = "✓" if d.docker_present else "✗!"
        return (
            f"{d.ram_gb:.0f} GB · {gpu} · Vulkan {vk} ROCm {rc} Docker {dk} · tier={d.recommended_tier}"
        )

    @staticmethod
    def _slug(name: str) -> str:
        """Convert profile name (может содержать дефисы типа core-caddy) → CSS-safe id."""
        return name.replace("-", "_")

    def _collect_state(self) -> SetupState:
        """Gather inputs from widgets into SetupState."""
        domain = self.query_one("#domain-input", Input).value.strip()
        cf_token = self.query_one("#cf-token-input", Input).value.strip()
        backend_select = self.query_one("#backend-select", Select)
        backend = str(backend_select.value) if backend_select.value is not None else "auto"

        # Phase J.1.8: collect per-service selection
        services: list[str] = []
        for tier_services in self.services_by_tier.values():
            for name, _ in tier_services:
                cb = self.query_one(f"#svc-{self._slug(name)}", Checkbox)
                if cb.value:
                    services.append(name)

        return SetupState(
            domain=domain,
            cf_api_token=cf_token,
            services=services,
            profiles=[],  # cleared — services field теперь primary
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
        if not state.services and not state.profiles:
            errors.append("Выбери хотя бы один service")
        if not self.detected.docker_present:
            errors.append("Docker не установлен — apt install docker.io")
        return errors

    def _check_dependencies(self, state: SetupState) -> dict[str, list[str]]:
        """Warn если выбраны services с unfulfilled depends_on."""
        if not state.services:
            return {}
        try:
            from agmind.services.renderer import (
                check_missing_dependencies,
                load_descriptors,
                select_services,
            )
            all_d = load_descriptors()
            selected = select_services(all_d, services=state.services)
            return check_missing_dependencies(selected, all_d)
        except Exception:
            return {}

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
                services=state.services,
                domain=state.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"❌ render failed: {exc}", kind="error")
            return

        # Check for missing dependencies — warn, не блокируй
        missing_deps = self._check_dependencies(state)
        dep_warn = ""
        if missing_deps:
            details = "; ".join(
                f"{name} нуждается в {','.join(deps)}"
                for name, deps in list(missing_deps.items())[:3]
            )
            dep_warn = f" ⚠️ Missing deps: {details}"

        lines = preview.splitlines()
        self.preview_text = preview
        self._set_status(
            f"✓ Compose rendered ({len(lines)} lines). "
            f"Services: {len(state.services)}. "
            f"Backend: {state.backend}. Domain: {state.domain}.{dep_warn}",
            kind="success" if not missing_deps else "error",
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
                from typing import Literal
                from agmind.deploy.runner import DeployResult as _DR
                mode: Literal["next_steps", "deploy_success", "deploy_failure"] = (
                    "deploy_success"
                    if isinstance(deploy_result, _DR) and deploy_result.success
                    else "deploy_failure"
                )
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
