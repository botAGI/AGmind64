"""Phase L.D: tests for agmind.migrations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agmind.migrations.base import Migration, MigrationContext
from agmind.migrations.runner import MigrationRunner
from agmind.migrations.state import AppliedMigration, SchemaState
from agmind.migrations.versions.v001_initial import V001Initial

pytestmark = pytest.mark.backend_any


# ---------- fake migration helper ----------


@dataclass
class FakeMigration(Migration):
    """Записывает up/down вызовы для assertion."""

    version: int = 0
    description: str = ""
    up_calls: list[str] = field(default_factory=list)
    down_calls: list[str] = field(default_factory=list)
    fail_up: bool = False

    def up(self, ctx: MigrationContext) -> None:
        if self.fail_up:
            raise RuntimeError(f"v{self.version:03d} forced failure")
        marker = ctx.user_state_dir / f"v{self.version:03d}.marker"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"applied v{self.version}", encoding="utf-8")
        self.up_calls.append(str(marker))

    def down(self, ctx: MigrationContext) -> None:
        marker = ctx.user_state_dir / f"v{self.version:03d}.marker"
        if marker.exists():
            marker.unlink()
        self.down_calls.append(str(marker))


def _make_runner(tmp_path: Path, migrations: list[Migration]) -> MigrationRunner:
    return MigrationRunner(
        user_state_dir=tmp_path / "user",
        system_state_dir=tmp_path / "sys",
        migrations=migrations,
    )


# ---------- SchemaState ----------


def test_schema_state_load_missing_returns_empty(tmp_path: Path) -> None:
    state = SchemaState.load(tmp_path / "schema.json")
    assert state.schema_version == 0
    assert state.applied == []


def test_schema_state_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "schema.json"
    state = SchemaState(
        schema_version=2,
        applied=[
            AppliedMigration(version=1, name="v001_x", applied_at="2026-05-20T10:00:00+00:00"),
            AppliedMigration(version=2, name="v002_y", applied_at="2026-05-20T11:00:00+00:00"),
        ],
    )
    state.save(path)
    loaded = SchemaState.load(path)
    assert loaded.schema_version == 2
    assert [a.version for a in loaded.applied] == [1, 2]
    assert loaded.applied[1].name == "v002_y"


def test_schema_state_record_advances_version() -> None:
    state = SchemaState()
    m = FakeMigration(version=3)
    state.record(m)
    assert state.schema_version == 3
    assert state.is_applied(3)


def test_schema_state_record_idempotent() -> None:
    state = SchemaState()
    m = FakeMigration(version=1)
    state.record(m)
    state.record(m)
    assert len(state.applied) == 1


def test_schema_state_unrecord_recomputes_version() -> None:
    state = SchemaState()
    state.record(FakeMigration(version=1))
    state.record(FakeMigration(version=2))
    state.record(FakeMigration(version=3))
    assert state.schema_version == 3
    state.unrecord(3)
    assert state.schema_version == 2
    state.unrecord(2)
    state.unrecord(1)
    assert state.schema_version == 0


def test_schema_state_load_corrupted_raises(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        SchemaState.load(path)


# ---------- MigrationRunner ----------


def test_runner_empty_when_no_migrations(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, [])
    assert runner.current_version == 0
    assert runner.pending() == []
    assert runner.applied() == []


def test_runner_up_applies_all(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, [FakeMigration(version=1), FakeMigration(version=2)])
    applied = runner.up()
    assert [m.version for m in applied] == [1, 2]
    assert runner.current_version == 2
    assert runner.pending() == []


def test_runner_up_target_partial(tmp_path: Path) -> None:
    runner = _make_runner(
        tmp_path,
        [FakeMigration(version=1), FakeMigration(version=2), FakeMigration(version=3)],
    )
    applied = runner.up(target=2)
    assert [m.version for m in applied] == [1, 2]
    assert runner.current_version == 2
    pending = runner.pending()
    assert [m.version for m in pending] == [3]


def test_runner_up_idempotent(tmp_path: Path) -> None:
    migrations = [FakeMigration(version=1), FakeMigration(version=2)]
    runner = _make_runner(tmp_path, migrations)
    runner.up()
    # Fresh runner reading saved schema.json
    runner2 = _make_runner(tmp_path, [FakeMigration(version=1), FakeMigration(version=2)])
    assert runner2.current_version == 2
    assert runner2.up() == []


def test_runner_down_rolls_back_steps(tmp_path: Path) -> None:
    runner = _make_runner(
        tmp_path,
        [FakeMigration(version=1), FakeMigration(version=2), FakeMigration(version=3)],
    )
    runner.up()
    rolled = runner.down(steps=2)
    assert [m.version for m in rolled] == [3, 2]
    assert runner.current_version == 1


def test_runner_down_with_target(tmp_path: Path) -> None:
    runner = _make_runner(
        tmp_path,
        [FakeMigration(version=1), FakeMigration(version=2), FakeMigration(version=3)],
    )
    runner.up()
    rolled = runner.down(target=1)
    assert [m.version for m in rolled] == [3, 2]
    assert runner.current_version == 1


def test_runner_down_when_empty(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, [FakeMigration(version=1)])
    assert runner.down(steps=1) == []
    assert runner.current_version == 0


def test_runner_persists_state(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, [FakeMigration(version=1)])
    runner.up()
    schema_file = tmp_path / "user" / "schema.json"
    assert schema_file.exists()
    payload = json.loads(schema_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["applied"][0]["version"] == 1


def test_runner_rejects_duplicate_versions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _make_runner(tmp_path, [FakeMigration(version=1), FakeMigration(version=1)])


def test_runner_rejects_zero_version(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid version"):
        _make_runner(tmp_path, [FakeMigration(version=0)])


def test_runner_discover_finds_v001(tmp_path: Path) -> None:
    discovered = MigrationRunner.discover()
    versions = sorted(m.version for m in discovered)
    assert 1 in versions
    assert any(isinstance(m, V001Initial) for m in discovered)


def test_runner_orders_by_version(tmp_path: Path) -> None:
    runner = _make_runner(
        tmp_path,
        [FakeMigration(version=3), FakeMigration(version=1), FakeMigration(version=2)],
    )
    versions = [m.version for m in runner.all_migrations]
    assert versions == [1, 2, 3]


# ---------- V001 baseline ----------


def test_v001_initial_creates_user_state_dir(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_state"
    ctx = MigrationContext(
        user_state_dir=user_dir,
        system_state_dir=tmp_path / "sys",
        log=logging.getLogger("agmind.test"),
    )
    V001Initial().up(ctx)
    assert user_dir.exists()


def test_v001_initial_down_is_no_op(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_state"
    user_dir.mkdir()
    (user_dir / "setup-state.json").write_text("{}", encoding="utf-8")
    ctx = MigrationContext(
        user_state_dir=user_dir,
        system_state_dir=tmp_path / "sys",
        log=logging.getLogger("agmind.test"),
    )
    V001Initial().down(ctx)
    # User data must survive rollback to baseline
    assert (user_dir / "setup-state.json").exists()


def test_v001_idempotent(tmp_path: Path) -> None:
    ctx = MigrationContext(
        user_state_dir=tmp_path / "user_state",
        system_state_dir=tmp_path / "sys",
        log=logging.getLogger("agmind.test"),
    )
    m = V001Initial()
    m.up(ctx)
    m.up(ctx)  # повторный — без ошибки


# ---------- CLI smoke ----------


def test_cli_status_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import migrate_cmd

    def fake_runner() -> MigrationRunner:
        return MigrationRunner(
            user_state_dir=tmp_path / "user",
            system_state_dir=tmp_path / "sys",
            migrations=[FakeMigration(version=1, description="test")],
        )

    monkeypatch.setattr(migrate_cmd, "_make_runner", fake_runner)
    rc = migrate_cmd.cmd_status()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Current schema version" in out
    assert "Pending migrations" in out


def test_cli_status_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import migrate_cmd

    def fake_runner() -> MigrationRunner:
        return MigrationRunner(
            user_state_dir=tmp_path / "user",
            system_state_dir=tmp_path / "sys",
            migrations=[FakeMigration(version=1, description="test")],
        )

    monkeypatch.setattr(migrate_cmd, "_make_runner", fake_runner)
    rc = migrate_cmd.cmd_status(as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current_version"] == 0
    assert len(payload["pending"]) == 1


def test_cli_up_and_status_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import migrate_cmd

    user_dir = tmp_path / "user"
    sys_dir = tmp_path / "sys"

    def fake_runner() -> MigrationRunner:
        return MigrationRunner(
            user_state_dir=user_dir,
            system_state_dir=sys_dir,
            migrations=[FakeMigration(version=1, description="alpha"),
                        FakeMigration(version=2, description="beta")],
        )

    monkeypatch.setattr(migrate_cmd, "_make_runner", fake_runner)
    assert migrate_cmd.cmd_up() == 0
    out = capsys.readouterr().out
    assert "Schema now at v002" in out

    assert migrate_cmd.cmd_status() == 0
    out2 = capsys.readouterr().out
    assert "v002" in out2
    assert "No pending" in out2


def test_cli_down_rejects_negative_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import migrate_cmd

    rc = migrate_cmd.cmd_down(steps=-1)
    assert rc == 2


def test_cli_list_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import migrate_cmd

    def fake_runner() -> MigrationRunner:
        return MigrationRunner(
            user_state_dir=tmp_path / "user",
            system_state_dir=tmp_path / "sys",
            migrations=[FakeMigration(version=1, description="alpha")],
        )

    monkeypatch.setattr(migrate_cmd, "_make_runner", fake_runner)
    rc = migrate_cmd.cmd_list(as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["version"] == 1
    assert payload[0]["applied"] is False
