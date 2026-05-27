"""Phase L.E: thin CLI wrappers for logs / shell / backup / restore.

Бизнес-логика живёт в `agmind/ops/` — здесь только парсинг аргументов и
форматирование вывода.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from agmind.ops.backup import (
    DEFAULT_INSTALL_DIR as BACKUP_INSTALL_DIR,
)
from agmind.ops.backup import (
    DEFAULT_SYSTEM_DIR,
    DEFAULT_USER_DIR,
    BackupResult,
    create_backup,
    default_sources,
    read_metadata,
    restore_backup,
)
from agmind.ops.exec import logs as do_logs
from agmind.ops.exec import shell as do_shell


def _prompt_sudo_password(ask_sudo_password: bool) -> str | None:
    if not ask_sudo_password:
        return None
    return getpass.getpass("sudo password: ")


def _running_compose_services(install_dir: Path) -> list[str]:
    """Return list of running services if compose deployment is up. Empty otherwise."""
    if shutil.which("docker") is None:
        return []
    compose_file = install_dir / "docker-compose.yml"
    if not compose_file.exists():
        return []
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps", "--services", "--filter", "status=running"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [s for s in proc.stdout.splitlines() if s.strip()]


def cmd_logs(
    service: str | None,
    install_dir: Path,
    tail: int,
    follow: bool,
    ask_sudo_password: bool = False,
) -> int:
    return do_logs(
        install_dir=install_dir,
        service=service,
        tail=tail,
        follow=follow,
        sudo_password=_prompt_sudo_password(ask_sudo_password),
    )


def cmd_shell(
    service: str,
    install_dir: Path,
    cmd: list[str] | None,
    workdir: str | None,
    ask_sudo_password: bool = False,
) -> int:
    return do_shell(
        install_dir=install_dir,
        service=service,
        cmd=cmd,
        workdir=workdir,
        sudo_password=_prompt_sudo_password(ask_sudo_password),
    )


def cmd_backup(
    output: Path,
    ask_sudo_password: bool = False,
) -> int:
    output = Path(output)
    if output.exists():
        print(f"agmind backup: refusing to overwrite existing {output}", file=sys.stderr)
        return 2
    try:
        result: BackupResult = create_backup(
            output_path=output,
            sudo_password=_prompt_sudo_password(ask_sudo_password),
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"agmind backup: {exc}", file=sys.stderr)
        return 1
    size_mb = result.bytes_written / (1024 * 1024)
    print(f"✓ backup written: {result.output_path} ({size_mb:.2f} MiB)")
    print(
        f"  included ({len(result.sources_included)}): {', '.join(result.sources_included) or '<none>'}"
    )
    if result.sources_missing:
        print(f"  missing  ({len(result.sources_missing)}): {', '.join(result.sources_missing)}")
    return 0


def cmd_restore(
    backup_path: Path,
    yes: bool = False,
    install_dir: Path = BACKUP_INSTALL_DIR,
    user_dir: Path = DEFAULT_USER_DIR,
    system_dir: Path = DEFAULT_SYSTEM_DIR,
    ask_sudo_password: bool = False,
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

    included_raw = metadata.get("included", [])
    included = [str(x) for x in included_raw] if isinstance(included_raw, list) else []
    print(f"agmind restore: backup from {metadata.get('created_at', '?')}")
    print(f"  format v{metadata.get('format_version', '?')}")
    print(f"  includes: {', '.join(included) or '<none>'}")

    # L.E.5: detect running deployment ДО overwrite — restore поверх работающего
    # compose'а гарантированно ломает container'ы (compose файл меняется на лету).
    running = _running_compose_services(install_dir)
    if running:
        print(f"\nWARNING: deployment at {install_dir} has {len(running)} running services:")
        print(f"  {', '.join(running)}")
        print("Restore поверх работающего compose может сломать containers.")
        print("Рекомендуется: `docker compose -f docker-compose.yml down` сначала.")

    if not yes:
        try:
            answer = input("Proceed restore? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    sources = default_sources(install_dir=install_dir, user_dir=user_dir, system_dir=system_dir)
    try:
        result = restore_backup(
            backup_path=backup_path,
            sources=sources,
            sudo_password=_prompt_sudo_password(ask_sudo_password),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"agmind restore: failed: {exc}", file=sys.stderr)
        return 1

    print(f"✓ restored {len(result.extracted)}: {', '.join(result.extracted) or '<none>'}")

    # L.E.1: hint про cf_dns_api_token — он не в backup'е, secret.
    token_path = user_dir / "cf_dns_api_token"
    if not token_path.exists():
        print(
            "\nNOTE: cf_dns_api_token не восстановлен (secret НЕ в backup'е). Восстанови вручную:"
        )
        print(f'  echo "$TOKEN" > {token_path} && chmod 600 {token_path}')

    # L.E.4: warn если каталог моделей пуст после restore
    models_dir = system_dir / "models"
    has_models = models_dir.exists() and any(
        p.suffix in (".gguf", ".safetensors", ".bin") for p in models_dir.iterdir()
    )
    if not has_models:
        print(
            f"\nWARNING: {models_dir} is empty — models не в backup'е (большие). "
            f"Run `agmind models pull <name>` чтобы заполнить."
        )

    return 0


def cmd_root_owned_backup_smoke(
    root: Path,
    output: Path,
    dry_run: bool = False,
    keep: bool = False,
) -> int:
    from agmind.ops import root_owned_backup_smoke

    argv = ["--root", str(root), "--output", str(output)]
    if dry_run:
        argv.append("--dry-run")
    if keep:
        argv.append("--keep")
    return root_owned_backup_smoke.main(argv)


__all__ = [
    "BACKUP_INSTALL_DIR",  # re-export для backwards compat / tests
    "cmd_backup",
    "cmd_logs",
    "cmd_restore",
    "cmd_root_owned_backup_smoke",
    "cmd_shell",
]
