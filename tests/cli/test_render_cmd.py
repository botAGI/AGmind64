from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agmind.cli import render_cmd

pytestmark = pytest.mark.backend_any


def test_render_compose_passes_explicit_services_to_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_render_to_string(**kwargs: object) -> str:
        captured.update(kwargs)
        return "services: {}\n"

    monkeypatch.setattr(render_cmd, "render_to_string", fake_render_to_string)

    assert render_cmd.cmd_render_compose(profiles=["stale"], services=["n8n"]) == 0
    assert captured["profiles"] == ["stale"]
    assert captured["services"] == ["n8n"]


def test_render_compose_preserves_existing_output_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "docker-compose.yml"
    old = "services:\n  old: {}\n"
    output.write_text(old, encoding="utf-8")
    output.chmod(0o600)
    original_write_text = Path.write_text

    def fake_render_to_string(**kwargs: object) -> str:
        return "services:\n  new: {}\n"

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self == output or self.name == f".{output.name}.tmp":
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(render_cmd, "render_to_string", fake_render_to_string)
    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        render_cmd.cmd_render_compose(profiles=["core"], output=output)

    assert output.read_text(encoding="utf-8") == old
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not output.with_name(f".{output.name}.tmp").exists()


def test_render_kubernetes_passes_explicit_services_to_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_render_to_string(**kwargs: object) -> str:
        captured.update(kwargs)
        return "apiVersion: v1\nkind: List\nitems: []\n"

    import agmind.services.kubernetes_renderer as kubernetes_renderer

    monkeypatch.setattr(kubernetes_renderer, "render_to_string", fake_render_to_string)

    assert render_cmd.cmd_render_kubernetes(profiles=["stale"], services=["n8n"]) == 0
    assert captured["profiles"] == ["stale"]
    assert captured["services"] == ["n8n"]


def test_render_kubernetes_preserves_existing_output_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "manifest.yaml"
    old = "apiVersion: v1\nkind: List\nitems: []\n"
    output.write_text(old, encoding="utf-8")
    output.chmod(0o600)
    original_write_text = Path.write_text

    def fake_render_to_string(**kwargs: object) -> str:
        return "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: agmind\n"

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self == output or self.name == f".{output.name}.tmp":
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    import agmind.services.kubernetes_renderer as kubernetes_renderer

    monkeypatch.setattr(kubernetes_renderer, "render_to_string", fake_render_to_string)
    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        render_cmd.cmd_render_kubernetes(profiles=["core"], output=output)

    assert output.read_text(encoding="utf-8") == old
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not output.with_name(f".{output.name}.tmp").exists()
