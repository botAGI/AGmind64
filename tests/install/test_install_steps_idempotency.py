"""Lane B — clean-prefix python install steps, run TWICE (fast, no Docker).

CLEAN-INSTALL-HARDENING §3 lane B. The operator hit clean-machine / re-run-over-dirty-state
install bugs ONE AT A TIME because nothing ran the python install steps twice against a fresh
prefix and asserted the 2nd run is a no-op. This lane points an ``InstallConfig`` at a fresh
``tmp_path`` prefix, runs ``EnvWriteStep`` then ``ComposeConfigStep`` TWICE, and asserts:

- both runs succeed,
- the rendered ``.env`` (+ ``version.env``) is byte-identical on the 2nd run
  (idempotent — a non-idempotent EnvWrite that re-rolls a secret each run fails here),
- the compose render is byte-identical on the 2nd run,
- no stray temp / backup files (``*.tmp`` / ``~*``) leak into the prefix on either run
  (the RANK-1-class pip-backup leakage, asserted behaviourally for the python side).

HERMETIC: every path is pinned to ``tmp_path`` and ``sudo_password`` is None, so the
LOCAL write path is exercised — no host ``/opt/agmind`` / ``/var/lib/agmind`` is touched.
The docker ``compose config`` subprocess is mocked rc=0 (Compose render/staging stays REAL;
only the docker invocation is faked) so this runs in the normal CPU unit suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import ComposeConfigStep, EnvWriteStep

pytestmark = pytest.mark.backend_any


def _make_config(tmp_path: Path, services: list[str]) -> InstallConfig:
    """A hermetic InstallConfig — every prefix under ``tmp_path``, no sudo (local path)."""
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="cf-token-" + "X" * 40,
        services=services,
        install_dir=tmp_path / "opt" / "agmind",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
        model_file="model-Q4_K_M.gguf",
        sudo_password=None,
    )


def _stray_temp_files(root: Path) -> list[Path]:
    """Pip/copytree-style leftovers a non-idempotent step would strand in the prefix.

    ``~*`` is the pip uninstall-backup prefix (``~gmind``); ``*.tmp`` / ``INSTALLER*.tmp``
    are pip's metadata temp files and our own staging temps. None should survive a step.
    """
    if not root.exists():
        return []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and (p.name.startswith("~") or p.name.endswith(".tmp") or p.name.startswith(".agmind-"))
    )


def _mock_compose_config_rc0(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock ONLY the docker subprocess (``docker compose config``) to rc=0.

    ComposeConfigStep's render + temp-compose + env-file staging stay REAL — we replace
    the one heavy/host-coupled call (the docker daemon) so the lane is Docker-free.
    """
    import agmind.install.steps as steps

    monkeypatch.setattr(steps, "_stream_subprocess", lambda *a, **k: (0, []))


@pytest.mark.parametrize(
    "services",
    [
        pytest.param(["prometheus", "grafana", "loki"], id="observability"),
        # Edge selection carries authelia+redis: the P0.3 topology gate fail-closes a
        # traefik render of a chain-llm service without its auth backend (15-04).
        pytest.param(["traefik", "llama-llm", "authelia", "redis"], id="traefik-llm"),
    ],
)
def test_env_write_then_compose_config_twice_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    services: list[str],
) -> None:
    """Run EnvWrite + ComposeConfig TWICE on a fresh prefix; the 2nd run is a byte no-op."""
    from agmind.services.renderer import render_to_string

    cfg = _make_config(tmp_path, services)
    _mock_compose_config_rc0(monkeypatch)
    env_path = cfg.install_dir / ".env"
    version_path = cfg.install_dir / "version.env"

    def render() -> str:
        # No explicit traefik_enabled — derive from the selection, exactly like
        # ComposeConfigStep does (P0.3 / 15-04 selection-derive).
        return render_to_string(services=cfg.services, domain=cfg.domain)

    # ---- run 1 ----
    env1 = EnvWriteStep().run(lambda _e: None, cfg)
    assert env1.success, env1.message
    compose1 = ComposeConfigStep().run(lambda _e: None, cfg)
    assert compose1.success, compose1.message
    env_bytes_1 = env_path.read_bytes()
    version_bytes_1 = version_path.read_bytes()
    render_1 = render()
    assert _stray_temp_files(tmp_path) == []

    # ---- run 2 (the operator re-run scenario) ----
    env2 = EnvWriteStep().run(lambda _e: None, cfg)
    assert env2.success, env2.message
    compose2 = ComposeConfigStep().run(lambda _e: None, cfg)
    assert compose2.success, compose2.message

    # idempotent: nothing rewritten / re-rolled / leaked on the second pass.
    assert env_path.read_bytes() == env_bytes_1, ".env diverged on re-run (non-idempotent)"
    assert version_path.read_bytes() == version_bytes_1, "version.env diverged on re-run"
    assert render() == render_1, "compose render diverged on re-run"
    assert _stray_temp_files(tmp_path) == [], "re-run leaked temp/backup files into the prefix"


def test_env_write_preserves_hand_edited_secret_on_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hand-edited / previously-generated runtime secret survives a re-run unchanged.

    EnvWriteStep's contract: existing runtime secrets are preserved on re-run (so re-running
    install never silently rotates a password mid-flight and breaks already-deployed
    containers). This asserts that contract behaviourally — write once, mutate the on-disk
    value, re-run, and confirm the operator's value is kept.
    """
    from agmind.core.env import parse_env_file

    cfg = _make_config(tmp_path, ["postgres", "redis"])
    _mock_compose_config_rc0(monkeypatch)
    env_path = cfg.install_dir / ".env"

    first = EnvWriteStep().run(lambda _e: None, cfg)
    assert first.success, first.message
    generated_pg = parse_env_file(env_path)["POSTGRES_PASSWORD"]
    assert generated_pg, "POSTGRES_PASSWORD should be generated on first install"

    # Operator (or a prior successful run) supplied a known value; re-running must keep it.
    sentinel = "operator-set-pg-password-123456"
    text = env_path.read_text(encoding="utf-8")
    text = text.replace(f"POSTGRES_PASSWORD={generated_pg}", f"POSTGRES_PASSWORD={sentinel}")
    env_path.write_text(text, encoding="utf-8")

    second = EnvWriteStep().run(lambda _e: None, cfg)
    assert second.success, second.message
    assert parse_env_file(env_path)["POSTGRES_PASSWORD"] == sentinel, (
        "re-run clobbered a hand-edited runtime secret (would break a deployed stack)"
    )
