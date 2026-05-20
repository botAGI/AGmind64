"""Phase L.E: thin CLI wrappers for logs / shell / backup / restore.

Бизнес-логика живёт в `agmind/ops/` — здесь только парсинг аргументов и
форматирование вывода.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agmind.ops.backup import (
    DEFAULT_INSTALL_DIR as BACKUP_INSTALL_DIR,
    BackupResult,
    create_backup,
    read_metadata,
    restore_backup,
)
from agmind.ops.exec import logs as do_logs
from agmind.ops.exec import shell as do_shell


def cmd_logs(
    service: str | None,
    install_dir: Path,
    tail: int,
    follow: bool,
) -> int:
    return do_logs(install_dir=install_dir, service=service, tail=tail, follow=follow)


def cmd_shell(
    service: str,
    install_dir: Path,
    cmd: list[str] | None,
    workdir: str | None,
) -> int:
    return do_shell(install_dir=install_dir, service=service, cmd=cmd, workdir=workdir)


def cmd_backup(
    output: Path,
) -> int:
    output = Path(output)
    if output.exists():
        print(f"agmind backup: refusing to overwrite existing {output}", file=sys.stderr)
        return 2
    try:
        result: BackupResult = create_backup(output_path=output)
    except FileNotFoundError as exc:
        print(f"agmind backup: {exc}", file=sys.stderr)
        return 1
    size_mb = result.bytes_written / (1024 * 1024)
    print(f"✓ backup written: {result.output_path} ({size_mb:.2f} MiB)")
    print(f"  included ({len(result.sources_included)}): {', '.join(result.sources_included) or '<none>'}")
    if result.sources_missing:
        print(f"  missing  ({len(result.sources_missing)}): {', '.join(result.sources_missing)}")
    return 0


def cmd_restore(
    backup_path: Path,
    yes: bool = False,
) -> int:
    backup_path = Path(backup_path)
    if not backup_path.exists():
        print(f"agmind restore: file not found: {backup_path}", file=sys.stderr)
        return 2

    try:
        metadata = read_metadata(backup_path)
    except (ValueError, OSError) as exc:
        print(f"agmind restore: {exc}", file=sys.stderr)
        return 1

    included = metadata.get("included", [])
    print(f"agmind restore: backup from {metadata.get('created_at', '?')}")
    print(f"  format v{metadata.get('format_version', '?')}")
    print(f"  includes: {', '.join(included) or '<none>'}")

    if not yes:
        try:
            answer = input("Proceed restore? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    try:
        result = restore_backup(backup_path=backup_path)
    except Exception as exc:  # noqa: BLE001
        print(f"agmind restore: failed: {exc}", file=sys.stderr)
        return 1

    print(f"✓ restored {len(result.extracted)}: {', '.join(result.extracted) or '<none>'}")
    return 0


__all__ = [
    "BACKUP_INSTALL_DIR",  # re-export для backwards compat / tests
    "cmd_backup",
    "cmd_logs",
    "cmd_restore",
    "cmd_shell",
]
