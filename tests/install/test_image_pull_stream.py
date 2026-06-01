"""Live-log fix: the install image-pull must STREAM, not run silently.

`ImagePullStep` ran `docker compose pull --policy missing --quiet`. `--quiet`
suppresses every per-layer line, so the TUI sat at 0% / "Starting…" for minutes
during a multi-GB pull (ragflow ~9 GB) and looked frozen. Drop `--quiet`, render
plain progress (`docker compose --progress plain`, a GLOBAL flag), and emit a
coarse PROGRESS pct as service images complete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install import steps
from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import ImagePullStep, _docker_compose_cmd, _pull_progress_pct

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path, services: list[str]) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=services,
        install_dir=tmp_path / "opt",
    )


def test_docker_compose_cmd_injects_progress_as_global_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, ["redis"])
    cmd = _docker_compose_cmd(cfg, ["pull", "--policy", "missing"], progress="plain")
    assert "--progress" in cmd
    assert cmd[cmd.index("--progress") + 1] == "plain"
    # --progress is a GLOBAL flag — it must come BEFORE the `pull` subcommand.
    assert cmd.index("--progress") < cmd.index("pull")


def test_docker_compose_cmd_no_progress_by_default(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, ["redis"])
    assert "--progress" not in _docker_compose_cmd(cfg, ["config"])


def test_docker_compose_cmd_sudo_forwards_invoking_user_docker_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installer sudo-pull must keep docker login credentials."""
    cfg = _cfg(tmp_path, ["redis"])
    cfg.sudo_password = "pw"
    monkeypatch.setattr(
        steps,
        "_user_docker_config_dir",
        lambda: "/home/op/.docker",
        raising=False,
    )

    cmd = _docker_compose_cmd(cfg, ["pull", "--policy", "missing"], progress="plain")

    assert cmd == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "env",
        "DOCKER_CONFIG=/home/op/.docker",
        "docker",
        "compose",
        "--progress",
        "plain",
        "pull",
        "--policy",
        "missing",
    ]


def test_pull_progress_pct_counts_completed_services() -> None:
    services = {"redis", "postgres", "traefik"}
    pulled: set[str] = set()
    # Layer lines / non-service lines yield nothing.
    assert _pull_progress_pct("a1b2c3 Downloading [===>   ] 4MB/40MB", services, pulled) is None
    assert _pull_progress_pct("a1b2c3 Pull complete", services, pulled) is None
    # Service-completion lines advance the bar.
    assert _pull_progress_pct("redis Pulled", services, pulled) == 33
    assert _pull_progress_pct("postgres Pulled", services, pulled) == 66
    # A duplicate completion does not double-count.
    assert _pull_progress_pct("redis Pulled", services, pulled) is None
    assert _pull_progress_pct("traefik Pulled", services, pulled) == 100


def test_image_pull_streams_plain_without_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.services import renderer

    monkeypatch.setattr(
        renderer,
        "render_to_string",
        lambda **_kw: "services:\n  redis:\n    image: redis:8.4.3-alpine\n",
    )
    captured: dict[str, list[str]] = {}

    def fake_stream(cmd: list[str], callback: object, step_id: str, **_kw: object):
        captured["cmd"] = cmd
        return 0, []

    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream)

    result = ImagePullStep().run(lambda _e: None, _cfg(tmp_path, ["redis"]))

    assert result.success
    cmd = captured["cmd"]
    assert "--quiet" not in cmd, "the silent --quiet flag must be gone (it froze the bar)"
    assert "--progress" in cmd and cmd[cmd.index("--progress") + 1] == "plain"
    assert cmd.index("--progress") < cmd.index("pull")
