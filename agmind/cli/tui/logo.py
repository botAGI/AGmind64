"""Phase J: AnimatedLogo widget — крутой цветной анимированный логотип.

Использует pyfiglet для ASCII art + Rich для gradient colors + Textual reactive
intervals для smooth animation (gradient cycling каждые 100ms).

Палитра — Strix Halo themed: AMD red → magenta → deep purple → cyan
(намёк на Vulkan + ROCm spectrum).
"""

from __future__ import annotations

import pyfiglet
from rich.console import Console
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# Smooth gradient stops через HSL-like color rotation.
# Bright AMD-themed palette: red → orange → magenta → purple → cyan → green-amd.
GRADIENT_COLORS: tuple[str, ...] = (
    "#FF0033",  # AMD red
    "#FF3D00",  # vivid orange
    "#FF1493",  # deep pink
    "#9D00FF",  # electric purple
    "#5B00FF",  # indigo
    "#00B7FF",  # cyan (Vulkan vibes)
    "#00E5C0",  # teal
    "#7CFC00",  # AMD green
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
    offset: reactive[int] = reactive(0)

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
        # tick offset каждые `speed` секунд → gradient крутится
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
        self.offset += 1

    def render(self) -> Text:
        rendered = _render_gradient(self.ascii_art.rstrip("\n"), self.offset)
        if self.subtitle:
            rendered.append("\n")
            # Subtitle — единый pulsating color
            sub_color = GRADIENT_COLORS[self.offset % len(GRADIENT_COLORS)]
            rendered.append(
                self.subtitle.center(50),
                style=f"italic {sub_color}",
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
            style=f"italic {GRADIENT_COLORS[3]}",
        )
    console.print(rich_text)
