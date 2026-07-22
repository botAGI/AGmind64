"""Phase L.E: thin CLI wrappers for logs / shell / backup / restore.

Бизнес-логика живёт в `agmind/ops/` — здесь только парсинг аргументов и
форматирование вывода.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from agmind.core.locks import deploy_lock
from agmind.ops.backup import (
    DEFAULT_INSTALL_DIR as BACKUP_INSTALL_DIR,
)
from agmind.ops.backup import (
    DEFAULT_SYSTEM_DIR,
    DEFAULT_USER_DIR,
    BackupEncryptError,
    BackupResult,
    create_backup,
    default_sources,
    list_backups,
    read_metadata,
    restore_backup,
    restore_plan,
    verify_backup,
    volume_restore_target,
    write_data_backup_marker,
)
from agmind.ops.exec import logs as do_logs
from agmind.ops.exec import shell as do_shell
from agmind.ops.offhost import OffHostPushError, push_backup, resolve_remote, which_rclone

# ---- opt-in backup systemd timer (SPEC-16.5) --------------------------------
# The scheduled backup writes into /var/lib/agmind/backups (matching the image
# default data root; a *_stack gc never touches a real path there). The unit
# files ship as filled templates under templates/systemd/ and land in the system
# unit dir; only `agmind ops backup-timer --install/--uninstall` provisions them —
# they are NEVER part of default_steps() (opt-in, per SPEC-16.5).
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
BACKUP_SERVICE_UNIT = "agmind-backup.service"
BACKUP_TIMER_UNIT = "agmind-backup.timer"
DEFAULT_BACKUP_OUTPUT_DIR = DEFAULT_SYSTEM_DIR / "backups"
_SERVICE_EXECSTART_TOKEN = "__AGMIND_BACKUP_EXECSTART__"
_TIMER_ONCALENDAR_TOKEN = "__AGMIND_BACKUP_ONCALENDAR__"
# systemd calendar shorthands passed through verbatim; anything else is treated
# as a raw OnCalendar expression (e.g. "*-*-* 03:00:00").
_ONCALENDAR_ALIASES = frozenset(
    {"minutely", "hourly", "daily", "weekly", "monthly", "quarterly", "semiannually", "yearly"}
)


def _prompt_sudo_password(ask_sudo_password: bool) -> str | None:
    if not ask_sudo_password:
        return None
    return getpass.getpass("sudo password: ")


def _compose_project_name(install_dir: Path) -> str:
    """Compose project name WITHOUT reading .env — the rendered compose file's top-level
    ``name:`` if present, else the install-dir basename (docker compose's default)."""
    compose_file = install_dir / "docker-compose.yml"
    try:
        for line in compose_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                value = line.split(":", 1)[1].strip().strip("'\"")
                if value:
                    return value
            elif line and not line[0].isspace() and not line.startswith("#"):
                # left the top-level header region without finding name: → use the default
                break
    except OSError:
        pass
    return install_dir.name


def _running_compose_services(install_dir: Path) -> list[str]:
    """Running services of the deployed compose project — WITHOUT reading .env.

    `docker compose ps` loads the project .env (root:root 0600 on a real deploy) and exits
    non-zero for a non-root operator → this used to return [] → a `--include-data` backup then
    silently captured ZERO data and reported success (live-audit 2026-06-05 HIGH
    backup-include-data-empty). Query the daemon by compose-project label instead.
    """
    if shutil.which("docker") is None:
        return []
    compose_file = install_dir / "docker-compose.yml"
    if not compose_file.exists():
        return []
    project = _compose_project_name(install_dir)
    try:
        proc = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                '{{.Label "com.docker.compose.service"}}',
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return sorted({s.strip() for s in proc.stdout.splitlines() if s.strip()})


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
    remote: str | None = None,
    encrypt: bool = False,
    age_recipient: str | None = None,
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
        if not services:
            # Fail LOUDLY: a --include-data backup with zero enumerated services would write a
            # config-only archive that LOOKS complete but contains no DB dumps / volume tars →
            # silent total data loss on restore (live-audit 2026-06-05 HIGH). Never pretend.
            print(
                "agmind backup: --include-data but no running services found for compose "
                f"project '{_compose_project_name(install_dir)}' — refusing to write a backup "
                "that captures ZERO data. Start the stack first, or drop --include-data for a "
                "config-only backup.",
                file=sys.stderr,
            )
            return 1
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
    # Pass the encryption knobs only when --encrypt is set so a plain backup keeps the exact same
    # create_backup call surface (age params are inert defaults otherwise).
    encrypt_kwargs: dict[str, Any] = (
        {"encrypt": True, "age_recipient": age_recipient} if encrypt else {}
    )
    try:
        result: BackupResult = create_backup(
            output_path=output,
            sudo_password=sudo_password,
            data_sources=data_sources,
            **encrypt_kwargs,
        )
    except BackupEncryptError as exc:
        # age missing / no recipient / age run failed — actionable message, never a traceback
        # (mirrors the off-host push error path below).
        print(f"agmind backup: {exc}", file=sys.stderr)
        return 1
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
    if not encrypt and "env" in result.sources_included:
        # SPEC-17.4: the archive embeds /opt/agmind/.env — runtime env + generated service
        # secrets — in PLAINTEXT (gzip only, no encryption). Anyone who can read the .tar.gz
        # reads every secret. Warn loudly so the operator either encrypts at rest (--encrypt) or
        # keeps the archive on trusted storage / pushes only to a trusted off-host remote.
        print(
            "  NOTE: this archive contains /opt/agmind/.env (runtime secrets) in PLAINTEXT "
            "(gzip only, NOT encrypted). Re-run with --encrypt --age-recipient <age1...> to "
            "encrypt it at rest, or keep it on trusted storage / push only to a trusted "
            "off-host remote.",
            file=sys.stderr,
        )
    if include_data:
        # D-06: stamp the data-backup marker on the SUCCESS path only (after create_backup
        # returned, before this function returns 0) — the deploy stateful-apply guard reads it
        # to prove a recent --include-data backup exists. sha256 is chunked (models_cmd's
        # _file_sha256) since the archive may be multi-GB with data sources included. Best
        # effort: a marker-stamp failure (e.g. archive vanished) must not turn an already
        # successful backup into a reported failure.
        try:
            from agmind.cli.models_cmd import _file_sha256

            sha256 = _file_sha256(result.output_path)
            write_data_backup_marker(install_dir, result.output_path, sha256)
        except OSError as exc:
            print(
                f"agmind backup: could not stamp data-backup marker ({exc}) — backup itself "
                "succeeded, but the stateful-apply guard will not see it as fresh.",
                file=sys.stderr,
            )
    if remote:
        # SPEC-16.5 off-host push: only on an EXPLICIT --remote (predictable manual
        # behaviour — a plain `agmind backup` never silently ships the archive
        # off-host). The scheduled path bakes the resolved remote into the timer's
        # ExecStart at install time, so it reaches here as an explicit value too.
        try:
            target = push_backup(result.output_path, remote)
        except OffHostPushError as exc:
            print(
                f"agmind backup: off-host push FAILED ({exc}) — the LOCAL backup at "
                f"{result.output_path} is intact.",
                file=sys.stderr,
            )
            return 1
        print(f"✓ pushed off-host: {target}")
        if not encrypt:
            # live-run 2026-07-21: rclone does NOT carry the local 0600 mode across — a
            # config-only archive written 0600 landed 0644 on a local remote, i.e.
            # world-readable secrets (.env) at rest. We cannot fix this generically:
            # every backend has its own permission model (S3/B2 ACLs, SFTP umask,
            # gdrive none), and rclone exposes no portable chmod. So say it plainly
            # instead of pretending the local mode protects the copy.
            print(
                "  NOTE: the off-host copy does NOT inherit the local 0600 mode — rclone "
                "applies the remote's own permission model (a local/SFTP remote typically "
                "lands 0644, i.e. readable by other users on that host). This archive is "
                "UNENCRYPTED: re-run with --encrypt --age-recipient <age1...> so the copy "
                "at rest is safe regardless of remote permissions.",
                file=sys.stderr,
            )
    if not include_data:
        # Make the config-only scope LOUD: this archive contains NO database dumps / volume
        # data, so a restore from it cannot recover any stateful store (live-audit 2026-06-05
        # default-backup-no-data-tier-silent).
        print(
            "  NOTE: config-only backup — NO database/volume data was captured. Re-run with "
            "--include-data (and --ask-sudo-password) to protect the stateful stores.",
            file=sys.stderr,
        )
    return 0


def _systemd_template(name: str) -> str:
    """Read a shipped systemd unit template (templates/systemd/<name>) via data_root()."""
    from agmind.core.paths import data_root

    return (data_root() / "templates" / "systemd" / name).read_text(encoding="utf-8")


def _resolve_agmind_bin() -> str:
    """Absolute command that invokes the SAME agmind the operator ran.

    Prefer the running console-script (``sys.argv[0]`` when it is ``agmind``) so a
    repo-.venv install is used (a global shim rots post-wipe — feedback
    install-from-repo-venv), then the ``agmind`` on PATH, then a module invocation
    via the current interpreter. The result is embedded in the timer's ExecStart,
    which runs with systemd's minimal PATH, so an absolute path matters.
    """
    argv0 = Path(sys.argv[0])
    if argv0.name == "agmind" and argv0.exists():
        return str(argv0.resolve())
    found = shutil.which("agmind")
    if found:
        return found
    return f"{sys.executable} -m agmind"


def _resolve_oncalendar(schedule: str) -> str:
    """Map ``--schedule`` to an ``OnCalendar=`` value (alias verbatim, else raw)."""
    text = schedule.strip()
    if not text:
        return "daily"
    if text.lower() in _ONCALENDAR_ALIASES:
        return text.lower()
    return text


def _backup_timer_execstart(
    agmind_bin: str,
    *,
    include_data: bool,
    remote: str | None,
    output_dir: Path,
) -> str:
    """Build the timer service ExecStart line.

    Wrapped in ``/bin/sh -c`` so the shell timestamps the output filename at run
    time — successive runs write a unique archive and never hit ``cmd_backup``'s
    refuse-to-overwrite guard. The ``%`` in the ``date`` format is doubled so
    systemd does not treat it as a unit specifier.

    NB: the backup subcommand is registered on the TOP-LEVEL app (``agmind
    backup``), NOT under ``agmind ops`` — only the timer/rotate/dr-drill helpers
    live under ``ops``. Emitting ``ops backup`` here would make every scheduled
    fire die with "No such command 'backup'"; the ExecStart mirrors the command
    that actually resolves.
    """
    parts = [agmind_bin, "backup"]
    if include_data:
        parts.append("--include-data")
    if remote:
        parts += ["--remote", remote]
    out = f"{output_dir}/agmind-backup-$(date -u +%%Y%%m%%dT%%H%%M%%SZ).tar.gz"
    parts += ["-o", out]
    return "/bin/sh -c '" + " ".join(parts) + "'"


def _run_maybe_sudo(args: list[str], sudo_password: str | None) -> subprocess.CompletedProcess[str]:
    """Run ``args`` directly (sudo_password is None → already root / writable dir) or via
    non-interactive ``sudo -S``. Captures text output; never raises on non-zero rc.

    Calls the module-level ``subprocess.run`` (monkeypatchable in tests — the same seam
    the k6 wrapper uses)."""
    if sudo_password is None:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    from agmind.core.proc import sudo_argv, sudo_stdin_text

    return subprocess.run(
        sudo_argv(args),
        capture_output=True,
        text=True,
        check=False,
        input=sudo_stdin_text(sudo_password),
    )


def _write_unit_file(dest: Path, content: str, sudo_password: str | None) -> None:
    """Place a unit body at ``dest`` (root:root 0644).

    Without a sudo password: a plain atomic local write — works when the operator
    can write the unit dir directly (root, or a test tmp dir); a non-root operator
    against /etc raises PermissionError, which the caller turns into a
    --ask-sudo-password hint. With a sudo password: stage to a user temp, then
    ``install`` it root:root 0644 (the GpuMetricsStep pattern).
    """
    if sudo_password is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, 0o644)
        tmp.replace(dest)
        return
    fd, tmp_name = tempfile.mkstemp(prefix=".agmind-backup-unit-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp_name, 0o644)
        result = _run_maybe_sudo(
            ["install", "-m", "0644", "-o", "root", "-g", "root", tmp_name, str(dest)],
            sudo_password,
        )
        if result.returncode != 0:
            raise OSError(
                f"install of {dest} failed (rc={result.returncode}): "
                f"{(result.stderr or '').strip() or 'no output'}"
            )
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def cmd_backup_timer(
    *,
    install: bool = False,
    uninstall: bool = False,
    schedule: str = "daily",
    remote: str | None = None,
    include_data: bool = True,
    ask_sudo_password: bool = False,
    install_dir: Path = BACKUP_INSTALL_DIR,
    output_dir: Path = DEFAULT_BACKUP_OUTPUT_DIR,
    unit_dir: Path = SYSTEMD_UNIT_DIR,
) -> int:
    """Install / uninstall the opt-in scheduled-backup systemd timer (SPEC-16.5).

    ``--install`` materializes ``agmind-backup.{service,timer}`` (filling the
    schedule + off-host remote), reloads systemd, and enables+starts the timer.
    ``--uninstall`` disables it and removes both units. This is OPT-IN — it is
    never part of the default install pipeline.
    """
    if install == uninstall:
        print(
            "agmind backup-timer: choose exactly one of --install / --uninstall",
            file=sys.stderr,
        )
        return 2

    sudo_password = _prompt_sudo_password(ask_sudo_password)
    unit_dir = Path(unit_dir)
    service_path = unit_dir / BACKUP_SERVICE_UNIT
    timer_path = unit_dir / BACKUP_TIMER_UNIT

    if uninstall:
        # disable --now stops + un-schedules; best-effort so a partially-installed
        # timer still cleans up. Then remove both unit files and reload systemd.
        _run_maybe_sudo(["systemctl", "disable", "--now", BACKUP_TIMER_UNIT], sudo_password)
        removed: list[str] = []
        try:
            for path in (timer_path, service_path):
                if sudo_password is None:
                    if path.exists():
                        path.unlink()
                        removed.append(path.name)
                else:
                    result = _run_maybe_sudo(["rm", "-f", str(path)], sudo_password)
                    if result.returncode != 0:
                        print(
                            f"agmind backup-timer: could not remove {path} "
                            f"(rc={result.returncode})",
                            file=sys.stderr,
                        )
                        return 1
                    removed.append(path.name)
        except PermissionError as exc:
            hint = "" if ask_sudo_password else " — re-run with --ask-sudo-password"
            print(
                f"agmind backup-timer: cannot remove unit files ({exc}){hint}",
                file=sys.stderr,
            )
            return 1
        _run_maybe_sudo(["systemctl", "daemon-reload"], sudo_password)
        print(f"✓ backup timer removed ({', '.join(removed) or 'nothing to remove'})")
        return 0

    # ---- install ----
    resolved_remote = resolve_remote(remote, install_dir=install_dir)
    if resolved_remote and which_rclone() is None:
        # Not fatal — rclone can be installed before the first scheduled run; but a
        # missing binary means the 3am push fails silently, so warn loudly now.
        print(
            "agmind backup-timer: WARNING off-host remote is set but rclone is not on PATH; "
            "install + configure it (https://rclone.org/install/ then `rclone config`) before "
            "the timer fires, or the off-host push will fail.",
            file=sys.stderr,
        )
    oncalendar = _resolve_oncalendar(schedule)
    execstart = _backup_timer_execstart(
        _resolve_agmind_bin(),
        include_data=include_data,
        remote=resolved_remote,
        output_dir=output_dir,
    )
    service_content = _systemd_template(BACKUP_SERVICE_UNIT).replace(
        _SERVICE_EXECSTART_TOKEN, execstart
    )
    timer_content = _systemd_template(BACKUP_TIMER_UNIT).replace(
        _TIMER_ONCALENDAR_TOKEN, oncalendar
    )

    # Ensure the backup output dir exists (root-owned 0700 — archives are 0600 but
    # the dir itself may hold .env-bearing tarballs, so keep it private).
    _run_maybe_sudo(["install", "-d", "-m", "0700", str(output_dir)], sudo_password)

    try:
        _write_unit_file(service_path, service_content, sudo_password)
        _write_unit_file(timer_path, timer_content, sudo_password)
    except PermissionError as exc:
        hint = "" if ask_sudo_password else " — re-run with --ask-sudo-password"
        print(
            f"agmind backup-timer: cannot write unit files to {unit_dir} ({exc}){hint}",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"agmind backup-timer: {exc}", file=sys.stderr)
        return 1

    reload_result = _run_maybe_sudo(["systemctl", "daemon-reload"], sudo_password)
    if reload_result.returncode != 0:
        print(
            f"agmind backup-timer: systemctl daemon-reload failed (rc={reload_result.returncode})",
            file=sys.stderr,
        )
        return 1
    enable_result = _run_maybe_sudo(
        ["systemctl", "enable", "--now", BACKUP_TIMER_UNIT], sudo_password
    )
    if enable_result.returncode != 0:
        print(
            f"agmind backup-timer: systemctl enable --now {BACKUP_TIMER_UNIT} failed "
            f"(rc={enable_result.returncode})",
            file=sys.stderr,
        )
        return 1

    print(f"✓ backup timer installed: {timer_path} (OnCalendar={oncalendar})")
    print(f"  service: {service_path}")
    off = f"  → off-host: {resolved_remote}" if resolved_remote else ""
    print(f"  backups: {output_dir}{off}")
    return 0


def _human_size(num_bytes: int) -> str:
    """Human-readable byte size (binary units), e.g. ``43.56 MiB``."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def cmd_backup_list(directory: Path, as_json: bool = False) -> int:
    """List agmind backup archives in ``directory`` (default: cwd), newest first.

    Text or ``--json`` output. Never extracts member contents, so no secret VALUES
    are read; only the archive path + cheap metadata are shown. An empty/absent dir
    prints a friendly note and exits 0.
    """
    import json

    entries = list_backups(Path(directory))

    if as_json:
        backups = [{k: v for k, v in e.items() if k != "mtime"} for e in entries]
        print(json.dumps({"backups": backups}, indent=2, ensure_ascii=False))
        return 0

    if not entries:
        print(f"no backups found in {directory}")
        return 0

    print(f"Backup archives in {directory}:")
    for e in entries:
        when = e.get("created_at") or "?"
        size_raw = e.get("size_bytes", 0)
        size = _human_size(int(size_raw) if isinstance(size_raw, int) else 0)
        if e.get("ok"):
            included = e.get("included") or []
            n_inc = len(included) if isinstance(included, list) else 0
            data_raw = e.get("data_members", 0)
            n_data = int(data_raw) if isinstance(data_raw, int) else 0
            status = f"({n_inc} included, {n_data} data)  [OK]"
        else:
            status = f"[CORRUPT: {e.get('error', 'unreadable')}]"
        print(f"  {when:<26}  {e['name']:<32}  {size:>10}  {status}")
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


def _blocked_volume_consumers(
    volume_members: list[dict[str, Any]],
    running: list[str],
    descriptors: dict[str, Any] | None = None,
) -> set[str]:
    """Running services whose on-disk /var/lib/agmind bind overlaps a volume archive member.

    The volume label's FIRST path segment is NOT the consuming service name: dify's data dir is
    /var/lib/agmind/dify/storage (label ``volume/dify/storage``) but the live consumers are
    dify-api/dify-worker; docling's is /var/lib/agmind/docling-cache, consumed by ``docling``. A
    label-first-segment match silently missed both, letting ``restore`` overwrite a live service's
    dir and corrupt it (#18). Resolve real ownership by comparing bind host paths instead: a
    running service is blocked if any of its /var/lib/agmind binds is equal to, or nested with, a
    volume member's host path (either direction — restoring a parent dir clobbers a child bind and
    vice-versa).
    """
    from agmind.ops.backup_data import _DATA_PREFIX

    if descriptors is None:
        from agmind.services.renderer import load_descriptors

        descriptors = load_descriptors()

    vol_paths = [
        Path(_DATA_PREFIX) / str(m["label"]).removeprefix("volume/")
        for m in volume_members
        if m.get("label")
    ]
    blocked: set[str] = set()
    for svc in running:
        desc = descriptors.get(svc)
        if desc is None:
            continue
        for vol in getattr(desc, "volumes", []):
            host = Path(str(vol).split(":", 1)[0])
            if not str(host).startswith(_DATA_PREFIX):
                continue
            if any(
                host == vp or host.is_relative_to(vp) or vp.is_relative_to(host) for vp in vol_paths
            ):
                blocked.add(svc)
                break
    return blocked


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
    force: bool = False,
) -> int:
    backup_path = Path(backup_path)
    if not backup_path.exists():
        print(f"agmind restore: file not found: {backup_path}", file=sys.stderr)
        return 2

    # F3-lite/D-08: restore mutates live deployment state (compose config, volume data,
    # DB dumps) same as `deploy`/`rollback` — share the single-flight deploy_lock so a
    # concurrent deploy/rollback/rotate-secrets cannot race a restore on the same
    # install_dir. Contention refuses (rc=1) before any of the mutating body below runs.
    with deploy_lock(install_dir) as acquired:
        if not acquired:
            print("agmind restore: operation already in progress", file=sys.stderr)
            return 1

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
                print(
                    f"  {row.label:<14} {row.kind:<8} {row.target or '<no target>'}  ({row.detail})"
                )
            print("\nno changes made (dry-run).")
            return 0

        # L.E.5: detect running deployment ДО overwrite — restore поверх работающего
        # compose'а гарантированно ломает container'ы (compose файл меняется на лету).
        running = _running_compose_services(install_dir)

        # F2/D-03: volume-kind members overwrite a service's /var/lib/agmind/<svc> dir on disk
        # (mv/rm -rf) — doing that while a consuming container is live can corrupt its on-disk
        # state. Resolve the consuming service(s) per volume member by BIND PATH, not the label's
        # first segment: the label's first segment is the data DIR name, which is not always the
        # service name (#18 — dify's /var/lib/agmind/dify/storage is consumed by dify-api/
        # dify-worker; docling's /var/lib/agmind/docling-cache by docling). dbdump members are the
        # OPPOSITE: their restore execs into the live container (`docker exec ... psql`), so they
        # are excluded from both the hard-fail and the advice below.
        raw_data_members = metadata.get("data", [])
        data_members = raw_data_members if isinstance(raw_data_members, list) else []
        volume_members = [
            m for m in data_members if isinstance(m, dict) and m.get("kind") == "volume"
        ]
        blocked = _blocked_volume_consumers(volume_members, running)

        if running:
            print(f"\nWARNING: deployment at {install_dir} has {len(running)} running services:")
            print(f"  {', '.join(running)}")
            print("Restoring over a running compose can break containers.")
            if volume_members:
                print("Recommended: run `docker compose -f docker-compose.yml down` first.")

        if blocked and not force:
            # Unconditional of `-y` — a volume restore over a live consumer is data loss, not a
            # "proceed anyway" prompt. --force is the only documented bypass (T-restore-force).
            print(
                "agmind restore: refusing — this archive would overwrite volume data for live "
                f"consumer(s): {', '.join(sorted(blocked))}. Stop them first "
                "(`docker compose -f docker-compose.yml down`) or pass --force to accept the "
                "data loss risk (restoring volume data under a live daemon can corrupt "
                "on-disk state).",
                file=sys.stderr,
            )
            return 1

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

        # Route every volume/<svc> data member to its trusted system_dir destination.
        # default_sources carries no volume/* label, so without this restore_backup drops
        # every volume member silently while printing "✓ restored" (review HIGH
        # restore-volume-data-unreachable). Destinations come ONLY from the local system_dir
        # + the label suffix — never the archive's host_path (audit H#4).
        destinations: dict[str, Path] = {}
        raw_data = metadata.get("data", [])
        db_passwords: dict[str, str] = {}
        from agmind.core.env import parse_env_file

        env_path = install_dir / ".env"
        try:
            env = parse_env_file(env_path) if env_path.exists() else {}
        except PermissionError:
            # Root-owned .env unreadable as non-root: don't crash with a traceback — restore the
            # config/volume members and let DB-dump members that need a password surface as
            # failed (RestoreResult.failed). For a full DB restore, re-run with sudo.
            print(
                "agmind restore: .env is root-owned (0600) — DB-credential restores need sudo; "
                "config/volume members will still restore.",
                file=sys.stderr,
            )
            env = {}
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
        except Exception as exc:
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
                "\nNOTE: cf_dns_api_token was NOT restored (the secret is not in the backup). "
                "Restore it manually:"
            )
            print(f'  echo "$TOKEN" > {token_path} && chmod 600 {token_path}')

        # L.E.4: warn если каталог моделей пуст после restore
        models_dir = system_dir / "models"
        has_models = models_dir.exists() and any(
            p.suffix in (".gguf", ".safetensors", ".bin") for p in models_dir.iterdir()
        )
        if not has_models:
            print(
                f"\nWARNING: {models_dir} is empty — models are not in the backup (too large). "
                f"Run `agmind models pull <name>` to populate it."
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
    secrets_dir: Path = Path("/var/lib/agmind/secrets"),
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

    # F3-lite/D-08: rotate-secrets mutates live deployment state (.env, secret FILEs, and
    # force-recreates holders) same as `deploy`/`rollback`/`restore` — share the single-flight
    # deploy_lock so a concurrent deploy/rollback/restore cannot race a rotation on the same
    # install_dir. Contention refuses (rc=1) before any of the mutating body below runs.
    with deploy_lock(install_dir) as acquired:
        if not acquired:
            print("agmind rotate-secrets: operation already in progress", file=sys.stderr)
            return 1

        try:
            text = env_path.read_text(encoding="utf-8")
            env = parse_env_file(env_path)
        except PermissionError as exc:
            # /opt/agmind/.env is root:root 0600; rotate-secrets must READ and WRITE it, so it
            # genuinely needs root. Guide the operator instead of dumping a raw traceback
            # (live-audit 2026-06-05 rotate-secrets-permissionerror-traceback).
            print(
                f"agmind rotate-secrets: cannot read {env_path} ({exc}) — the .env is "
                "root-owned. Re-run with sudo (e.g. `sudo -E agmind ops rotate-secrets ...`).",
                file=sys.stderr,
            )
            return 1
        plan = plan_rotation(env, include=include or [], force_destructive=force_destructive)

        print("rotate-secrets plan:")
        print(f"  rotate ({len(plan.rotate)}): {', '.join(plan.rotate) or '<none>'}")
        if plan.skipped_init:
            print(f"  skipped INIT-ONLY (need --include): {', '.join(plan.skipped_init)}")
        if plan.refused_encrypt:
            print(
                "  refused ENCRYPT-AT-REST (need --force-destructive): "
                f"{', '.join(plan.refused_encrypt)}"
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

        # db-secrets→FILE: a rotated DB-server password also lives in a 0600 secret FILE the
        # server reads via *_PASSWORD_FILE (invisible to the ${VAR}-scanning secret_consumers).
        # Re-materialize the FILE and force-recreate the server below, or the server keeps the
        # OLD password while .env consumers move to the new one → auth skew.
        # live-audit 2026-06-07 (SEC-3).
        from agmind.install.secret_keys import DB_SECRET_FILE_READER_UID, DB_SECRET_FILES

        rotated_db_servers: list[str] = []
        for svc, fname, env_key in DB_SECRET_FILES:
            if env_key in plan.rotate:
                write_private_text(secrets_dir / fname, new_env[env_key])
                reader_uid = DB_SECRET_FILE_READER_UID.get(fname)
                if reader_uid is not None:
                    # mongo reads its *_FILE as uid 999 — keep the rotated file readable by it.
                    # Best-effort (root rotation works; non-root context skips rather than
                    # crashing).
                    try:
                        os.chown(secrets_dir / fname, reader_uid, reader_uid)
                    except (PermissionError, OSError):
                        pass
                rotated_db_servers.append(svc)
        if rotated_db_servers:
            print(
                f"✓ re-materialized {len(rotated_db_servers)} DB secret file(s): "
                f"{', '.join(rotated_db_servers)}"
            )

        # authelia-secrets→FILE: a rotated authelia secret also lives in a 0600 secret FILE
        # authelia reads via *_FILE (invisible to the ${VAR}-scanning secret_consumers walk) —
        # exact parity with the DB_SECRET_FILES case above. Re-materialize the FILE and
        # force-recreate authelia below, or it keeps reading the OLD secret while .env
        # consumers move to the new one → auth desync. Closes the SEC-3 desync class for
        # authelia (SPEC-15.4).
        from agmind.install.secret_keys import AUTHELIA_SECRET_FILES

        rotated_authelia_holders: set[str] = set()
        for svc, fname, env_key in AUTHELIA_SECRET_FILES:
            if env_key in plan.rotate:
                write_private_text(secrets_dir / fname, new_env[env_key])
                rotated_authelia_holders.add(svc)
        if rotated_authelia_holders:
            print(
                f"✓ re-materialized {len(rotated_authelia_holders)} authelia secret file(s): "
                f"{', '.join(sorted(rotated_authelia_holders))}"
            )

        if not recreate:
            print(
                "skipped recreate (--no-recreate). Run "
                "`docker compose up -d --force-recreate <holders>` (NOT restart — that keeps "
                "the old env)."
            )
            return 0

        from agmind.services.renderer import load_descriptors

        # Union the consumers (from ${VAR} scan) with the DB SERVERS + authelia whose FILE(s)
        # we just rewrote — those reference the secret via *_FILE so secret_consumers can't
        # see them, but they MUST be recreated to re-read the file.
        # live-audit 2026-06-07 (SEC-3); authelia parity SPEC-15.4.
        holders = sorted(
            set(holders_for(plan.rotate, secret_consumers(load_descriptors())))
            | set(rotated_db_servers)
            | rotated_authelia_holders
        )
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
    include_data: bool = False,
) -> int:
    import json
    import tempfile

    from agmind.ops.backup import create_backup, default_sources, restore_backup, verify_backup
    from agmind.ops.dr_drill import run_drill

    if not (install_dir / "docker-compose.yml").exists() and not (install_dir / ".env").exists():
        print(f"agmind dr-drill: no deployment found at {install_dir}", file=sys.stderr)
        return 2

    sudo = _prompt_sudo_password(ask_sudo_password)

    # --include-data exercises the DATA tier (live-audit MED dr-drill-skips-data-tier): the drill
    # now BACKS UP the db dumps + volume tars and the integrity step verifies each dump's sha256.
    # The sandbox restore deliberately restores config + VOLUME members only — a dbdump restore
    # execs `docker exec agmind-<db> psql`, which targets the LIVE container, NOT the sandbox, so
    # exec'ing it here would overwrite live data. Dump integrity is proven by the integrity step.
    data_srcs = None
    if include_data:
        from agmind.core.env import parse_env_file_or_empty
        from agmind.ops.backup_data import data_sources as enumerate_data_sources
        from agmind.services.renderer import load_descriptors

        services = _running_compose_services(install_dir)
        env = parse_env_file_or_empty(install_dir / ".env")
        data_srcs = enumerate_data_sources(services, load_descriptors(), env)

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
                data_sources=data_srcs,
                sudo_password=sudo,
            )
            return archive_path

        def _restore(path: Path) -> list[str]:
            # Restore into a throwaway sandbox — NEVER the live install. With --include-data,
            # scope to config + VOLUME members only (exclude dbdump/* — its restore would exec
            # into the LIVE db container, not the sandbox).
            restore_labels = None
            if include_data:
                meta = read_metadata(path)
                included_raw = meta.get("included", [])
                config_labels = (
                    [str(x) for x in included_raw] if isinstance(included_raw, list) else []
                )
                data_raw = meta.get("data", [])
                data_members = data_raw if isinstance(data_raw, list) else []
                volume_labels = [
                    str(m["label"])
                    for m in data_members
                    if isinstance(m, dict) and m.get("kind") != "dbdump"
                ]
                restore_labels = config_labels + volume_labels
            result = restore_backup(
                backup_path=path,
                sources=default_sources(sandbox / "opt", sandbox / "user", sandbox / "system"),
                labels=restore_labels,
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
        service: str | None = typer.Argument(None, help="Service name (omit for all services)."),
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
            None, "--workdir", "-w", help="Working dir inside the container."
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
        remote: str | None = typer.Option(
            None,
            "--remote",
            help="After a successful backup, push the archive off-host via rclone to this "
            "remote (e.g. `s3:agmind-backups/host1`). Requires the rclone binary + config.",
        ),
        encrypt: bool = typer.Option(
            False,
            "--encrypt/--no-encrypt",
            help="Encrypt the finished archive at rest with age, producing <output>.age and "
            "removing the plaintext. Requires --age-recipient and the age binary.",
        ),
        age_recipient: str | None = typer.Option(
            None,
            "--age-recipient",
            help="age recipient the encrypted backup is sealed to (e.g. `age1...`). Required "
            "with --encrypt.",
        ),
    ) -> None:
        """Create tar.gz backup of compose / .env / state / snapshots."""
        raise typer.Exit(
            code=cmd_backup(
                output,
                ask_sudo_password=ask_sudo_password,
                include_data=include_data,
                remote=remote,
                encrypt=encrypt,
                age_recipient=age_recipient,
            )
        )

    @app.command("backup-list")
    def backup_list(
        directory: Path = typer.Option(
            Path("."),
            "--directory",
            "-d",
            help="Directory to scan for *.tar.gz backup archives (default: current dir).",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """List backup archives in a directory (name, timestamp, size), newest first."""
        raise typer.Exit(code=cmd_backup_list(directory, as_json=as_json))

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
        force: bool = typer.Option(
            False,
            "--force",
            help="Bypass the live-consumer hard-fail for volume restores; data loss risk.",
        ),
    ) -> None:
        """Restore deployment from an `agmind backup` archive."""
        raise typer.Exit(
            code=cmd_restore(
                backup_file,
                yes=yes,
                ask_sudo_password=ask_sudo_password,
                dry_run=dry_run,
                labels=label,
                skip_verify=skip_verify,
                force=force,
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
        include_data: bool = typer.Option(
            False,
            "--include-data",
            help="Exercise the DATA tier: back up db dumps + volume tars and verify dump "
            "integrity (sandbox restore stays config+volume only — dbdump restore would hit "
            "the live db).",
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
                include_data=include_data,
            )
        )

    @ops_app.command("backup-timer")
    def ops_backup_timer(
        install: bool = typer.Option(
            False, "--install", help="Install + enable the scheduled-backup systemd timer."
        ),
        uninstall: bool = typer.Option(
            False, "--uninstall", help="Disable + remove the scheduled-backup timer."
        ),
        schedule: str = typer.Option(
            "daily",
            "--schedule",
            help="Backup cadence: daily / weekly / a raw systemd OnCalendar expression.",
        ),
        remote: str | None = typer.Option(
            None,
            "--remote",
            help="rclone remote to push each backup off-host (e.g. `s3:agmind-backups/host1`). "
            "Defaults to AGMIND_BACKUP_RCLONE_REMOTE in the deployment .env when unset.",
        ),
        include_data: bool = typer.Option(
            True,
            "--include-data/--no-include-data",
            help="Back up the DATA tier (DB dumps + volume dirs), not just config. Default on.",
        ),
        install_dir: Path = typer.Option(
            BACKUP_INSTALL_DIR, "--install-dir", help="Deployment dir (for .env remote lookup)."
        ),
        output_dir: Path = typer.Option(
            DEFAULT_BACKUP_OUTPUT_DIR, "--output-dir", help="Where scheduled backups are written."
        ),
        unit_dir: Path = typer.Option(
            SYSTEMD_UNIT_DIR, "--unit-dir", help="systemd unit dir.", hidden=True
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password to write root-owned systemd unit files.",
        ),
    ) -> None:
        """Manage the opt-in scheduled-backup systemd timer (--include-data + off-host push)."""
        raise typer.Exit(
            code=cmd_backup_timer(
                install=install,
                uninstall=uninstall,
                schedule=schedule,
                remote=remote,
                include_data=include_data,
                ask_sudo_password=ask_sudo_password,
                install_dir=install_dir,
                output_dir=output_dir,
                unit_dir=unit_dir,
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
    "cmd_backup_list",
    "cmd_backup_timer",
    "cmd_logs",
    "cmd_restore",
    "cmd_root_owned_backup_smoke",
    "cmd_shell",
    "cmd_verify_backup",
    "register",
]
