"""Phase 4.1: `agmind status --watch --interval N` — a HEADLESS auto-refresh loop.

The interactive picture was only reachable via the Textual `--tui`; `--watch` gives the
same live data over a plain terminal (a rich.Live re-render loop) reusing the existing
one-shot status data source — NO new domain logic in cli/. Asserted behaviourally
(MEMORY: CI wraps rich/typer formatting — never assert exact formatted columns):
  * Ctrl-C (KeyboardInterrupt) exits cleanly (rc 0), not a traceback.
  * Without a TTY it degrades to a SINGLE pass + a warning on stderr (no animated Live).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.backend_any


class _FakeInfo:
    backend = "cpu"
    engine = "llama-cpp"
    device_id = "cpu:0"
    name = "Fake CPU"
    total_memory_bytes = 8 * 1024**3
    capabilities = {"fp16": False}


class _FakeBackend:
    def device_info(self) -> _FakeInfo:
        return _FakeInfo()


def _patch_backend(monkeypatch) -> None:
    import agmind.compute as compute

    monkeypatch.setattr(compute, "get_backend", lambda: _FakeBackend())
    monkeypatch.setattr(compute, "list_available_backends", lambda: ["cpu"])


def test_watch_ctrl_c_exits_cleanly(monkeypatch, tmp_path) -> None:
    """A TTY watch loop sleeps between ticks; a KeyboardInterrupt from sleep exits rc 0."""
    from agmind.cli import _make_app, core_cmd

    _patch_backend(monkeypatch)
    # Force the TTY (animated) branch regardless of the test runner's stdout.
    monkeypatch.setattr(core_cmd, "_stdout_is_tty", lambda: True)

    ticks = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt
        return None

    monkeypatch.setattr(core_cmd.time, "sleep", fake_sleep)

    result = CliRunner().invoke(
        _make_app(),
        ["status", "--watch", "--interval", "1", "--install-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    # Looped at least twice before Ctrl-C; the device name is rendered each tick.
    assert ticks["n"] >= 2
    assert "Fake CPU" in result.output


def test_watch_without_tty_degrades_to_single_pass(monkeypatch, tmp_path) -> None:
    """Non-TTY (pipe/CI/ssh-no-tty): one render, a warning on stderr, no sleep loop."""
    from agmind.cli import _make_app, core_cmd

    _patch_backend(monkeypatch)
    monkeypatch.setattr(core_cmd, "_stdout_is_tty", lambda: False)

    def boom(_seconds: float) -> None:
        raise AssertionError("must not sleep/loop without a TTY")

    monkeypatch.setattr(core_cmd.time, "sleep", boom)

    # typer 0.26 (click 8.2) captures stderr separately by default → result.stdout
    # / result.stderr are distinct streams.
    result = CliRunner().invoke(
        _make_app(),
        ["status", "--watch", "--interval", "1", "--install-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Fake CPU" in result.stdout
    # Warning explaining the degrade goes to stderr, not stdout.
    assert "watch" in result.stderr.lower()


def test_watch_json_is_a_single_machine_parseable_pass(monkeypatch, tmp_path) -> None:
    """`--watch --json` must NOT enter the screen-owning Live loop — JSON wants line output.

    A single JSON document is emitted and the loop never sleeps (degrades like non-TTY).
    """
    import json

    from agmind.cli import _make_app, core_cmd

    _patch_backend(monkeypatch)
    monkeypatch.setattr(core_cmd, "_stdout_is_tty", lambda: True)

    def boom(_seconds: float) -> None:
        raise AssertionError("must not loop when --json is set")

    monkeypatch.setattr(core_cmd.time, "sleep", boom)

    result = CliRunner().invoke(
        _make_app(),
        ["status", "--watch", "--json", "--install-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    assert payload["selected"]["name"] == "Fake CPU"
