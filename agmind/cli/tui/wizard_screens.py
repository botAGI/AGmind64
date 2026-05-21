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
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
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

from agmind.i18n import t


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
    """Step 1: domain + CF token."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("tab", "focus_next", "Next field"),
        Binding("alt+n", "next_step", "Next"),
    ]

    def compose(self) -> ComposeResult:
        from agmind.cli.tui.setup_wizard import (
            DomainValidator,
            TokenLengthValidator,
        )
        yield Header(show_clock=False)
        yield StepHeader(1, 4, t("wizard.section.domain"))
        with VerticalScroll():
            yield Label(t("wizard.section.domain"), classes="section")
            yield Input(
                placeholder=t("wizard.placeholder.domain"),
                id="domain-input",
                value=self.app.state.domain,
                validators=[DomainValidator()],
            )
            yield Label(t("wizard.section.cf_token"), classes="section")
            yield Input(
                placeholder=t("wizard.placeholder.cf_token"),
                id="cf-token-input",
                value=self.app.state.cf_api_token,
                password=True,
                validators=[TokenLengthValidator()],
            )
        with Horizontal(id="nav-row"):
            yield Button(t("wizard.btn.quit"), id="back-btn", variant="default")
            yield Button(t("wizard.btn.next"), id="next-btn", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.exit(None)
        elif event.button.id == "next-btn":
            self._save_and_advance()

    def action_next_step(self) -> None:
        self._save_and_advance()

    def _save_and_advance(self) -> None:
        self.app.state.domain = self.query_one("#domain-input", Input).value.strip()
        self.app.state.cf_api_token = self.query_one("#cf-token-input", Input).value.strip()
        # Validate before advancing
        if not self.app.state.domain or "." not in self.app.state.domain:
            self.app.notify("Domain должен содержать '.'", severity="error")
            return
        if self.app.state.cf_api_token and len(self.app.state.cf_api_token) < 20:
            self.app.notify("CF token < 20 chars", severity="error")
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
    ]

    def compose(self) -> ComposeResult:
        from agmind.install.models import (
            CTX_SIZE_PRESETS,
            KV_CACHE_TYPES,
            PARALLEL_PRESETS,
            THREADS_PRESETS,
            models_for_wizard,
        )
        from textual.widgets import Rule
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
            llm_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                llm_options, id="llm-model-select",
                value=state.model_id, allow_blank=False,
            )
            yield Static(t("wizard.section.custom_hf_hint"), classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="llm-model-repo-input", value=state.model_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="llm-model-file-input", value=state.model_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options), id="llm-ctx-size-select",
                value=str(state.ctx_size), allow_blank=False,
            )
            yield Label(t("wizard.section.kv_cache"), classes="section")
            yield Select(
                list(kv_options), id="llm-kv-cache-select",
                value=state.kv_cache_type, allow_blank=False,
            )
            yield Label(t("wizard.section.threads"), classes="section")
            yield Select(
                threads_options, id="llm-threads-select",
                value=str(state.threads), allow_blank=False,
            )
            yield Label(t("wizard.section.parallel"), classes="section")
            yield Select(
                list(parallel_options), id="llm-parallel-select",
                value=str(state.parallel_slots), allow_blank=False,
            )

            # ---- Embed section ----
            yield Rule(line_style="heavy")
            yield Label("<EMBED> DENSE EMBEDDINGS (RAG)", classes="model-section-header")
            embed_options = models_for_wizard(kind="embed")
            embed_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                embed_options, id="embed-model-select",
                value=state.embed_model_id, allow_blank=False,
            )
            yield Static(t("wizard.section.custom_hf_hint"), classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="embed-repo-input", value=state.embed_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="embed-file-input", value=state.embed_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options), id="embed-ctx-size-select",
                value=str(state.embed_ctx_size), allow_blank=False,
            )
            yield Label(t("wizard.section.kv_cache"), classes="section")
            yield Select(
                list(kv_options), id="embed-kv-cache-select",
                value=state.embed_kv_cache, allow_blank=False,
            )
            yield Label(t("wizard.section.parallel"), classes="section")
            yield Select(
                list(parallel_options), id="embed-parallel-select",
                value=str(state.embed_parallel), allow_blank=False,
            )

            # ---- Rerank section ----
            yield Rule(line_style="heavy")
            yield Label("<RERANK> CROSS-ENCODER (RAG ORDERING)", classes="model-section-header")
            rerank_options = models_for_wizard(kind="rerank")
            rerank_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                rerank_options, id="rerank-model-select",
                value=state.rerank_model_id, allow_blank=False,
            )
            yield Static(
                "[dim]Empty filename = skip rerank service (RAG будет без re-ordering)[/dim]",
                classes="hint",
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="rerank-repo-input", value=state.rerank_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="rerank-file-input", value=state.rerank_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                list(ctx_options), id="rerank-ctx-size-select",
                value=str(state.rerank_ctx_size), allow_blank=False,
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
        from agmind.install.models import find_by_id
        state = self.app.state

        # ---- LLM ----
        state.model_id = self._read_str("llm-model-select", state.model_id)
        state.model_repo = self.query_one("#llm-model-repo-input", Input).value.strip()
        state.model_file = self.query_one("#llm-model-file-input", Input).value.strip()
        if state.model_id != "custom":
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

        self.app.push_screen(ServicesScreen())


# ---- ServicesScreen (3/4) ----


class ServicesScreen(Screen[None]):
    """Step 3: per-tier service selection."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("alt+b", "back", "Back"),
        Binding("alt+n", "next_step", "Next"),
    ]

    def compose(self) -> ComposeResult:
        from agmind.cli.tui.setup_wizard import (
            AGCheckbox,
            _TIER_LABELS,
        )
        yield Header(show_clock=False)
        services_by_tier = self.app.services_by_tier
        total = sum(len(svcs) for svcs in services_by_tier.values())
        yield StepHeader(3, 4, t("wizard.section.services", default=f"Services ({total} available)").format(total=total))
        with VerticalScroll(id="service-checkboxes"):
            for tier, services in services_by_tier.items():
                tier_label = _TIER_LABELS.get(tier, tier)
                with Container(classes="tier-group"):
                    yield Label(
                        f"{tier_label}  ·  {len(services)} services",
                        classes="tier-section",
                    )
                    for name, _purpose in services:
                        yield AGCheckbox(
                            name,
                            id=f"svc-{name.replace('-', '_')}",
                            value=(name in self.app.state.services),
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

    def _save_and_advance(self) -> None:
        services: list[str] = []
        for tier_services in self.app.services_by_tier.values():
            for name, _ in tier_services:
                cb = self.query_one(f"#svc-{name.replace('-', '_')}", Checkbox)
                if cb.value:
                    services.append(name)
        self.app.state.services = services
        if not services:
            self.app.notify("Выбери хотя бы один service", severity="error")
            return
        self.app.push_screen(ConfirmScreen())


# ---- ConfirmScreen (4/4) ----


class ConfirmScreen(Screen[None]):
    """Step 4: summary + Apply / Back."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("alt+b", "back", "Back"),
        Binding("ctrl+s", "apply", "Apply"),
    ]

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
        line = "─" * 60
        rerank_block = (
            f"  RERANK ............. {state.rerank_model_id}\n"
            f"      REPO/FILE ...... {state.rerank_repo}/{state.rerank_file}\n"
            f"      CTX SIZE ....... {state.rerank_ctx_size}\n"
        ) if state.rerank_file else "  RERANK ............. [dim]skipped (no model)[/dim]\n"
        return (
            f"[bold]── DEPLOYMENT STATUS REPORT ──[/bold]\n"
            f"{line}\n"
            f"  DOMAIN ............. {state.domain}\n"
            f"  CF API TOKEN ....... {'*' * 8} ({len(state.cf_api_token)} CHARS)\n"
            f"  BACKEND ............ {state.backend}\n"
            f"{line}\n"
            f"  LLM ................ {state.model_id}\n"
            f"      REPO/FILE ...... {state.model_repo}/{state.model_file}\n"
            f"      CTX SIZE ....... {state.ctx_size}\n"
            f"      KV CACHE ....... {state.kv_cache_type}\n"
            f"      CPU THREADS .... {state.threads}\n"
            f"      PARALLEL SLOTS . {state.parallel_slots}\n"
            f"{line}\n"
            f"  EMBED .............. {state.embed_model_id}\n"
            f"      REPO/FILE ...... {state.embed_repo}/{state.embed_file}\n"
            f"      CTX SIZE ....... {state.embed_ctx_size}\n"
            f"      KV CACHE ....... {state.embed_kv_cache}\n"
            f"      PARALLEL ....... {state.embed_parallel}\n"
            f"{line}\n"
            f"{rerank_block}"
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
        # Hand off to original action_submit (saves state + push install).
        # Pop confirm screen first чтобы action_submit видел "root" app.
        self.app.pop_screen()
        self.app.action_submit()


__all__ = [
    "ConfirmScreen",
    "DomainScreen",
    "ModelScreen",
    "ServicesScreen",
    "StepHeader",
]
