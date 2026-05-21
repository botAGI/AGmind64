"""Phase J.2: live deployment dashboard — `agmind status --tui`.

Опрашивает `docker compose ps --format json` в install_dir каждые N секунд,
показывает сервисы / state / health / uptime / image в Textual DataTable.

Сознательно read-only: никаких restart/stop кнопок — destructive ops идут
через `agmind deploy`/`agmind rollback`, а не из дашборда.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from agmind.log import logger

log = logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/agmind")
DEFAULT_REFRESH_INTERVAL = 5.0
COMPOSE_PS_TIMEOUT = 10.0


@dataclass(frozen=True)
class ServiceState:
    """One row of docker compose ps output."""

    service: str
    state: str  # running / exited / restarting / created / paused
    health: str  # healthy / unhealthy / starting / "" (no healthcheck)
    uptime: str  # "Up 2 hours" / "Exited (0) 5 minutes ago"
    image: str
    name: str  # container name (agmind-traefik-1)


@dataclass(frozen=True)
class ComposeStateSnapshot:
    services: tuple[ServiceState, ...]
    error: str | None
    compose_file: Path

    @property
    def total(self) -> int:
        return len(self.services)

    @property
    def running(self) -> int:
        return sum(1 for s in self.services if s.state == "running")

    @property
    def healthy(self) -> int:
        return sum(1 for s in self.services if s.health == "healthy")

    @property
    def unhealthy(self) -> int:
        return sum(1 for s in self.services if s.health == "unhealthy")


def _run_compose_ps(install_dir: Path) -> tuple[str, str, int]:
    """Shell-out to `docker compose ps --format json --all`. Returns (stdout, stderr, rc)."""
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", "--all"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=COMPOSE_PS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return "", "docker command not found", 127
    except subprocess.TimeoutExpired:
        return "", f"docker compose ps timed out after {COMPOSE_PS_TIMEOUT}s", 124
    return proc.stdout, proc.stderr, proc.returncode


def query_compose_state(install_dir: Path) -> ComposeStateSnapshot:
    """Query current deployment state. Never raises — wraps errors в snapshot."""
    compose_file = install_dir / "docker-compose.yml"
    if not compose_file.exists():
        return ComposeStateSnapshot(
            services=(),
            error=f"no deployment at {compose_file} (run `agmind deploy --apply`)",
            compose_file=compose_file,
        )

    stdout, stderr, rc = _run_compose_ps(install_dir)
    if rc != 0:
        return ComposeStateSnapshot(
            services=(),
            error=f"docker compose ps rc={rc}: {stderr[-200:].strip() or 'no stderr'}",
            compose_file=compose_file,
        )

    services: list[ServiceState] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("skipping non-JSON line from compose ps: %s", exc)
            continue
        services.append(
            ServiceState(
                service=obj.get("Service", ""),
                state=obj.get("State", ""),
                health=obj.get("Health", ""),
                uptime=obj.get("Status", ""),
                image=obj.get("Image", ""),
                name=obj.get("Name", ""),
            )
        )
    services.sort(key=lambda s: (s.service, s.name))
    return ComposeStateSnapshot(
        services=tuple(services), error=None, compose_file=compose_file
    )


_HEALTH_GLYPH: dict[str, str] = {
    "healthy": "[green]●[/green] healthy",
    "unhealthy": "[red]●[/red] unhealthy",
    "starting": "[yellow]●[/yellow] starting",
    "": "[dim]○[/dim] —",
}
_STATE_GLYPH: dict[str, str] = {
    "running": "[green]running[/green]",
    "exited": "[red]exited[/red]",
    "restarting": "[yellow]restarting[/yellow]",
    "paused": "[yellow]paused[/yellow]",
    "created": "[dim]created[/dim]",
    "dead": "[red]dead[/red]",
}


class StatusDashboardApp(App[None]):
    """Phase J.2: live deployment dashboard."""

    CSS_PATH: ClassVar[str | None] = "dashboard.tcss"

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh now", show=True),
        # Phase M4.5
        Binding("p", "toggle_pause", "Pause/resume", show=True),
        Binding("f", "cycle_filter", "Filter", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
    ]

    TITLE = "AGmind · Dashboard"
    SUB_TITLE = "live docker compose ps · J.2"

    # Phase M4.5: filter / sort cycle
    FILTER_OPTIONS = ("all", "running", "unhealthy", "exited")
    SORT_OPTIONS = ("name", "state", "health", "uptime")

    def __init__(
        self,
        install_dir: Path = DEFAULT_INSTALL_DIR,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ) -> None:
        super().__init__()
        self.install_dir = Path(install_dir)
        self.refresh_interval = float(refresh_interval)
        self._snapshot: ComposeStateSnapshot | None = None
        self._last_refresh_ts: float = 0.0
        self._refresh_timer: object | None = None
        # Phase M4.5 state
        self._paused = False
        self._filter_idx = 0   # 0 = all
        self._sort_idx = 0     # 0 = name

    @property
    def _filter_name(self) -> str:
        return self.FILTER_OPTIONS[self._filter_idx]

    @property
    def _sort_name(self) -> str:
        return self.SORT_OPTIONS[self._sort_idx]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("[dim]loading…[/dim]", id="dashboard-summary")
        yield DataTable(id="services-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#services-table", DataTable)
        table.add_columns("SERVICE", "STATE", "HEALTH", "UPTIME", "IMAGE")
        table.cursor_type = "row"
        self.refresh_state()
        self._refresh_timer = self.set_interval(
            self.refresh_interval, self.refresh_state
        )

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._refresh_timer = None

    def refresh_state(self) -> None:
        # Phase M4.5: respect pause
        if self._paused:
            return
        self._snapshot = query_compose_state(self.install_dir)
        self._last_refresh_ts = time.time()
        self._update_view()

    def action_refresh(self) -> None:
        # Manual refresh — works даже paused
        self._snapshot = query_compose_state(self.install_dir)
        self._last_refresh_ts = time.time()
        self._update_view()

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self._safe_update_view()

    def action_cycle_filter(self) -> None:
        self._filter_idx = (self._filter_idx + 1) % len(self.FILTER_OPTIONS)
        self._safe_update_view()

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self.SORT_OPTIONS)
        self._safe_update_view()

    def _safe_update_view(self) -> None:
        """Try update view, skip silently если widgets not mounted (unit tests)."""
        try:
            self._update_view()
        except Exception:  # noqa: BLE001
            pass

    def _update_view(self) -> None:
        summary = self.query_one("#dashboard-summary", Static)
        table = self.query_one("#services-table", DataTable)
        snap = self._snapshot
        if snap is None:
            summary.update("[dim]loading…[/dim]")
            return

        if snap.error is not None:
            summary.update(f"[red]✗ {snap.error}[/red]")
            table.clear()
            return

        if snap.total == 0:
            summary.update(
                f"[yellow]∅ no containers in {self.install_dir}[/yellow]"
            )
            table.clear()
            return

        unhealthy_part = (
            f" · [red]Unhealthy: {snap.unhealthy}[/red]" if snap.unhealthy else ""
        )
        # Phase M4.5: include filter/sort/pause state в header
        pause_part = "  [yellow]⏸ PAUSED[/yellow]" if self._paused else ""
        filter_part = (
            f"  [dim]filter:[/dim] [bold]{self._filter_name}[/bold]"
            if self._filter_name != "all" else ""
        )
        sort_part = (
            f"  [dim]sort:[/dim] [bold]{self._sort_name}[/bold]"
            if self._sort_name != "name" else ""
        )
        summary.update(
            f"Install: [bold]{self.install_dir}[/bold] · "
            f"Running: [green]{snap.running}/{snap.total}[/green] · "
            f"Healthy: [green]{snap.healthy}/{snap.total}[/green]"
            f"{unhealthy_part}"
            f"{pause_part}{filter_part}{sort_part}"
        )

        # Phase M4.5: filter
        services = list(snap.services)
        f = self._filter_name
        if f == "running":
            services = [s for s in services if s.state == "running"]
        elif f == "unhealthy":
            services = [s for s in services if s.health == "unhealthy"]
        elif f == "exited":
            services = [s for s in services if s.state != "running"]
        # Phase M4.5: sort
        sort_key = self._sort_name
        if sort_key == "state":
            services.sort(key=lambda s: (s.state, s.service))
        elif sort_key == "health":
            services.sort(key=lambda s: (s.health, s.service))
        elif sort_key == "uptime":
            services.sort(key=lambda s: s.uptime)
        else:  # name
            services.sort(key=lambda s: (s.service, s.name))

        table.clear()
        for s in services:
            health = _HEALTH_GLYPH.get(s.health, s.health or "—")
            state = _STATE_GLYPH.get(s.state, s.state or "—")
            image_short = s.image if len(s.image) <= 40 else f"…{s.image[-39:]}"
            uptime_short = s.uptime if len(s.uptime) <= 30 else f"{s.uptime[:29]}…"
            table.add_row(s.service or s.name, state, health, uptime_short, image_short)


def run_dashboard(
    install_dir: Path = DEFAULT_INSTALL_DIR,
    refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
) -> None:
    """Launch dashboard. Blocking until user quits."""
    app = StatusDashboardApp(
        install_dir=install_dir, refresh_interval=refresh_interval
    )
    app.run()
