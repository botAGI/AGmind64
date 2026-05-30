"""Live-log fix (deploy path): split a streamed `pull` phase out of the blocking `up`.

`agmind deploy` ran `docker compose up -d --remove-orphans --quiet-pull` as ONE
blocking subprocess.run — during a multi-GB pull the DeployProgressScreen showed no
output for minutes (looked frozen) and the `up` could not be cancelled. Add a
streaming Popen runner (`_stream_compose`), run a visible `docker compose --progress
plain pull --policy missing` phase first, then `up --pull never` (no silent re-pull).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agmind.deploy import runner

pytestmark = pytest.mark.backend_any


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._it = iter(lines)
        self.closed = False

    def __iter__(self) -> _FakeStdout:
        return self

    def __next__(self) -> str:
        return next(self._it)

    def close(self) -> None:
        self.closed = True


class _FakeStdin:
    def __init__(self) -> None:
        self.written = ""
        self.closed = False

    def write(self, s: str) -> None:
        self.written += s

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    def __init__(self, lines: list[str], rc: int = 0, *, with_stdin: bool = False) -> None:
        self.stdout = _FakeStdout(lines)
        self.stdin = _FakeStdin() if with_stdin else None
        self.returncode = rc
        self.pid = 4242
        self._done = False

    def wait(self, timeout: float | None = None) -> int:
        self._done = True
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode if self._done else None


def test_stream_compose_streams_lines_and_returns_rc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakePopen(["redis Pulling\n", "redis Pulled\n"], rc=0)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: fake)

    seen: list[str] = []
    rc, tail = runner._stream_compose(
        ["--progress", "plain", "pull", "--policy", "missing", "redis"],
        cwd=tmp_path,
        on_line=seen.append,
    )
    assert rc == 0
    assert seen == ["redis Pulling", "redis Pulled"]
    assert "redis Pulled" in tail
    assert fake.stdout.closed


def test_stream_compose_sudo_writes_password_and_builds_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    captured: dict[str, object] = {}
    fake = _FakePopen([], rc=0, with_stdin=True)

    def fake_popen(cmd: list[str], **kwargs: object) -> _FakePopen:
        captured["cmd"] = cmd
        return fake

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner, "_user_docker_config_dir", lambda: None)

    rc, _ = runner._stream_compose(
        ["--progress", "plain", "pull"], cwd=tmp_path, sudo_password="pw"
    )
    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[:5] == ["sudo", "-S", "-p", "", "--"]
    assert "--progress" in cmd and "pull" in cmd
    assert fake.stdin is not None and fake.stdin.written == "pw\n"


def test_stream_compose_kills_child_on_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()

    class _CancelAfterFirst(_FakeStdout):
        def __next__(self) -> str:
            line = super().__next__()
            cancel.set()  # fire cancel after handing back the first line
            return line

    fake = _FakePopen(["one\n", "two\n", "three\n"], rc=0)
    fake.stdout = _CancelAfterFirst(["one\n", "two\n", "three\n"])
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: fake)

    killed: list[object] = []
    monkeypatch.setattr(runner, "_kill_proc_group", lambda proc: killed.append(proc))

    rc, _ = runner._stream_compose(["pull"], cwd=tmp_path, cancel_event=cancel)
    assert killed == [fake], "the child must be killed when cancel fires mid-stream"
    assert rc == 130


def test_deploy_apply_pulls_streamed_then_up_without_quiet_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    rendered = "services:\n  redis:\n    image: redis:8.4.3-alpine\n"

    monkeypatch.setattr(runner, "render_to_string", lambda **_k: rendered)
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_write_text_maybe_sudo", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_wait_healthy", lambda *a, **k: (True, []))

    class FakeSnap:
        id = "snap-1"

    class FakeSnapMgr:
        def __init__(self, sudo_password: str | None = None) -> None:
            pass

        def save(self, **_k: object) -> FakeSnap:
            return FakeSnap()

    monkeypatch.setattr(runner, "SnapshotManager", FakeSnapMgr)
    monkeypatch.setattr(runner, "_read_text_maybe_sudo", lambda *a, **k: "services: {}\n")

    stream_calls: list[list[str]] = []

    def fake_stream(args: list[str], **_k: object) -> tuple[int, str]:
        stream_calls.append(args)
        return 0, ""

    monkeypatch.setattr(runner, "_stream_compose", fake_stream)

    steps: list[str] = []
    result = runner.deploy(
        profiles=[],
        install_dir=install_dir,
        domain="ci.example.com",
        apply=True,
        no_prompt=True,
        services=["redis"],
        progress=lambda step, _msg: steps.append(step),
    )

    assert result.success, result.message
    # Two streamed compose phases: pull (with --progress plain) then up (--pull never).
    assert len(stream_calls) == 2
    pull_args, up_args = stream_calls
    assert "pull" in pull_args and pull_args[:2] == ["--progress", "plain"]
    assert "up" in up_args
    assert "--pull" in up_args and up_args[up_args.index("--pull") + 1] == "never"
    assert "--quiet-pull" not in up_args
    # The 'pull' step is emitted before 'compose_up'.
    assert "pull" in steps and "compose_up" in steps
    assert steps.index("pull") < steps.index("compose_up")
