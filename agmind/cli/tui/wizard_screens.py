"""Phase M3.S.2: multi-step wizard screens.

Opt-in via env var `AGMIND_WIZARD_MULTISTEP=1` или CLI flag `--multi-step`.
Default — legacy single-screen flow in AgmindSetupApp (backward compat).

Screens (sequential):
    1. DomainScreen  — domain + CF token (with inline validators)
    2. ModelScreen   — curated/custom + ctx/kv/threads/parallel
    3. ServicesScreen — per-tier checkboxes (reuses Phase J.1.8 layout)
    4. ConfirmScreen — summary + [Back] [Apply]

State shared через `self.app.state` (SetupState dataclass), Next/Back
buttons вызывают `app.next_step()` / `app.prev_step()`. App tracks
`current_step_index` для linear navigation.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from agmind.core.domain import validate_domain
from agmind.i18n import t
from agmind.services.retrieval_policy import DIFY_VECTOR_PROVIDERS

# ---- Shared helpers (M5.3) ----


def _legal_select_value(value: object, options: list) -> object:  # type: ignore[type-arg]
    """Coerce a Select's initial value to a legal option.

    Textual's Select(allow_blank=False) assigns the given value on mount and RAISES
    (InvalidSelectValue) if it isn't among the option values — which crashes the whole
    ModelScreen. This happens when app.state carries an off-list value: a hand-edited /
    older saved setup-state.json, or a curated model id that was removed from the catalog.
    Return the value when it matches an option, else fall back to the first option's value.
    """
    option_values = [opt[1] for opt in options]
    if value in option_values:
        return value
    return option_values[0] if option_values else value


def _format_hardware_panel(d) -> str:  # type: ignore[no-untyped-def]
    """M5.3.2: panel layout вместо single-line dim — Fallout pip-boy table.

    Detection state already collected на app startup (см. detect_hardware).
    """
    if d is None:
        return "[dim]NO HARDWARE DATA — running detached?[/dim]"
    gpu = "Strix Halo gfx1151" if d.is_strix_halo else (d.gpu_name or "no GPU")
    vk = "[bold green]OK[/bold green]" if d.vulkan_present else "[red]MISSING[/red]"
    rc = "[bold green]OK[/bold green]" if d.rocm_present else "[red]MISSING[/red]"
    dk = "[bold green]OK[/bold green]" if d.docker_present else "[red bold]MISSING[/red bold]"
    return (
        "[bold]── DETECTED HARDWARE ──[/bold]\n"
        f"  RAM ............. {d.ram_gb:.0f} GB\n"
        f"  GPU ............. {gpu}\n"
        f"  Vulkan .......... {vk}\n"
        f"  ROCm ............ {rc}\n"
        f"  Docker .......... {dk}\n"
        f"  Recommended tier  {d.recommended_tier}"
    )


def _format_cluster_peers_banner(peers: list[tuple[str, str]]) -> str:
    """M5.4.1: banner shown when ≥1 peer detected via mDNS."""
    if not peers:
        return ""
    listed = ", ".join(f"{h} ({a})" for h, a in peers[:3])
    extra = f" + {len(peers) - 3} more" if len(peers) > 3 else ""
    return f"[bold]CLUSTER PEERS DETECTED:[/bold] {len(peers)} — {listed}{extra}"


# ---- Shared header / footer / step indicator ----


def _make_step_header(step_n: int, total: int, title: str) -> Static:
    """Pip-boy step indicator — clean single-line bar (M4.7.2)."""
    dots = ("●" * step_n) + ("○" * (total - step_n))
    text = f"STEP {step_n}/{total}  ·  {title.upper()}  ·  {dots}"
    return Static(text, classes="step-header")


# Backwards-compat alias (used to be a class)
StepHeader = _make_step_header


# ---- DomainScreen (1/4) ----


class DomainScreen(Screen[None]):
    """Step 1: domain + CF token + auto-detect hardware panel + cluster peers."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("tab", "focus_next", "Next field"),
        Binding("alt+n", "next_step", "Next"),
        Binding("f1", "help", "Help"),
    ]

    def compose(self) -> ComposeResult:
        from agmind.cli.tui.setup_wizard import (
            DomainValidator,
            TokenLengthValidator,
        )

        yield Header(show_clock=False)
        yield StepHeader(1, 4, t("wizard.section.domain"))
        # M5.3.2: full-width hardware panel наверху wizard
        yield Static(_format_hardware_panel(self.app.detected), id="hardware-panel")
        # M5.4.1: cluster peers banner (если detected) — auto-discover hint
        peers = list(self.app.cluster_peers)
        peers_banner = _format_cluster_peers_banner(peers)
        if peers_banner:
            yield Static(peers_banner, classes="peers-banner")
        with VerticalScroll():
            yield Label(t("wizard.section.domain"), classes="section")
            yield Static("[dim](TLS via Cloudflare, subdomain recommended)[/dim]", classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.domain"),
                id="domain-input",
                value=self.app.state.domain,
                validators=[DomainValidator()],
            )
            yield Label(t("wizard.section.cf_token"), classes="section")
            yield Static("[dim](Zone:DNS:Edit — ≥20 chars, hidden input)[/dim]", classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.cf_token"),
                id="cf-token-input",
                value=self.app.state.cf_api_token,
                password=True,
                validators=[TokenLengthValidator()],
            )
            if getattr(self.app, "require_sudo_password", False):
                yield Label("Sudo password", classes="section")
                yield Static(
                    "[dim](bootstrap only — hidden input, never saved to state)[/dim]",
                    classes="hint",
                )
                yield Input(
                    placeholder="sudo password",
                    id="sudo-password-input",
                    value=getattr(self.app.state, "sudo_password", ""),
                    password=True,
                )
            # M5.4.2: cluster replicate checkbox (приходит вместе с peers banner)
            if peers:
                from agmind.cli.tui.setup_wizard import AGCheckbox

                yield AGCheckbox(
                    "Replicate stack to detected peers (mDNS auto-discover)",
                    id="cluster-replicate-checkbox",
                    value=getattr(self.app.state, "cluster_replicate", False),
                )
        with Horizontal(id="nav-row"):
            yield Button(t("wizard.btn.quit"), id="back-btn", variant="default")
            yield Button(t("wizard.btn.next"), id="next-btn", variant="primary")
        yield Footer()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.exit(None)
        elif event.button.id == "next-btn":
            self._save_and_advance()

    def action_next_step(self) -> None:
        self._save_and_advance()

    def _save_and_advance(self) -> None:
        raw_domain = self.query_one("#domain-input", Input).value.strip()
        self.app.state.cf_api_token = self.query_one("#cf-token-input", Input).value.strip()
        if getattr(self.app, "require_sudo_password", False):
            self.app.state.sudo_password = self.query_one("#sudo-password-input", Input).value
        # M5.4: persist cluster-replicate checkbox если он есть на экране
        try:
            cb = self.query_one("#cluster-replicate-checkbox", Checkbox)
            self.app.state.cluster_replicate = bool(cb.value)
        except Exception:
            pass
        # Validate before advancing
        try:
            self.app.state.domain = validate_domain(raw_domain)
        except ValueError as exc:
            self.app.notify(f"Domain invalid: {exc}", severity="error")
            return
        if self.app.state.cf_api_token and len(self.app.state.cf_api_token) < 20:
            self.app.notify("CF token < 20 chars", severity="error")
            return
        if getattr(self.app, "require_sudo_password", False) and not self.app.state.sudo_password:
            self.app.notify("Sudo password нужен для bootstrap step", severity="error")
            return
        self.app.push_screen(ModelScreen())


# ---- ModelScreen (2/4) ----


class ModelScreen(Screen[None]):
    """Step 2: model selection + context settings.

    Phase M5.1: split на 3 sections (LLM / Embed / Rerank). User видит
    отдельный selector + custom HF fields + context для каждого role.
    Раньше один dropdown миксовал все kinds — embed модели вылезали в LLM
    list.
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("alt+b", "back", "Back"),
        Binding("alt+n", "next_step", "Next"),
        Binding("f1", "help", "Help"),
    ]

    def compose(self) -> ComposeResult:
        from textual.widgets import Rule

        from agmind.cli.tui.setup_wizard import MODEL_CUSTOM_OPTION, MODEL_SKIP_OPTION
        from agmind.install.models import (
            CTX_SIZE_PRESETS,
            KV_CACHE_TYPES,
            PARALLEL_PRESETS,
            THREADS_PRESETS,
            models_for_wizard,
        )

        yield Header(show_clock=False)
        yield StepHeader(2, 4, t("wizard.section.model"))
        state = self.app.state
        ctx_options = [(label, str(n)) for n, label in CTX_SIZE_PRESETS]
        kv_options = [(label, val) for val, label in KV_CACHE_TYPES]
        parallel_options = [(label, str(n)) for n, label in PARALLEL_PRESETS]
        threads_options = [(label, str(n)) for n, label in THREADS_PRESETS]
        with VerticalScroll():
            # ---- LLM section ----
            yield Label("<LLM> TOKEN GENERATION", classes="model-section-header")
            llm_options = models_for_wizard(kind="llm")
            llm_options.append(MODEL_SKIP_OPTION)
            llm_options.append(MODEL_CUSTOM_OPTION)
            yield Select(
                llm_options,
                id="llm-model-select",
                value=_legal_select_value(state.model_id, llm_options),
                allow_blank=False,
            )
            yield Static(t("wizard.section.custom_hf_hint"), classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="llm-model-repo-input",
                value=state.model_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="llm-model-file-input",
                value=state.model_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options),
                id="llm-ctx-size-select",
                value=_legal_select_value(str(state.ctx_size), ctx_options),
                allow_blank=False,
            )
            yield Label(t("wizard.section.kv_cache"), classes="section")
            yield Select(
                list(kv_options),
                id="llm-kv-cache-select",
                value=_legal_select_value(state.kv_cache_type, kv_options),
                allow_blank=False,
            )
            yield Label(t("wizard.section.threads"), classes="section")
            yield Select(
                threads_options,
                id="llm-threads-select",
                value=_legal_select_value(str(state.threads), threads_options),
                allow_blank=False,
            )
            yield Label(t("wizard.section.parallel"), classes="section")
            yield Select(
                list(parallel_options),
                id="llm-parallel-select",
                value=_legal_select_value(str(state.parallel_slots), parallel_options),
                allow_blank=False,
            )

            # ---- Embed section ----
            yield Rule(line_style="heavy")
            yield Label("<EMBED> DENSE EMBEDDINGS (RAG)", classes="model-section-header")
            embed_options = models_for_wizard(kind="embed")
            embed_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                embed_options,
                id="embed-model-select",
                value=_legal_select_value(state.embed_model_id, embed_options),
                allow_blank=False,
            )
            yield Static(t("wizard.section.custom_hf_hint"), classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="embed-repo-input",
                value=state.embed_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="embed-file-input",
                value=state.embed_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options),
                id="embed-ctx-size-select",
                value=_legal_select_value(str(state.embed_ctx_size), ctx_options),
                allow_blank=False,
            )
            yield Label(t("wizard.section.kv_cache"), classes="section")
            yield Select(
                list(kv_options),
                id="embed-kv-cache-select",
                value=_legal_select_value(state.embed_kv_cache, kv_options),
                allow_blank=False,
            )
            yield Label(t("wizard.section.parallel"), classes="section")
            yield Select(
                list(parallel_options),
                id="embed-parallel-select",
                value=_legal_select_value(str(state.embed_parallel), parallel_options),
                allow_blank=False,
            )

            # ---- Rerank section ----
            yield Rule(line_style="heavy")
            yield Label("<RERANK> CROSS-ENCODER (RAG ORDERING)", classes="model-section-header")
            rerank_options = models_for_wizard(kind="rerank")
            rerank_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                rerank_options,
                id="rerank-model-select",
                value=_legal_select_value(state.rerank_model_id, rerank_options),
                allow_blank=False,
            )
            yield Static(
                "[dim]Empty filename = skip rerank service (RAG будет без re-ordering)[/dim]",
                classes="hint",
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="rerank-repo-input",
                value=state.rerank_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="rerank-file-input",
                value=state.rerank_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options),
                id="rerank-ctx-size-select",
                value=_legal_select_value(str(state.rerank_ctx_size), ctx_options),
                allow_blank=False,
            )

        with Horizontal(id="nav-row"):
            yield Button(t("wizard.btn.back"), id="back-btn", variant="default")
            yield Button(t("wizard.btn.next"), id="next-btn", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "next-btn":
            self._save_and_advance()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_next_step(self) -> None:
        self._save_and_advance()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def _read_int(self, widget_id: str, default: int) -> int:
        try:
            value = self.query_one(f"#{widget_id}", Select).value
            return int(str(value))
        except (ValueError, TypeError):
            return default

    def _read_str(self, widget_id: str, default: str) -> str:
        value = self.query_one(f"#{widget_id}", Select).value
        return str(value) if value is not None else default

    def _save_and_advance(self) -> None:
        from agmind.cli.tui.setup_wizard import MODEL_CUSTOM_ID, MODEL_SKIP_ID
        from agmind.install.models import find_by_id

        state = self.app.state

        # ---- LLM ----
        state.model_id = self._read_str("llm-model-select", state.model_id)
        state.model_repo = self.query_one("#llm-model-repo-input", Input).value.strip()
        state.model_file = self.query_one("#llm-model-file-input", Input).value.strip()
        if state.model_id == MODEL_SKIP_ID:
            state.model_repo = ""
            state.model_file = ""
        elif state.model_id != MODEL_CUSTOM_ID:
            entry = find_by_id(state.model_id)
            if entry is not None:
                state.model_repo, state.model_file = entry.repo, entry.file
        state.ctx_size = self._read_int("llm-ctx-size-select", state.ctx_size)
        state.kv_cache_type = self._read_str("llm-kv-cache-select", state.kv_cache_type)
        state.threads = self._read_int("llm-threads-select", state.threads)
        state.parallel_slots = self._read_int("llm-parallel-select", state.parallel_slots)

        # ---- Embed ----
        state.embed_model_id = self._read_str("embed-model-select", state.embed_model_id)
        state.embed_repo = self.query_one("#embed-repo-input", Input).value.strip()
        state.embed_file = self.query_one("#embed-file-input", Input).value.strip()
        if state.embed_model_id != "custom":
            entry = find_by_id(state.embed_model_id)
            if entry is not None:
                state.embed_repo, state.embed_file = entry.repo, entry.file
        state.embed_ctx_size = self._read_int("embed-ctx-size-select", state.embed_ctx_size)
        state.embed_kv_cache = self._read_str("embed-kv-cache-select", state.embed_kv_cache)
        state.embed_parallel = self._read_int("embed-parallel-select", state.embed_parallel)

        # ---- Rerank ----
        state.rerank_model_id = self._read_str("rerank-model-select", state.rerank_model_id)
        state.rerank_repo = self.query_one("#rerank-repo-input", Input).value.strip()
        state.rerank_file = self.query_one("#rerank-file-input", Input).value.strip()
        if state.rerank_model_id != "custom":
            entry = find_by_id(state.rerank_model_id)
            if entry is not None:
                state.rerank_repo, state.rerank_file = entry.repo, entry.file
        state.rerank_ctx_size = self._read_int("rerank-ctx-size-select", state.rerank_ctx_size)
        state.normalize_model_fields_and_services()

        self.app.push_screen(ServicesScreen())


# ---- ServicesScreen (3/4) ----


class ServicesScreen(Screen[None]):
    """Step 3: service selection split into installer-facing departments."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("alt+b", "back", "Back"),
        Binding("alt+n", "next_step", "Next"),
        Binding("f1", "help", "Help"),
    ]

    def __init__(self, department_index: int = 0) -> None:
        super().__init__()
        self.department_index = department_index
        self._syncing_service_selection = False

    @property
    def current_department_key(self) -> str:
        departments = self._departments()
        if not departments:
            return ""
        index = max(0, min(self.department_index, len(departments) - 1))
        return departments[index][0]

    def compose(self) -> ComposeResult:
        from agmind.cli.tui.setup_wizard import (
            _SERVICE_DEPARTMENT_HINTS,
            _SERVICE_DEPARTMENT_LABELS,
            _TIER_LABELS,
            AGCheckbox,
        )

        yield Header(show_clock=False)
        departments = self._departments()
        total = sum(len(services) for _, services in departments)
        current_key, current_services = departments[self.department_index]
        department_label = _SERVICE_DEPARTMENT_LABELS.get(
            current_key,
            _TIER_LABELS.get(current_key, current_key),
        )
        yield StepHeader(
            3,
            4,
            t("wizard.section.services", default="Services").format(total=total)
            + f" {self.department_index + 1}/{len(departments)}",
        )
        # M5.3.4: empty-state banner shown ONLY когда 0 selected (initial reactive)
        selected_count = len(self.app.state.services)
        yield Static(
            "[ NO SERVICES SELECTED — PRESS SPACE TO CHECK ]"
            if selected_count == 0
            else f"[dim]{selected_count} of {total} services selected[/dim]",
            id="services-empty-banner",
            classes="empty-banner" if selected_count == 0 else "hint",
        )
        with VerticalScroll(id="service-checkboxes"):
            with Container(classes="tier-group"):
                yield Label(
                    f"{department_label}  ·  {len(current_services)} services",
                    classes="tier-section",
                )
                hint = _SERVICE_DEPARTMENT_HINTS.get(current_key)
                if hint:
                    yield Static(f"[dim]{hint}[/dim]", classes="hint")
                for name, _purpose in current_services:
                    yield AGCheckbox(
                        name,
                        id=f"svc-{name.replace('-', '_')}",
                        value=(name in self.app.state.services),
                    )
        with Horizontal(id="nav-row"):
            yield Button(t("wizard.btn.back"), id="back-btn", variant="default")
            yield Button(t("wizard.btn.next"), id="next-btn", variant="primary")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """M5.3.4: re-render banner когда меняется selection count."""
        if not str(event.checkbox.id or "").startswith("svc-"):
            return
        if not self._syncing_service_selection:
            self._sync_state_from_visible_checkboxes()
            service_name = self._service_name_for_checkbox(event.checkbox.id)
            if event.value and service_name in DIFY_VECTOR_PROVIDERS:
                self._uncheck_other_vector_providers(service_name)
            self._sync_expanded_service_selection()
        self._update_selection_banner()

    def _departments(self) -> list[tuple[str, list[tuple[str, str]]]]:
        return list(self.app.services_by_tier.items())

    def _current_department_services(self) -> list[tuple[str, str]]:
        departments = self._departments()
        if not departments:
            return []
        index = max(0, min(self.department_index, len(departments) - 1))
        return departments[index][1]

    def _visible_service_names(self) -> list[str]:
        return [name for name, _ in self._current_department_services()]

    def _update_selection_banner(self) -> None:
        selected = self._selected_service_count()
        try:
            banner = self.query_one("#services-empty-banner", Static)
        except Exception:
            return
        total = sum(len(services) for _, services in self._departments())
        if selected == 0:
            banner.update("[ NO SERVICES SELECTED — PRESS SPACE TO CHECK ]")
            banner.set_classes("empty-banner")
        else:
            banner.update(f"[dim]{selected} of {total} services selected[/dim]")
            banner.set_classes("hint")

    def _selected_service_count(self) -> int:
        return len(set(self.app.state.services))

    def _checked_service_names(self) -> list[str]:
        self._sync_state_from_visible_checkboxes()
        return sorted(set(self.app.state.services))

    def _sync_state_from_visible_checkboxes(self) -> None:
        selected = set(self.app.state.services)
        for name in self._visible_service_names():
            checkbox = self.query_one(f"#svc-{name.replace('-', '_')}", Checkbox)
            if checkbox.value:
                selected.add(name)
            else:
                selected.discard(name)
        self.app.state.services = sorted(selected)

    def _service_name_for_checkbox(self, widget_id: object) -> str | None:
        raw_id = str(widget_id or "")
        if not raw_id.startswith("svc-"):
            return None
        normalized = raw_id.removeprefix("svc-")
        for tier_services in self.app.services_by_tier.values():
            for name, _ in tier_services:
                if name.replace("-", "_") == normalized:
                    return str(name)
        return None

    def _uncheck_other_vector_providers(self, selected_provider: str) -> None:
        selected = set(self.app.state.services)
        self._syncing_service_selection = True
        try:
            for provider in DIFY_VECTOR_PROVIDERS:
                if provider == selected_provider:
                    continue
                selected.discard(provider)
                try:
                    checkbox = self.query_one(f"#svc-{provider.replace('-', '_')}", Checkbox)
                except Exception:
                    continue
                checkbox.value = False
        finally:
            self._syncing_service_selection = False
        self.app.state.services = sorted(selected)

    def _sync_expanded_service_selection(self) -> None:
        from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

        expanded = set(expand_selected_services_for_setup(self._checked_service_names()))
        self.app.state.services = sorted(expanded)
        self._syncing_service_selection = True
        try:
            for name in self._visible_service_names():
                checkbox = self.query_one(f"#svc-{name.replace('-', '_')}", Checkbox)
                checkbox.value = name in expanded
        finally:
            self._syncing_service_selection = False

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "next-btn":
            self._save_and_advance()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_next_step(self) -> None:
        self._save_and_advance()

    def _save_and_advance(self) -> None:
        from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

        services = expand_selected_services_for_setup(self._checked_service_names())
        self.app.state.services = services
        self.app.state.normalize_model_fields_and_services()
        departments = self._departments()
        if self.department_index < len(departments) - 1:
            self.app.push_screen(ServicesScreen(self.department_index + 1))
            return
        self.app.state.normalize_model_fields_and_services(drop_unselected_model_files=True)
        if not self.app.state.services:
            self.app.notify("Выбери хотя бы один service", severity="error")
            return
        self.app.push_screen(ConfirmScreen())


# ---- ConfirmScreen (4/4) ----


class ConfirmScreen(Screen[None]):
    """Step 4: summary + Apply / Back."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("alt+b", "back", "Back"),
        Binding("ctrl+s", "apply", "Apply"),
        Binding("f1", "help", "Help"),
    ]

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def compose(self) -> ComposeResult:
        state = self.app.state
        yield Header(show_clock=False)
        yield StepHeader(4, 4, t("wizard.confirm.title", default="Confirm + Apply"))
        with VerticalScroll():
            yield Static(self._summary(state), id="summary-text")
        with Horizontal(id="nav-row"):
            yield Button(t("wizard.btn.back"), id="back-btn", variant="default")
            yield Button(t("wizard.btn.apply"), id="apply-btn", variant="success")
        yield Footer()

    def _summary(self, state) -> str:  # type: ignore[no-untyped-def]
        # Phase M4.7 + M5.1: Fallout pip-boy STATUS REPORT — separate LLM/Embed/Rerank
        from agmind.services.deployment_topology import (
            build_deployment_topology_report_for_services,
        )

        line = "─" * 60
        topology = build_deployment_topology_report_for_services(state.services)
        topology_lines = topology.block_lines()
        topology_block = ""
        if topology_lines:
            topology_block = "\n".join(f"  {item}" for item in topology_lines) + "\n"
        llm_block = (
            (
                f"  LLM ................ {state.model_id}\n"
                f"      REPO/FILE ...... {state.model_repo}/{state.model_file}\n"
                f"      CTX SIZE ....... {state.ctx_size}\n"
                f"      KV CACHE ....... {state.kv_cache_type}\n"
                f"      CPU THREADS .... {state.threads}\n"
                f"      PARALLEL SLOTS . {state.parallel_slots}\n"
            )
            if state.model_file
            else "  LLM ................ [dim]skipped (no model)[/dim]\n"
        )
        rerank_block = (
            (
                f"  RERANK ............. {state.rerank_model_id}\n"
                f"      REPO/FILE ...... {state.rerank_repo}/{state.rerank_file}\n"
                f"      CTX SIZE ....... {state.rerank_ctx_size}\n"
            )
            if state.rerank_file
            else "  RERANK ............. [dim]skipped (no model)[/dim]\n"
        )
        return (
            f"[bold]── DEPLOYMENT STATUS REPORT ──[/bold]\n"
            f"{line}\n"
            f"  DOMAIN ............. {state.domain}\n"
            f"  CF API TOKEN ....... {'*' * 8} ({len(state.cf_api_token)} CHARS)\n"
            f"  BACKEND ............ {state.backend}\n"
            f"{line}\n"
            f"{llm_block}"
            f"{line}\n"
            f"  EMBED .............. {state.embed_model_id}\n"
            f"      REPO/FILE ...... {state.embed_repo}/{state.embed_file}\n"
            f"      CTX SIZE ....... {state.embed_ctx_size}\n"
            f"      KV CACHE ....... {state.embed_kv_cache}\n"
            f"      PARALLEL ....... {state.embed_parallel}\n"
            f"{line}\n"
            f"{rerank_block}"
            f"{line}\n"
            f"{topology_block}"
            f"{line}\n"
            f"  SERVICES SELECTED .. {len(state.services)}\n"
            f"  {', '.join(sorted(state.services))}\n"
            f"{line}\n"
            f"\n  PRESS [bold]< APPLY >[/bold] TO DEPLOY · PRESS [bold]< BACK >[/bold] TO REVIEW\n"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "apply-btn":
            self.action_apply()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_apply(self) -> None:
        # Hand off to action_submit (it writes the cluster inventory when replicate is
        # enabled, saves state, and starts the install). Pop confirm first so action_submit
        # sees the "root" app. NB: the cluster-inventory write lives in action_submit, not
        # here, so the Ctrl+S priority-binding path (which calls action_submit directly,
        # bypassing this method) also generates it.
        self.app.pop_screen()
        self.app.action_submit()


# ---- HelpScreen (F1 overlay) ----


class HelpScreen(Screen[None]):
    """M5.3.7: F1 keymap overlay — modal screen с full shortcut list."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "close", "Close"),
        Binding("f1", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    HELP_TEXT = (
        "[bold]── AGMIND SETUP — KEY BINDINGS ──[/bold]\n\n"
        "  [bold]Navigation[/bold]\n"
        "    Alt+N           Next step\n"
        "    Alt+B           Back to previous step\n"
        "    Tab / Shift+Tab Move focus between fields\n"
        "    F1              Show this help\n"
        "    Esc / Q         Close help / Quit wizard\n\n"
        "  [bold]Apply[/bold]\n"
        "    Ctrl+S          Submit (apply on Confirm screen)\n\n"
        "  [bold]Services screen[/bold]\n"
        "    Space           Toggle service checkbox\n"
        "    Arrows          Move between checkboxes\n\n"
        "  [bold]Environment variables[/bold]\n"
        "    AGMIND_WIZARD_LEGACY=1   force single-screen wizard\n"
        "    AGMIND_LANG=ru/en        UI language\n\n"
        "  PRESS [bold]< ESC >[/bold] OR [bold]< F1 >[/bold] TO RETURN"
    )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            yield Static(self.HELP_TEXT, id="help-content")
        with Horizontal(id="nav-row"):
            yield Button("Close (Esc)", id="close-btn", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.app.pop_screen()

    def action_close(self) -> None:
        self.app.pop_screen()


__all__ = [
    "ConfirmScreen",
    "DomainScreen",
    "HelpScreen",
    "ModelScreen",
    "ServicesScreen",
    "StepHeader",
]
