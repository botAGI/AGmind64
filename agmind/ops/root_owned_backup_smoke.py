"""Non-destructive backup/restore smoke for root-owned temporary paths."""

from __future__ import annotations

import argparse
import getpass
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from agmind.ops.backup import BackupSource, create_backup, restore_backup

DEFAULT_ROOT = Path("/tmp/agmind-root-owned-smoke")
DEFAULT_OUTPUT = Path("/tmp/agmind-root-owned-smoke.tar.gz")


def _under_tmp(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path("/tmp").resolve())
    except ValueError:
        return False
    return True


def _validate_tmp_root(path: Path) -> Path:
    resolved = path.resolve()
    if not _under_tmp(resolved):
        raise ValueError(f"root must be under /tmp: {path}")
    if resolved == Path("/tmp").resolve():
        raise ValueError(f"root must be a dedicated child under /tmp: {path}")
    return resolved


def _sudo_run(cmd: list[str], sudo_password: str) -> None:
    result = subprocess.run(
        ["sudo", "-S", "-p", "", "--", *cmd],
        capture_output=True,
        text=True,
        check=False,
        input=f"{sudo_password}\n",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or str(result.returncode)).strip()
        raise RuntimeError(f"sudo command failed ({cmd[0]}): {detail}")


def _sudo_install_text(path: Path, text: str, mode: str, sudo_password: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="agmind-root-owned-smoke-",
        dir="/tmp",
        delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        _sudo_run(["install", "-D", "-m", mode, str(tmp), str(path)], sudo_password)
    finally:
        tmp.unlink(missing_ok=True)


def _sources(root: Path) -> list[BackupSource]:
    return [
        BackupSource("compose", root / "install" / "docker-compose.yml"),
        BackupSource("env", root / "install" / ".env"),
        BackupSource("descriptors", root / "install" / "templates" / "services"),
        BackupSource("setup_state", root / "user" / "setup-state.json"),
        BackupSource("schema_state", root / "user" / "schema.json"),
        BackupSource("snapshots", root / "system" / "snapshots"),
    ]


def _prepare_root_owned_tree(root: Path, sudo_password: str) -> None:
    _sudo_run(["rm", "-rf", "--one-file-system", str(root)], sudo_password)
    _sudo_run(
        [
            "install",
            "-d",
            "-m",
            "0755",
            str(root / "install" / "templates" / "services"),
            str(root / "system" / "snapshots" / "smoke-snapshot"),
            str(root / "user"),
        ],
        sudo_password,
    )
    _sudo_install_text(
        root / "install" / "docker-compose.yml",
        "services: {}\n",
        "0644",
        sudo_password,
    )
    _sudo_install_text(
        root / "install" / ".env",
        "POSTGRES_PASSWORD=placeholder\nAGMIND_DOMAIN=lab.example.com\n",
        "0600",
        sudo_password,
    )
    _sudo_install_text(
        root / "install" / "templates" / "services" / "smoke.yaml",
        "name: smoke\n",
        "0644",
        sudo_password,
    )
    _sudo_install_text(
        root / "user" / "setup-state.json", '{"domain":"lab.example.com"}\n', "0644", sudo_password
    )
    _sudo_install_text(
        root / "user" / "schema.json", '{"schema_version":1}\n', "0644", sudo_password
    )
    _sudo_install_text(
        root / "system" / "snapshots" / "smoke-snapshot" / "meta.json",
        "{}\n",
        "0644",
        sudo_password,
    )


def _print_plan(root: Path, output: Path) -> None:
    restore_root = root / "restore-target"
    print("root-owned backup smoke dry-run")
    print(f"root:    {root}")
    print(f"output:  {output}")
    print(f"restore: {restore_root}")
    print("sources:")
    for source in _sources(root):
        print(f"  - {source.label}: {source.path}")


def _run_smoke(root: Path, output: Path, keep: bool) -> int:
    if shutil.which("sudo") is None:
        print("ERROR: sudo not found", file=sys.stderr)
        return 2

    try:
        sudo_password = getpass.getpass("sudo password for root-owned backup smoke: ")
    except (EOFError, KeyboardInterrupt):
        print("ERROR: sudo password prompt aborted", file=sys.stderr)
        return 2
    if not sudo_password:
        print("ERROR: empty sudo password", file=sys.stderr)
        return 2

    restore_root = root / "restore-target"
    try:
        output.unlink(missing_ok=True)
        _prepare_root_owned_tree(root, sudo_password)
        result = create_backup(
            output_path=output,
            sources=_sources(root),
            sudo_password=sudo_password,
        )
        restore_backup(
            backup_path=output,
            sources=_sources(root),
            destinations={
                "compose": restore_root / "install" / "docker-compose.yml",
                "env": restore_root / "install" / ".env",
                "descriptors": restore_root / "install" / "templates" / "services",
                "setup_state": restore_root / "user" / "setup-state.json",
                "schema_state": restore_root / "user" / "schema.json",
                "snapshots": restore_root / "system" / "snapshots",
            },
            sudo_password=sudo_password,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: root-owned backup smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not keep:
            try:
                _sudo_run(["rm", "-rf", "--one-file-system", str(root)], sudo_password)
            except Exception as exc:  # noqa: BLE001
                print(f"WARNING: cleanup failed: {exc}", file=sys.stderr)

    print(
        "root-owned backup smoke OK: "
        f"{result.output_path} ({result.bytes_written} bytes), "
        f"{len(result.sources_included)} sources"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exercise AGmind backup/restore against root-owned temp paths.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep", action="store_true", help="Keep temporary smoke tree.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without sudo.")
    args = parser.parse_args(argv)

    try:
        root = _validate_tmp_root(args.root)
        output = args.output.resolve()
        if not _under_tmp(output):
            raise ValueError(f"output must be under /tmp: {args.output}")
    except ValueError as exc:
        parser.error(str(exc))

    if args.dry_run:
        _print_plan(root, output)
        return 0
    return _run_smoke(root, output, keep=args.keep)


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_ROOT",
    "main",
]
