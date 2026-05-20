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
    """Factory для 'Step N of M' header — returns plain Static widget."""
    dots = "● " * step_n + "○ " * (total - step_n)
    text = f"[bold]Step {step_n} of {total} · {title}[/bold]\n{dots.strip()}"
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
    """Step 2: model selection + context settings."""

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
        yield Header(show_clock=False)
        yield StepHeader(2, 4, t("wizard.section.model"))
        with VerticalScroll():
            yield Label(t("wizard.section.model"), classes="section")
            model_options = models_for_wizard()
            model_options.append(("Custom HuggingFace…", "custom"))
            yield Select(
                model_options, id="model-select",
                value=self.app.state.model_id, allow_blank=False,
            )
            yield Static(t("wizard.section.custom_hf_hint"), classes="hint")
            yield Input(
                placeholder=t("wizard.placeholder.model_repo"),
                id="model-repo-input", value=self.app.state.model_repo,
            )
            yield Input(
                placeholder=t("wizard.placeholder.model_file"),
                id="model-file-input", value=self.app.state.model_file,
            )
            yield Label(t("wizard.section.ctx_size"), classes="section")
            yield Select(
                [(label, str(n)) for n, label in CTX_SIZE_PRESETS],
                id="ctx-size-select", value=str(self.app.state.ctx_size), allow_blank=False,
            )
            yield Label(t("wizard.section.kv_cache"), classes="section")
            yield Select(
                [(label, val) for val, label in KV_CACHE_TYPES],
                id="kv-cache-select", value=self.app.state.kv_cache_type, allow_blank=False,
            )
            yield Label(t("wizard.section.threads"), classes="section")
            yield Select(
                [(label, str(n)) for n, label in THREADS_PRESETS],
                id="threads-select", value=str(self.app.state.threads), allow_blank=False,
            )
            yield Label(t("wizard.section.parallel"), classes="section")
            yield Select(
                [(label, str(n)) for n, label in PARALLEL_PRESETS],
                id="parallel-select", value=str(self.app.state.parallel_slots), allow_blank=False,
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
        state = self.app.state
        model_select = self.query_one("#model-select", Select)
        state.model_id = str(model_select.value) if model_select.value is not None else state.model_id
        state.model_repo = self.query_one("#model-repo-input", Input).value.strip()
        state.model_file = self.query_one("#model-file-input", Input).value.strip()
        if state.model_id != "custom":
            from agmind.install.models import find_by_id
            entry = find_by_id(state.model_id)
            if entry is not None:
                state.model_repo = entry.repo
                state.model_file = entry.file

        ctx_select = self.query_one("#ctx-size-select", Select)
        try:
            state.ctx_size = int(str(ctx_select.value))
        except (ValueError, TypeError):
            pass

        kv = self.query_one("#kv-cache-select", Select)
        if kv.value is not None:
            state.kv_cache_type = str(kv.value)

        threads = self.query_one("#threads-select", Select)
        try:
            state.threads = int(str(threads.value))
        except (ValueError, TypeError):
            pass

        parallel = self.query_one("#parallel-select", Select)
        try:
            state.parallel_slots = int(str(parallel.value))
        except (ValueError, TypeError):
            pass

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
        return (
            f"[bold]Domain:[/bold]      {state.domain}\n"
            f"[bold]CF token:[/bold]    {'*' * 8} ({len(state.cf_api_token)} chars)\n"
            f"[bold]Backend:[/bold]     {state.backend}\n"
            f"[bold]Model:[/bold]       {state.model_id} → {state.model_repo}/{state.model_file}\n"
            f"[bold]Context:[/bold]     {state.ctx_size}\n"
            f"[bold]KV cache:[/bold]    {state.kv_cache_type}\n"
            f"[bold]Threads:[/bold]     {state.threads}\n"
            f"[bold]Parallel:[/bold]    {state.parallel_slots}\n"
            f"[bold]Services:[/bold]    {len(state.services)} selected\n"
            f"  {', '.join(sorted(state.services))}\n"
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
