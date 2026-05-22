"""Phase J: AnimatedLogo widget — крутой цветной анимированный логотип.

Использует pyfiglet для ASCII art + Rich для gradient colors + Textual reactive
intervals для smooth animation (gradient cycling каждые 100ms).

Палитра — Strix Halo themed: AMD red → magenta → deep purple → cyan
(намёк на Vulkan + ROCm spectrum).
"""

from __future__ import annotations

# Tech/cyber palette — AMD red core + cyan highlights (Vulkan vibes).
# Monochromatic-ish, не rainbow. Brand-appropriate для AMD Strix Halo.
# Можно override через AGMIND_LOGO_THEME env var (red|cyan|matrix|amd).
import os as _os

import pyfiglet
from rich.console import Console
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

_PALETTES: dict[str, tuple[str, ...]] = {
    # Default: AMD branded red-orange-white burst
    "amd": (
        "#FFFFFF",  # bright highlight
        "#FFD700",  # gold accent
        "#FF6600",  # AMD orange
        "#FF0033",  # AMD red
        "#CC0022",
        "#8B0000",  # dark red base
        "#CC0022",
        "#FF0033",
    ),
    # Pure AMD red sweep
    "red": (
        "#FF0033",
        "#E50028",
        "#CC001F",
        "#B30019",
        "#990013",
        "#B30019",
        "#CC001F",
        "#E50028",
    ),
    # Cyber/Vulkan cyan
    "cyan": (
        "#00FFE5",
        "#00E5FF",
        "#00C8FF",
        "#00ABFF",
        "#0080FF",
        "#00ABFF",
        "#00C8FF",
        "#00E5FF",
    ),
    # Matrix green (для шуточек)
    "matrix": (
        "#00FF41",
        "#00CC33",
        "#009922",
        "#006611",
        "#003300",
        "#006611",
        "#009922",
        "#00CC33",
    ),
}

GRADIENT_COLORS: tuple[str, ...] = _PALETTES.get(
    _os.environ.get("AGMIND_LOGO_THEME", "amd"),
    _PALETTES["amd"],
)


def _render_gradient(text: str, color_offset: int) -> Text:
    """Render multi-line text with diagonal gradient.

    color_offset shifts palette → animation through reactive interval tick.
    """
    rich_text = Text()
    lines = text.split("\n")
    n_colors = len(GRADIENT_COLORS)
    for row_idx, line in enumerate(lines):
        # Each character gets color based on (row + col + offset) mod len(colors)
        for col_idx, ch in enumerate(line):
            if ch in (" ", "\t"):
                rich_text.append(ch)
                continue
            color = GRADIENT_COLORS[(row_idx + col_idx + color_offset) % n_colors]
            rich_text.append(ch, style=f"bold {color}")
        rich_text.append("\n")
    return rich_text


class AnimatedLogo(Widget):
    """Cycling-gradient ASCII logo.

    Renders "AGMINDx86" (pyfiglet `slant` font) с smooth color rotation —
    каждый character меняет цвет через короткий interval.
    """

    DEFAULT_CSS = """
    AnimatedLogo {
        height: auto;
        width: auto;
        padding: 0 2;
        content-align: center top;
    }
    """

    # Reactive int — увеличивается каждые 100ms через self.set_interval.
    color_offset: reactive[int] = reactive(0)

    def __init__(
        self,
        text: str = "AGMIND",
        subtitle: str = "x86 Strix Halo",
        font: str = "slant",
        speed: float = 0.10,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        try:
            self.ascii_art = pyfiglet.figlet_format(text, font=font, width=80)
        except pyfiglet.FontNotFound:
            # Fallback на default font
            self.ascii_art = pyfiglet.figlet_format(text, width=80)
        self.subtitle = subtitle
        self.speed = speed

    _timer: object | None = None

    def on_mount(self) -> None:
        # tick offset каждые `speed` секунд → gradient крутится.
        # AGMIND_LOGO_DISABLE_ANIMATION=1 → отключить таймер (для headless
        # Textual Pilot tests, где reactive interval вешает event loop).
        if _os.environ.get("AGMIND_LOGO_DISABLE_ANIMATION"):
            return
        self._timer = self.set_interval(self.speed, self._tick)

    def on_unmount(self) -> None:
        # Stop animation timer when widget removed (важно для pytest Pilot)
        if self._timer is not None:
            try:
                self._timer.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._timer = None

    def _tick(self) -> None:
        self.color_offset += 1

    def render(self) -> Text:
        rendered = _render_gradient(self.ascii_art.rstrip("\n"), self.color_offset)
        if self.subtitle:
            rendered.append("\n")
            # Subtitle — static technical look (dim white) — без animation
            rendered.append(
                self.subtitle.center(50),
                style="dim italic #888888",
            )
        return rendered


def print_static_logo(text: str = "AGMIND", subtitle: str = "x86 Strix Halo") -> None:
    """Print logo to terminal (non-TUI mode, для startup splash перед wizard launch)."""
    console = Console()
    try:
        ascii_art = pyfiglet.figlet_format(text, font="slant", width=80)
    except pyfiglet.FontNotFound:
        ascii_art = pyfiglet.figlet_format(text, width=80)

    rich_text = _render_gradient(ascii_art.rstrip("\n"), color_offset=0)
    if subtitle:
        rich_text.append("\n")
        rich_text.append(
            subtitle.center(50),
            style="dim italic #888888",
        )
    console.print(rich_text)
