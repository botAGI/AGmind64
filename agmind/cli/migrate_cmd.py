"""Phase L.D: `agmind migrate` CLI subcommands.

agmind migrate status                # current version + pending
agmind migrate list                  # все известные миграции
agmind migrate up [--target N]       # apply pending
agmind migrate down [--steps N | --target N]   # rollback
"""

from __future__ import annotations

import json
import sys

from agmind.migrations.runner import MigrationRunner


def _make_runner() -> MigrationRunner:
    return MigrationRunner()


def cmd_status(as_json: bool = False) -> int:
    runner = _make_runner()
    pending = runner.pending()
    applied = runner.applied()
    if as_json:
        payload = {
            "current_version": runner.current_version,
            "applied": [
                {"version": m.version, "name": m.name, "description": m.description}
                for m in applied
            ],
            "pending": [
                {"version": m.version, "name": m.name, "description": m.description}
                for m in pending
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Current schema version: v{runner.current_version:03d}")
    print(f"Applied: {len(applied)}  Pending: {len(pending)}")
    if applied:
        print("\nApplied migrations:")
        for m in applied:
            print(f"  [x] v{m.version:03d}  {m.description or m.name}")
    if pending:
        print("\nPending migrations:")
        for m in pending:
            print(f"  [ ] v{m.version:03d}  {m.description or m.name}")
    else:
        print("\nNo pending migrations.")
    return 0


def cmd_list(as_json: bool = False) -> int:
    runner = _make_runner()
    migrations = runner.all_migrations
    if as_json:
        payload = [
            {
                "version": m.version,
                "name": m.name,
                "description": m.description,
                "applied": m.version <= runner.current_version,
            }
            for m in migrations
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not migrations:
        print("No migrations registered.")
        return 0
    print(f"{'':<3}{'VERSION':<10} {'NAME':<32} DESCRIPTION")
    print("-" * 80)
    for m in migrations:
        mark = "x" if runner._state.is_applied(m.version) else " "  # noqa: SLF001
        print(f"[{mark}] v{m.version:03d}      {m.name:<32} {m.description}")
    return 0


def cmd_up(target: int | None = None) -> int:
    runner = _make_runner()
    pending = runner.pending()
    if target is not None:
        pending = [m for m in pending if m.version <= target]
    if not pending:
        print("No pending migrations.")
        return 0
    print(f"Applying {len(pending)} migration(s)...")
    try:
        applied = runner.up(target=target)
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    for m in applied:
        print(f"  [x] v{m.version:03d} {m.name} applied")
    print(f"Schema now at v{runner.current_version:03d}")
    return 0


def cmd_down(steps: int = 1, target: int | None = None) -> int:
    if steps < 0:
        print("--steps must be >= 0", file=sys.stderr)
        return 2
    runner = _make_runner()
    if runner.current_version == 0:
        print("No migrations applied — nothing to roll back.")
        return 0
    try:
        rolled = runner.down(steps=steps, target=target)
    except Exception as exc:  # noqa: BLE001
        print(f"Rollback failed: {exc}", file=sys.stderr)
        return 1
    if not rolled:
        print("No migrations rolled back.")
        return 0
    for m in rolled:
        print(f"  <- v{m.version:03d} {m.name} rolled back")
    print(f"Schema now at v{runner.current_version:03d}")
    return 0
