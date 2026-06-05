"""Phase L.E: thin CLI wrappers for logs / shell / backup / restore.

Бизнес-логика живёт в `agmind/ops/` — здесь только парсинг аргументов и
форматирование вывода.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

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
    restore_plan,
    verify_backup,
    volume_restore_target,
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
    *,
    include_data: bool = False,
    install_dir: Path = BACKUP_INSTALL_DIR,
) -> int:
    output = Path(output)
    if output.exists():
        print(f"agmind backup: refusing to overwrite existing {output}", file=sys.stderr)
        return 2
    data_sources = None
    if include_data:
        from agmind.core.env import parse_env_file_or_empty
        from agmind.ops.backup_data import data_sources as enumerate_data_sources
        from agmind.services.renderer import load_descriptors

        descriptors = load_descriptors()
        services = _running_compose_services(install_dir)
        env_path = install_dir / ".env"
        # Don't crash the whole backup if .env is root-owned + unreadable without sudo — the
        # config tier still backs up; a DB dump that then lacks its password fails visibly.
        env = parse_env_file_or_empty(env_path)
        data_sources = enumerate_data_sources(services, descriptors, env)
    sudo_password = _prompt_sudo_password(ask_sudo_password)
    if include_data and sudo_password is None:
        # The /var/lib/agmind/* data dirs are typically root-owned; without a sudo password the
        # local tar path raises PermissionError and aborts the WHOLE backup after the slow config
        # portion. Warn up-front (world-readable data may still succeed, so don't fail-fast).
        print(
            "agmind backup: --include-data has no sudo password; root-owned /var/lib/agmind/* "
            "data dirs may abort the backup — re-run with --ask-sudo-password if it fails.",
            file=sys.stderr,
        )
    try:
        result: BackupResult = create_backup(
            output_path=output,
            sudo_password=sudo_password,
            data_sources=data_sources,
        )
    except PermissionError as exc:
        # /opt/agmind/.env and other artifacts are root-owned (0600); backing them up needs
        # sudo. Guide the operator instead of just echoing the raw errno.
        hint = "" if ask_sudo_password else " — re-run with --ask-sudo-password"
        print(f"agmind backup: cannot read a root-owned source ({exc}){hint}", file=sys.stderr)
        return 1
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


def cmd_verify_backup(backup_path: Path) -> int:
    issues = verify_backup(Path(backup_path))
    if issues:
        print(f"✗ backup verify FAILED ({len(issues)} issue(s)):", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(f"✓ backup OK: {backup_path}")
    return 0


def cmd_restore(
    backup_path: Path,
    yes: bool = False,
    install_dir: Path = BACKUP_INSTALL_DIR,
    user_dir: Path = DEFAULT_USER_DIR,
    system_dir: Path = DEFAULT_SYSTEM_DIR,
    ask_sudo_password: bool = False,
    dry_run: bool = False,
    labels: list[str] | None = None,
    skip_verify: bool = False,
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

    # Validate --label values up-front (a typo'd label would otherwise silently
    # restore nothing, since restore_backup skips labels with no destination).
    if labels:
        unknown = [lbl for lbl in labels if lbl not in included]
        if unknown:
            print(
                f"agmind restore: unknown label(s): {', '.join(unknown)}; "
                f"available: {', '.join(included) or '<none>'}",
                file=sys.stderr,
            )
            return 2

    if dry_run:
        rows = restore_plan(
            backup_path,
            install_dir=install_dir,
            user_dir=user_dir,
            system_dir=system_dir,
            labels=labels,
        )
        print("\nRestore plan (dry-run):")
        for row in rows:
            print(f"  {row.label:<14} {row.kind:<8} {row.target or '<no target>'}  ({row.detail})")
        print("\nno changes made (dry-run).")
        return 0

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

    # Integrity gate (audit M#17): re-hash the backup's dump members against their recorded
    # sha256 BEFORE overwriting live data — a bit-rotted/truncated archive must not be loaded.
    if not skip_verify:
        issues = verify_backup(backup_path)
        if issues:
            print("agmind restore: backup integrity check FAILED:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            print("aborting (pass --skip-verify to override).", file=sys.stderr)
            return 1

    sources = default_sources(install_dir=install_dir, user_dir=user_dir, system_dir=system_dir)
    if labels:
        wanted = set(labels)
        sources = [s for s in sources if s.label in wanted]

    # Route every volume/<svc> data member to its trusted system_dir destination. default_sources
    # carries no volume/* label, so without this restore_backup drops every volume member silently
    # while printing "✓ restored" (review HIGH restore-volume-data-unreachable). Destinations come
    # ONLY from the local system_dir + the label suffix — never the archive's host_path (audit H#4).
    destinations: dict[str, Path] = {}
    raw_data = metadata.get("data", [])
    db_passwords: dict[str, str] = {}
    from agmind.core.env import parse_env_file

    env_path = install_dir / ".env"
    env = parse_env_file(env_path) if env_path.exists() else {}
    for member in raw_data if isinstance(raw_data, list) else []:
        if not isinstance(member, dict):
            continue
        dlabel = str(member.get("label", ""))
        if member.get("kind") == "volume":
            vol_target = volume_restore_target(dlabel, system_dir)
            if vol_target is not None:
                destinations[dlabel] = vol_target
        elif member.get("kind") == "dbdump":
            # Wire the live DB password so a mysql restore has MYSQL_PWD (postgres uses
            # in-container trust). Read from the current .env, not the archive.
            if member.get("engine") == "mysql":
                db_passwords[dlabel] = env.get("MYSQL_ROOT_PASSWORD", "")
            elif member.get("engine") == "postgres":
                db_passwords[dlabel] = env.get("POSTGRES_PASSWORD", "")

    try:
        result = restore_backup(
            backup_path=backup_path,
            sources=sources,
            destinations=destinations,
            sudo_password=_prompt_sudo_password(ask_sudo_password),
            db_passwords=db_passwords,
            labels=labels,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"agmind restore: failed: {exc}", file=sys.stderr)
        return 1

    if result.failed:
        print(
            f"✗ {len(result.failed)} member(s) FAILED to restore: {', '.join(result.failed)}",
            file=sys.stderr,
        )
    print(f"✓ restored {len(result.extracted)}: {', '.join(result.extracted) or '<none>'}")
    if result.failed:
        return 1

    # A selective (--label) restore skips the whole-deployment hints below — they
    # only apply to a full restore.
    if labels:
        return 0

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


def _force_recreate(install_dir: Path, services: list[str], run: object | None = None) -> int:
    runner = run or subprocess.run
    cmd = [
        "docker",
        "compose",
        "-f",
        str(install_dir / "docker-compose.yml"),
        "up",
        "-d",
        "--force-recreate",
        *services,
    ]
    result = runner(  # type: ignore[operator]
        cmd, cwd=str(install_dir), capture_output=True, text=True, check=False
    )
    return int(result.returncode)


def cmd_rotate_secrets(
    install_dir: Path = BACKUP_INSTALL_DIR,
    *,
    include: list[str] | None = None,
    force_destructive: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    recreate: bool = True,
    timestamp: str | None = None,
    compose_run: object | None = None,
) -> int:
    from agmind.core.env import parse_env_file
    from agmind.core.secrets import write_private_text
    from agmind.ops.rotate import (
        apply_rotation,
        holders_for,
        plan_rotation,
        rewrite_env_text,
        secret_consumers,
    )

    env_path = install_dir / ".env"
    if not env_path.is_file():
        print(f"agmind rotate-secrets: no .env found at {env_path}", file=sys.stderr)
        return 2

    text = env_path.read_text(encoding="utf-8")
    env = parse_env_file(env_path)
    plan = plan_rotation(env, include=include or [], force_destructive=force_destructive)

    print("rotate-secrets plan:")
    print(f"  rotate ({len(plan.rotate)}): {', '.join(plan.rotate) or '<none>'}")
    if plan.skipped_init:
        print(f"  skipped INIT-ONLY (need --include): {', '.join(plan.skipped_init)}")
    if plan.refused_encrypt:
        print(
            f"  refused ENCRYPT-AT-REST (need --force-destructive): {', '.join(plan.refused_encrypt)}"
        )
    for warning in plan.warnings:
        print(f"  ! {warning}")

    if dry_run:
        print("no changes made (dry-run).")
        return 0
    if not plan.rotate:
        print("nothing to rotate.")
        return 0

    if not yes:
        try:
            answer = input("Proceed rotation? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    ts = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = env_path.with_name(f".env.pre-rotation.{ts}")
    write_private_text(backup_path, text)

    new_env = apply_rotation(env, plan)
    rewritten = rewrite_env_text(text, {key: new_env[key] for key in plan.rotate})
    write_private_text(env_path, rewritten)
    print(f"✓ rotated {len(plan.rotate)} secret(s); old .env backed up at {backup_path}")

    if not recreate:
        print(
            "skipped recreate (--no-recreate). Run "
            "`docker compose up -d --force-recreate <holders>` (NOT restart — that keeps the old env)."
        )
        return 0

    from agmind.services.renderer import load_descriptors

    holders = holders_for(plan.rotate, secret_consumers(load_descriptors()))
    running = set(_running_compose_services(install_dir))
    to_recreate = [h for h in holders if h in running]
    if not to_recreate:
        print("no running holders to recreate.")
        return 0
    rc = _force_recreate(install_dir, to_recreate, run=compose_run)
    if rc != 0:
        print(
            f"WARNING: force-recreate rc={rc}; recreate manually: "
            f"docker compose up -d --force-recreate {' '.join(to_recreate)}",
            file=sys.stderr,
        )
        return 1
    print(f"✓ force-recreated {len(to_recreate)} holder(s): {', '.join(to_recreate)}")
    return 0


def cmd_dr_drill(
    install_dir: Path = BACKUP_INSTALL_DIR,
    *,
    user_dir: Path = DEFAULT_USER_DIR,
    system_dir: Path = DEFAULT_SYSTEM_DIR,
    skip_restore: bool = True,
    as_json: bool = False,
    ask_sudo_password: bool = False,
) -> int:
    import json
    import tempfile

    from agmind.ops.backup import create_backup, default_sources, restore_backup, verify_backup
    from agmind.ops.dr_drill import run_drill

    if not (install_dir / "docker-compose.yml").exists() and not (install_dir / ".env").exists():
        print(f"agmind dr-drill: no deployment found at {install_dir}", file=sys.stderr)
        return 2

    sudo = _prompt_sudo_password(ask_sudo_password)
    with tempfile.TemporaryDirectory(prefix="agmind-drdrill-") as tmp:
        tmpdir = Path(tmp)
        archive_path = tmpdir / "drill-backup.tar.gz"
        sandbox = tmpdir / "sandbox"
        for sub in ("opt", "user", "system"):
            (sandbox / sub).mkdir(parents=True, exist_ok=True)

        def _backup() -> Path:
            create_backup(
                output_path=archive_path,
                sources=default_sources(install_dir, user_dir, system_dir),
                sudo_password=sudo,
            )
            return archive_path

        def _restore(path: Path) -> list[str]:
            # Restore into a throwaway sandbox — NEVER the live install.
            result = restore_backup(
                backup_path=path,
                sources=default_sources(sandbox / "opt", sandbox / "user", sandbox / "system"),
            )
            return list(result.extracted)

        report = run_drill(
            backup_fn=_backup,
            verify_fn=verify_backup,
            restore_fn=_restore,
            skip_restore=skip_restore,
        )

    if as_json:
        print(json.dumps(report.to_payload(), indent=2, ensure_ascii=False))
    else:
        print("DR drill:")
        for step in report.steps:
            print(f"  [{'OK ' if step.ok else 'FAIL'}] {step.name:<16} {step.detail}")
        print(f"  RTO: {report.rto_seconds:.2f}s  ->  {'PASS' if report.ok else 'FAIL'}")
        if skip_restore:
            print("  (live restore + health skipped; pass --no-skip-restore on the deploy host)")
    return 0 if report.ok else 1


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


def register(app: typer.Typer) -> None:
    """Attach logs/shell/backup/restore commands and the ``ops`` group to ``app``."""

    # ---- ops subcommands: logs / shell / backup / restore (Phase L.E) ----
    @app.command()
    def logs(
        service: str | None = typer.Argument(None, help="Service name (omit для всех сервисов)."),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        tail: int = typer.Option(200, "--tail", help="Initial backlog lines."),
        follow: bool = typer.Option(False, "-f", "--follow", help="Stream new logs."),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install paths",
        ),
    ) -> None:
        """Stream docker compose logs."""
        raise typer.Exit(
            code=cmd_logs(
                service,
                install_dir,
                tail,
                follow,
                ask_sudo_password=ask_sudo_password,
            )
        )

    @app.command()
    def shell(
        service: str = typer.Argument(..., help="Service name."),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        cmd: str = typer.Option("/bin/sh", "--cmd", help="Command to run (default /bin/sh)."),
        workdir: str | None = typer.Option(
            None, "--workdir", "-w", help="Working dir внутри container."
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install paths",
        ),
    ) -> None:
        """Open shell inside running service container (docker compose exec)."""
        import shlex

        cmd_argv = shlex.split(cmd) if cmd else None
        raise typer.Exit(
            code=cmd_shell(
                service,
                install_dir,
                cmd_argv,
                workdir,
                ask_sudo_password=ask_sudo_password,
            )
        )

    @app.command()
    def backup(
        output: Path = typer.Option(
            ...,
            "--output",
            "-o",
            help="Path для .tar.gz (e.g. agmind-2026-05-20.tar.gz).",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
        include_data: bool = typer.Option(
            False,
            "--include-data",
            help="Also back up the DATA tier: postgres/mysql logical dumps + "
            "/var/lib/agmind/* volume dirs of the running services (needs --ask-sudo-password "
            "for root-owned data dirs).",
        ),
    ) -> None:
        """Create tar.gz backup of compose / .env / state / snapshots (Phase L.E)."""
        raise typer.Exit(
            code=cmd_backup(output, ask_sudo_password=ask_sudo_password, include_data=include_data)
        )

    @app.command("backup-verify")
    def backup_verify(
        backup_file: Path = typer.Argument(..., help="Path to a .tar.gz backup to verify."),
    ) -> None:
        """Verify a backup archive: it opens and every data-member checksum matches (DR pre-check)."""
        raise typer.Exit(code=cmd_verify_backup(backup_file))

    @app.command()
    def restore(
        backup_file: Path = typer.Argument(..., help="Path to .tar.gz backup."),
        yes: bool = typer.Option(False, "-y", "--yes", help="Skip interactive confirmation."),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Print the restore plan and exit without changing anything."
        ),
        label: list[str] | None = typer.Option(
            None,
            "--label",
            help="Restore only these config categories (repeatable; e.g. --label env "
            "--label descriptors). Default: all included.",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
        skip_verify: bool = typer.Option(
            False,
            "--skip-verify",
            help="Skip the pre-restore sha256 integrity check (NOT recommended).",
        ),
    ) -> None:
        """Restore deployment from `agmind backup` archive (Phase L.E)."""
        raise typer.Exit(
            code=cmd_restore(
                backup_file,
                yes=yes,
                ask_sudo_password=ask_sudo_password,
                dry_run=dry_run,
                labels=label,
                skip_verify=skip_verify,
            )
        )

    ops_app = typer.Typer(
        name="ops",
        help="Run day-2 operator helpers.",
        no_args_is_help=True,
    )
    ops_smoke_app = typer.Typer(
        name="smoke",
        help="Run non-destructive operator smoke checks.",
        no_args_is_help=True,
    )
    ops_app.add_typer(ops_smoke_app)
    app.add_typer(ops_app)

    @ops_app.command("rotate-secrets")
    def ops_rotate_secrets(
        install_dir: Path = typer.Option(
            BACKUP_INSTALL_DIR, "--install-dir", help="Deployment dir holding .env."
        ),
        include: list[str] | None = typer.Option(
            None, "--include", help="Also rotate these INIT-ONLY keys (then run the in-DB reset)."
        ),
        force_destructive: bool = typer.Option(
            False,
            "--force-destructive",
            help="Rotate ENCRYPT-AT-REST keys (DESTROYS existing data).",
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; change nothing."),
        yes: bool = typer.Option(False, "-y", "--yes", help="Skip interactive confirmation."),
        no_recreate: bool = typer.Option(
            False, "--no-recreate", help="Rewrite .env but do not force-recreate holders."
        ),
    ) -> None:
        """Rotate runtime secrets in .env, then force-recreate their holders (4-bucket safety)."""
        raise typer.Exit(
            code=cmd_rotate_secrets(
                install_dir,
                include=include or [],
                force_destructive=force_destructive,
                dry_run=dry_run,
                yes=yes,
                recreate=not no_recreate,
            )
        )

    @ops_app.command("dr-drill")
    def ops_dr_drill(
        install_dir: Path = typer.Option(
            BACKUP_INSTALL_DIR, "--install-dir", help="Deployment dir to drill."
        ),
        user_dir: Path = typer.Option(DEFAULT_USER_DIR, "--user-dir", help="User state dir."),
        system_dir: Path = typer.Option(
            DEFAULT_SYSTEM_DIR, "--system-dir", help="System data dir."
        ),
        no_skip_restore: bool = typer.Option(
            False,
            "--no-skip-restore",
            help="Also run the LIVE restore + health check (host-only; mutates the stack).",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
        ask_sudo_password: bool = typer.Option(
            False, "--ask-sudo-password", help="Prompt for sudo for root-owned paths."
        ),
    ) -> None:
        """DR drill: backup → integrity → sandbox-restore → measure RTO (live restore gated)."""
        raise typer.Exit(
            code=cmd_dr_drill(
                install_dir,
                user_dir=user_dir,
                system_dir=system_dir,
                skip_restore=not no_skip_restore,
                as_json=as_json,
                ask_sudo_password=ask_sudo_password,
            )
        )

    @ops_smoke_app.command("backup-root-owned")
    def ops_smoke_backup_root_owned(
        root: Path = typer.Option(
            Path("/tmp/agmind-root-owned-smoke"),
            "--root",
            help="Temporary smoke root under /tmp.",
        ),
        output: Path = typer.Option(
            Path("/tmp/agmind-root-owned-smoke.tar.gz"),
            "--output",
            "-o",
            help="Backup archive path under /tmp.",
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without sudo."),
        keep: bool = typer.Option(False, "--keep", help="Keep temporary smoke tree."),
    ) -> None:
        """Smoke backup/restore against root-owned temporary paths."""
        raise typer.Exit(
            code=cmd_root_owned_backup_smoke(
                root=root,
                output=output,
                dry_run=dry_run,
                keep=keep,
            )
        )


__all__ = [
    "BACKUP_INSTALL_DIR",  # re-export для backwards compat / tests
    "cmd_backup",
    "cmd_logs",
    "cmd_restore",
    "cmd_root_owned_backup_smoke",
    "cmd_shell",
    "cmd_verify_backup",
    "register",
]
