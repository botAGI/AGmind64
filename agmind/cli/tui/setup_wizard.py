"""Phase J: Textual setup wizard — `agmind setup`.

Заменяет задротские CLI команды:
    agmind render compose --profile core,observability --domain X.com --output /opt/agmind/docker-compose.yml
    docker compose --env-file /opt/agmind/.env -f /opt/agmind/docker-compose.yml config --quiet
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
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.validation import ValidationResult, Validator
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static


class DomainValidator(Validator):
    """Inline validator для domain Input — non-empty + содержит точку + не placeholder.

    M4.3: messages через i18n.t() — supports EN/RU.
    """

    def validate(self, value: str) -> ValidationResult:
        from agmind.i18n import t

        v = value.strip()
        if not v:
            return self.failure(
                t(
                    "wizard.validation.domain_empty",
                    default="required (e.g. lab.example.com)",
                )
            )
        if "." not in v:
            return self.failure(
                t(
                    "wizard.validation.domain_no_dot",
                    default="must contain '.'",
                )
            )
        if v == "agmind.dev":
            return self.failure(
                t(
                    "wizard.validation.domain_placeholder",
                    default="agmind.dev is placeholder — use your own",
                )
            )
        return self.success()


class TokenLengthValidator(Validator):
    """Inline validator для CF API token — empty OK (filled later), ≥20 chars если есть."""

    def validate(self, value: str) -> ValidationResult:
        from agmind.i18n import t

        v = value.strip()
        if not v:
            return self.success()  # empty ok (token loaded из --cf-token-file)
        if len(v) < 20:
            msg = t(
                "wizard.validation.token_too_short",
                default="too short ({n} chars, expected ≥20)",
            )
            return self.failure(msg.format(n=len(v)))
        return self.success()


class AGCheckbox(Checkbox):
    """Pip-boy checkbox: `[ ]` unselected, `[✓]` selected.

    M4.7.3 fix — older AGCheckbox renderил BUTTON_INNER='✓' одинаково в обоих
    состояниях (Textual styles differ только color). User percepted "all
    checked". Override `_button` property чтобы glyph меняется по value.
    """

    BUTTON_LEFT = "["
    BUTTON_RIGHT = "]"
    BUTTON_INNER = "✓"

    @property
    def _button(self):  # type: ignore[no-untyped-def]
        # Override Textual ToggleButton._button: swap glyph based on .value
        # (mainline всегда draws BUTTON_INNER, styles только color → looks like
        # all checked even при value=False).
        from textual.content import Content
        from textual.style import Style

        button_style = self.get_visual_style("toggle--button")
        side_style = Style(
            foreground=button_style.background,
            background=self.background_colors[1],
        )
        inner = self.BUTTON_INNER if self.value else " "
        return Content.assemble(
            (self.BUTTON_LEFT, side_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, side_style),
        )

    def watch_value(self) -> None:
        # Re-render button row on toggle (force redraw, не just style swap)
        super().watch_value()
        self.refresh()


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
    "llama-llm",
    "llama-embed",
    "llama-rerank",
    "qdrant",
    "prometheus",
    "grafana",
    "loki",
    "alloy",
    "alertmanager",
    "node-exporter",
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

    # Phase N.G: LLM model selector + inference settings.
    # model_id != "custom" → use curated catalog. id == "custom" → repo/file заполняет user.
    model_id: str = "qwen36-a3b-q4km"
    """LLM curated catalog id (agmind.install.models.CURATED_MODELS, kind=llm) or 'custom'."""
    model_repo: str = ""
    """HF repo id для LLM, e.g. 'TheBloke/Llama-2-7B-GGUF'. Filled из catalog или вручную."""
    model_file: str = ""
    """GGUF filename inside repo. Empty = skip LLM download step."""
    ctx_size: int = 16384
    """llama-llm --ctx-size flag."""
    kv_cache_type: str = "q8_0"
    """LLM KV cache quant (passed как both --cache-type-k и --cache-type-v)."""
    threads: int = -1
    """llama-llm --threads. -1 = auto (server picks CPU count)."""
    parallel_slots: int = 1
    """llama-llm --parallel. >1 enables continuous batching N concurrent requests."""

    # Phase M5.1: separate embed model selector + per-service inference settings.
    embed_model_id: str = "bge-m3-q8"
    """Curated embed catalog id (CURATED_MODELS, kind=embed) or 'custom'."""
    embed_repo: str = ""
    """HF repo id для embed model (resolved из catalog когда id != 'custom')."""
    embed_file: str = ""
    """GGUF filename для embed model. Empty = skip embed download."""
    embed_ctx_size: int = 8192
    """llama-embed --ctx-size. BGE-M3 native max = 8192."""
    embed_kv_cache: str = "f16"
    """Embed KV cache quant. f16 = default (short inputs, no memory pressure)."""
    embed_parallel: int = 4
    """llama-embed --parallel. Embed = high concurrency (short inputs, batch friendly)."""

    # Phase M5.1: separate rerank model selector.
    rerank_model_id: str = "bge-reranker-v2-m3-q8"
    """Curated rerank catalog id (CURATED_MODELS, kind=rerank) or 'custom'."""
    rerank_repo: str = ""
    """HF repo id для rerank model."""
    rerank_file: str = ""
    """GGUF filename для rerank model. Empty = skip rerank download / disable service."""
    rerank_ctx_size: int = 2048
    """llama-rerank --ctx-size. Reranker inputs обычно короткие (query+doc)."""

    # Phase M5.4: cluster integration.
    cluster_replicate: bool = False
    """True → wizard ConfirmScreen генерирует Ansible inventory + replicates на peers."""

    def to_json(self, path: Path) -> None:
        """Save state (БЕЗ cf_api_token — он в secret file)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("cf_api_token")  # секреты НЕ в state.json
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> SetupState:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Phase N.G fields may be missing в old state.json — backward-compat
        # default'ы из @dataclass.
        known = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)

    def resolve_model_repo_file(self) -> tuple[str, str]:
        """Resolve final (repo, file) for LLM download — curated id или custom values."""
        return self._resolve_repo_file(self.model_id, self.model_repo, self.model_file)

    def resolve_embed_repo_file(self) -> tuple[str, str]:
        """Resolve embed model (repo, file). Empty file = skip download."""
        return self._resolve_repo_file(self.embed_model_id, self.embed_repo, self.embed_file)

    def resolve_rerank_repo_file(self) -> tuple[str, str]:
        """Resolve rerank model (repo, file). Empty file = skip download."""
        return self._resolve_repo_file(self.rerank_model_id, self.rerank_repo, self.rerank_file)

    @staticmethod
    def _resolve_repo_file(model_id: str, raw_repo: str, raw_file: str) -> tuple[str, str]:
        if model_id == "custom":
            return raw_repo, raw_file
        from agmind.install.models import find_by_id

        entry = find_by_id(model_id)
        if entry is not None:
            return entry.repo, entry.file
        return raw_repo, raw_file


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
        result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5, check=False)
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
        shutil.which("rocminfo") is not None or Path("/opt/rocm-7.2.3/bin/rocminfo").exists()
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
    # Phase M4.7 — Fallout pip-boy angle brackets (square parsed как markup).
    "edge": "<NET> EDGE & ROUTING",
    "inference": "<LLM> INFERENCE",
    "app": "<APP> APPLICATIONS",
    "storage": "<STR> STORAGE",
    "ops": "<OPS> OPERATIONS",
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


def expand_selected_services_for_setup(services: list[str]) -> list[str]:
    """Expand setup checkbox choices into a deployable service closure."""
    if not services:
        return []
    try:
        from agmind.components import load_component_contracts
        from agmind.services.renderer import load_descriptors
        from agmind.services.selection import resolve_service_selection

        descriptors = load_descriptors()
        selected = resolve_service_selection(
            descriptors,
            services=services,
            component_contracts=load_component_contracts(),
        )
        return sorted(selected)
    except Exception:
        return list(dict.fromkeys(services))


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
        desc = _PROFILE_DESCRIPTIONS.get(prof, "custom profile")
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

    # Phase M4.7: Fallout pip-boy style header
    TITLE = "ROBCO INDUSTRIES (TM) TERMLINK · AGMIND x86"
    SUB_TITLE = "── PRIVATE LLM/RAG STACK · SETUP TERMINAL ──"

    def __init__(
        self,
        detected: DetectedHardware | None = None,
        initial_state: SetupState | None = None,
        auto_deploy: bool = False,
        multi_step: bool | None = None,
    ) -> None:
        super().__init__()
        self.detected = detected or detect_hardware()
        self.state = initial_state or SetupState()
        self.result_state: SetupState | None = None
        self.preview_text: str = ""
        self.auto_deploy = auto_deploy
        """Если True — Apply сразу запускает DeployProgressScreen внутри TUI."""
        # Phase M4.1: multi-step wizard теперь DEFAULT.
        # Escape hatch: AGMIND_WIZARD_LEGACY=1 или multi_step=False kwarg.
        import os as _os_module

        if multi_step is None:
            legacy_env = _os_module.environ.get("AGMIND_WIZARD_LEGACY", "0") == "1"
            # Backward compat: старый AGMIND_WIZARD_MULTISTEP=0 был "off" → теперь default on
            multistep_env = _os_module.environ.get("AGMIND_WIZARD_MULTISTEP", "")
            if multistep_env == "0" or legacy_env:
                multi_step = False
            else:
                multi_step = True
        self.multi_step = multi_step
        # Discover profiles + backends + services-by-tier dynamically.
        self.profiles_available = get_available_profiles()
        self.backends_available = get_available_backends()
        # Phase J.1.8: per-service selection grouped by tier
        self.services_by_tier = get_services_by_tier()
        # Phase M5.4: cluster peers — populated lazily (mDNS browse) при первом read.
        self._cluster_peers_cache: list[tuple[str, str]] | None = None

    @property
    def cluster_peers(self) -> list[tuple[str, str]]:
        """List of (hostname, address) для detected mDNS peers (cached)."""
        if self._cluster_peers_cache is None:
            try:
                from agmind.cluster.detect import discover

                peers = discover(timeout=0.5, exclude_self=True)
                self._cluster_peers_cache = [(p.hostname, p.address) for p in peers]
            except Exception:
                self._cluster_peers_cache = []
        return self._cluster_peers_cache

    def on_mount(self) -> None:
        # Phase M3.S.2: multi-step mode pushes DomainScreen вместо single-screen render.
        if self.multi_step:
            from agmind.cli.tui.wizard_screens import DomainScreen

            self.push_screen(DomainScreen())

    def compose(self) -> ComposeResult:
        if self.multi_step:
            # Multi-step: только Header + Footer как root container,
            # реальные widgets живут в pushed Screens (см. on_mount).
            yield Header(show_clock=False)
            yield Footer()
            return
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
                validators=[DomainValidator()],
            )

            yield Label("Cloudflare API token (Zone:DNS:Edit)", classes="section")
            yield Input(
                placeholder="paste token (hidden)",
                id="cf-token-input",
                value=self.state.cf_api_token,
                password=True,
                validators=[TokenLengthValidator()],
            )

            yield Label("Backend", classes="section")
            yield Select(
                self.backends_available,
                id="backend-select",
                value=self.state.backend,
                allow_blank=False,
            )

            # Phase N.G: model selector
            from agmind.install.models import (
                CTX_SIZE_PRESETS,
                KV_CACHE_TYPES,
                models_for_wizard,
            )

            yield Label("Model", classes="section")
            model_options = models_for_wizard()
            model_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                model_options,
                id="model-select",
                value=self.state.model_id,
                allow_blank=False,
            )
            # Inputs для custom HF — видны всегда, заполняются если выбран Custom
            yield Static(
                "Custom HF (fill only если выбран 'Custom HuggingFace'):",
                classes="hint",
            )
            yield Input(
                placeholder="HF repo id: user/repo-name",
                id="model-repo-input",
                value=self.state.model_repo,
            )
            yield Input(
                placeholder="GGUF filename: model.Q4_K_M.gguf",
                id="model-file-input",
                value=self.state.model_file,
            )

            yield Label("Context size", classes="section")
            yield Select(
                [(label, str(n)) for n, label in CTX_SIZE_PRESETS],
                id="ctx-size-select",
                value=str(self.state.ctx_size),
                allow_blank=False,
            )

            yield Label("KV cache quantization", classes="section")
            yield Select(
                [(label, val) for val, label in KV_CACHE_TYPES],
                id="kv-cache-select",
                value=self.state.kv_cache_type,
                allow_blank=False,
            )

            # Inference threads + parallel slots
            from agmind.install.models import PARALLEL_PRESETS, THREADS_PRESETS

            yield Label("CPU threads", classes="section")
            yield Select(
                [(label, str(n)) for n, label in THREADS_PRESETS],
                id="threads-select",
                value=str(self.state.threads),
                allow_blank=False,
            )
            yield Label("Parallel slots (concurrent requests)", classes="section")
            yield Select(
                [(label, str(n)) for n, label in PARALLEL_PRESETS],
                id="parallel-select",
                value=str(self.state.parallel_slots),
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
        return f"{d.ram_gb:.0f} GB · {gpu} · Vulkan {vk} ROCm {rc} Docker {dk} · tier={d.recommended_tier}"

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

        # Phase N.G: model + context settings
        model_select = self.query_one("#model-select", Select)
        model_id = str(model_select.value) if model_select.value is not None else "qwen36-a3b-q4km"
        model_repo = self.query_one("#model-repo-input", Input).value.strip()
        model_file = self.query_one("#model-file-input", Input).value.strip()
        # If curated id selected — resolve repo+file из catalog (overrides empty inputs)
        if model_id != "custom":
            from agmind.install.models import find_by_id

            entry = find_by_id(model_id)
            if entry is not None:
                model_repo = entry.repo
                model_file = entry.file

        ctx_select = self.query_one("#ctx-size-select", Select)
        try:
            ctx_size = int(str(ctx_select.value)) if ctx_select.value is not None else 16384
        except ValueError:
            ctx_size = 16384

        kv_select = self.query_one("#kv-cache-select", Select)
        kv_cache_type = str(kv_select.value) if kv_select.value is not None else "q8_0"

        threads_select = self.query_one("#threads-select", Select)
        try:
            threads = int(str(threads_select.value)) if threads_select.value is not None else -1
        except ValueError:
            threads = -1

        parallel_select = self.query_one("#parallel-select", Select)
        try:
            parallel_slots = (
                int(str(parallel_select.value)) if parallel_select.value is not None else 1
            )
        except ValueError:
            parallel_slots = 1

        return SetupState(
            domain=domain,
            cf_api_token=cf_token,
            services=expand_selected_services_for_setup(services),
            profiles=[],  # cleared — services field теперь primary
            backend=backend,
            model_tier=self.detected.recommended_tier,
            install_dir=self.state.install_dir,
            model_id=model_id,
            model_repo=model_repo,
            model_file=model_file,
            ctx_size=ctx_size,
            kv_cache_type=kv_cache_type,
            threads=threads,
            parallel_slots=parallel_slots,
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
        # Phase O.A (revised): compat issues — warnings only, не блокируют Apply.
        # См. ADR-0011 amendment.
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

    def _check_compatibility(self, state: SetupState):  # type: ignore[no-untyped-def]
        """Phase O.A: detect conflicts / redundant providers / missing capabilities.

        Returns CompatReport or None если selection пуст.
        """
        if not state.services:
            return None
        try:
            from agmind.services.compatibility import check_service_compatibility
            from agmind.services.renderer import load_descriptors, select_services

            all_d = load_descriptors()
            selected = select_services(all_d, services=state.services)
            return check_service_compatibility(selected)
        except Exception:
            return None

    def _deployment_topology_report(self, state: SetupState):  # type: ignore[no-untyped-def]
        """Build the shared deployment topology report for preview/status UI."""
        if not state.services:
            return None
        try:
            from agmind.services.deployment_topology import (
                build_deployment_topology_report_for_services,
            )

            return build_deployment_topology_report_for_services(state.services)
        except Exception:
            return None

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
            # Phase M3.S.1: toast вместо persistent status-msg
            self.notify("\n".join(errors), title="Validation errors", severity="error", timeout=8.0)
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
            self.notify(str(exc), title="Render failed", severity="error", timeout=10.0)
            self._set_status(f"❌ render failed: {exc}", kind="error")
            return

        # Shared topology report — warnings only, не блокируй.
        topology = self._deployment_topology_report(state)
        dep_warn = ""
        dependency_warnings = topology.dependency_warnings if topology is not None else ()
        if dependency_warnings:
            details = "; ".join(dependency_warnings[:3])
            dep_warn = f" ⚠️ Missing deps: {details}"

        compat_warn = ""
        compatibility_warnings = topology.compatibility_warnings if topology is not None else ()
        if compatibility_warnings:
            compat_warn = " ⚠️ " + "; ".join(compatibility_warnings[:2])

        lines = preview.splitlines()
        self.preview_text = preview
        status_kind = "error" if dependency_warnings else "success"
        summary = (
            f"Compose rendered ({len(lines)} lines). "
            f"Services: {len(state.services)}. "
            f"Backend: {state.backend}. Domain: {state.domain}."
        )
        self._set_status(f"✓ {summary}{dep_warn}{compat_warn}", kind=status_kind)
        # Phase M3.S.1: Toast для immediate feedback (status-msg остаётся как
        # archive — toast исчезает 4s)
        severity: Literal["information", "warning"] = "information"
        if dependency_warnings or compat_warn:
            severity = "warning"
        self.notify(summary + (compat_warn or ""), title="Preview", severity=severity)

    def action_submit(self) -> None:
        state = self._collect_state()
        errors = self._validate(state)
        if errors:
            # Phase M3.S.1: toast + status — toast вылетает первым (visible)
            self.notify("\n".join(errors), title="Cannot apply", severity="error", timeout=10.0)
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
    multi_step: bool | None = None,
) -> SetupState | None:
    """Launch wizard, return collected state or None если cancelled.

    Args:
        initial_state: pre-populated SetupState (для resume / CLI flags)
        auto_deploy: True → Apply pushes DeployProgressScreen с live progress
        multi_step: None → default per env (M4.1 — default True);
                    True/False explicit override
    """
    detected = detect_hardware()
    app = AgmindSetupApp(
        detected=detected,
        initial_state=initial_state,
        auto_deploy=auto_deploy,
        multi_step=multi_step,
    )
    return app.run()
