"""Phase N: concrete install steps.

Каждый step стримит stdout/stderr субпроцесса через callback (LOG events),
обновляет progress percent если можно вычислить, и возвращает
InstallStepResult с success / message / elapsed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import yaml

from agmind.config.env import write_env
from agmind.core.docker_auth import user_docker_config_dir
from agmind.core.env import compose_env_quote, parse_env_file, parse_env_text
from agmind.core.secrets import write_private_text
from agmind.install.ansible_tools import resolve_ansible_command
from agmind.install.orchestrator import (
    DEFAULT_REPO_ROOT,
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressEvent,
    ProgressKind,
)
from agmind.install.secret_keys import AUTHELIA_SECRET_KEYS as _AUTHELIA_SECRET_KEYS
from agmind.install.secret_keys import RUNTIME_SECRET_KEYS as _RUNTIME_SECRET_KEYS
from agmind.install.secret_keys import generate_for as _generate_runtime_secret

# ---------- helpers ----------


_ALERTMANAGER_TELEGRAM_KEYS = (
    "AGMIND_ALERT_TELEGRAM_CHAT_ID",
    "AGMIND_ALERT_TELEGRAM_BOT_TOKEN",
)

# Optional extra alert channels. Operator-set (preserved-if-present, NOT
# auto-generated secrets) and wired into the staged config as mounted files,
# mirroring the Telegram *_file pattern. Email is conditional (smarthost has no
# _file variant and must be non-empty at config-load); webhook + email blocks
# are injected only when configured, so an unconfigured stack stays Telegram-only.
_ALERTMANAGER_MULTICHANNEL_KEYS = (
    "SMTP_SMARTHOST",
    "SMTP_FROM",
    "SMTP_TO",
    "SMTP_AUTH_USERNAME",
    "SMTP_AUTH_PASSWORD",
    "ALERT_WEBHOOK_URL",
)

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"

_RUNTIME_TARGET_GUARD_SCRIPT = r"""
set -eu
while [ "$#" -gt 0 ]; do
    kind=$1
    path=$2
    shift 2
    if [ -L "$path" ]; then
        echo "runtime ${kind} target must not be a symlink: ${path}" >&2
        exit 1
    fi
    if [ ! -e "$path" ]; then
        continue
    fi
    case "$kind" in
        directory)
            if [ ! -d "$path" ]; then
                echo "runtime directory target must be a real directory: ${path}" >&2
                exit 1
            fi
            ;;
        file)
            if [ ! -f "$path" ]; then
                echo "runtime file target must be a regular file: ${path}" >&2
                exit 1
            fi
            ;;
        *)
            echo "unknown runtime target kind: ${kind}" >&2
            exit 1
            ;;
    esac
done
"""


def _redact_install_secrets(text: str, config: InstallConfig) -> str:
    redacted = text
    for secret in (config.cf_api_token, config.sudo_password):
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def _copytree_contents(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"required runtime template directory missing: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            _copytree_atomic(item, destination)
        else:
            _copy_file_atomic(item, destination)


def _cleanup_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _replace_path_atomic(staged: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.rollback")
    _cleanup_path(backup)
    try:
        if target.exists():
            target.replace(backup)
        staged.replace(target)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        _cleanup_path(backup)


def _copytree_atomic(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        shutil.copytree(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        shutil.copy2(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _write_secret_file(path: Path, value: str, reader_uid: int | None = None) -> None:
    secret_dir = path.parent
    if secret_dir.exists() and (secret_dir.is_symlink() or not secret_dir.is_dir()):
        raise OSError(f"runtime secret directory must be a real directory: {secret_dir}")
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_dir.chmod(0o700)
    write_private_text(path, value)
    if reader_uid is not None:
        # Image reads this *_FILE secret after dropping to a non-root uid (e.g. mongo → 999), so a
        # root:root 0600 file is unreadable. chown to that uid, keeping 0600 (only it + root read).
        # Best-effort: the real install runs as root (chown works); a non-root context (tests /
        # non-sudo) can't chown to another uid — skip rather than fail the secret write.
        try:
            os.chown(path, reader_uid, reader_uid)
        except (PermissionError, OSError):
            pass


def _stage_prometheus_config(observability_dir: Path, prometheus_dir: Path) -> None:
    staged = prometheus_dir.with_name(f".{prometheus_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _copy_file_atomic(observability_dir / "prometheus.yml", staged / "prometheus.yml")
        _copytree_contents(observability_dir / "prometheus" / "rules", staged / "rules")
        _replace_path_atomic(staged, prometheus_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_single_file_config(source: Path, target_dir: Path, target_name: str) -> None:
    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _copy_file_atomic(source, staged / target_name)
        _replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


_WEBHOOK_URL_FILE = "/etc/alertmanager/webhook_url"
_SMTP_PASSWORD_FILE = "/etc/alertmanager/smtp_password"


def build_alertmanager_config(
    base_text: str,
    *,
    webhook_url: str = "",
    smtp_smarthost: str = "",
    smtp_from: str = "",
    smtp_to: str = "",
    smtp_auth_username: str = "",
) -> str:
    """Augment the Telegram-only base config with optional email/webhook channels.

    Webhook and email blocks are appended to EVERY receiver, but only when the
    channel is configured — so an unconfigured stack renders byte-equivalent to
    the Telegram-only base and keeps booting. Email is gated on a non-empty
    smarthost+recipient (Alertmanager requires smarthost at config-load and has
    no ``*_file`` variant for it); its password is file-backed
    (``auth_password_file``), never inlined. Webhook uses ``url_file`` so the
    (often token-bearing) URL never lands inline in a world-readable :ro config.
    """
    cfg = yaml.safe_load(base_text) or {}
    receivers = cfg.get("receivers") or []

    email_block: dict[str, object] | None = None
    if smtp_smarthost and smtp_to:
        email_block = {"to": smtp_to}
        if smtp_from:
            email_block["from"] = smtp_from
        email_block["smarthost"] = smtp_smarthost
        if smtp_auth_username:
            email_block["auth_username"] = smtp_auth_username
            email_block["auth_password_file"] = _SMTP_PASSWORD_FILE
        email_block["require_tls"] = True
        email_block["send_resolved"] = True

    webhook_block: dict[str, object] | None = None
    if webhook_url:
        webhook_block = {"url_file": _WEBHOOK_URL_FILE, "send_resolved": True}

    for receiver in receivers:
        if email_block is not None:
            receiver.setdefault("email_configs", []).append(dict(email_block))
        if webhook_block is not None:
            receiver.setdefault("webhook_configs", []).append(dict(webhook_block))

    return yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _stage_alertmanager_config(
    observability_dir: Path,
    target_dir: Path,
    *,
    chat_id: str,
    bot_token: str,
    webhook_url: str = "",
    smtp_smarthost: str = "",
    smtp_from: str = "",
    smtp_to: str = "",
    smtp_auth_username: str = "",
    smtp_auth_password: str = "",
) -> None:
    """Stage alertmanager.yml plus the per-channel secret files as mounted files.

    The config references ``/etc/alertmanager/tg_chat_id`` + ``/etc/alertmanager/
    tg_bot_token`` (chat_id_file/bot_token_file). Both are written here from the runtime
    .env (AGMIND_ALERT_TELEGRAM_CHAT_ID / _BOT_TOKEN); empty values still write empty
    files so the referenced paths exist and alertmanager boots (sending is a no-op until
    configured). Optional email/webhook channels are injected into the config only when
    configured, with their secrets written to ``webhook_url`` / ``smtp_password`` mounted
    files. Staged atomically so a partial write never replaces a good config dir.
    """
    base_text = (observability_dir / "alertmanager.yml").read_text(encoding="utf-8")
    rendered = build_alertmanager_config(
        base_text,
        webhook_url=webhook_url,
        smtp_smarthost=smtp_smarthost,
        smtp_from=smtp_from,
        smtp_to=smtp_to,
        smtp_auth_username=smtp_auth_username,
    )

    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "alertmanager.yml").write_text(rendered, encoding="utf-8")
        (staged / "tg_chat_id").write_text(chat_id, encoding="utf-8")
        # Bearer secrets → 0600 via write_private_text (not plain write_text's umask 0644):
        # the sudo `cp --no-preserve=ownership` preserves the source mode, so these stay
        # 0600 at rest instead of relying solely on the /etc/agmind 0750 parent bit.
        write_private_text(staged / "tg_bot_token", bot_token)
        # Only materialize a channel's secret file when that channel is configured,
        # matching the conditional injection in the rendered config above.
        if webhook_url:
            write_private_text(staged / "webhook_url", webhook_url)
        if smtp_auth_password:
            write_private_text(staged / "smtp_password", smtp_auth_password)
        _replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _authelia_argon2_hash(password: str) -> str:
    """argon2id hash matching Authelia's file-backend default params (m=64MiB,t=3,p=4)."""
    from argon2 import PasswordHasher, Type

    return PasswordHasher(
        time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
    ).hash(password)


def _replace_authelia_password_hash(text: str, password: str) -> str:
    """Replace the admin `password:` hash in users_database.yml with argon2id(password)."""
    new_hash = _authelia_argon2_hash(password)
    return re.sub(
        r"(?m)^(\s*password:\s*)'[^']*'",
        lambda _m: f"{_m.group(1)}'{new_hash}'",
        text,
        count=1,
    )


def _stage_authelia_config(
    authelia_src: Path, target_dir: Path, *, domain: str, admin_password: str = ""
) -> None:
    """Stage Authelia's configuration.yml + users_database.yml, substituting the domain.

    Both ship with the __AGMIND_DOMAIN__ token (session cookie domain + admin email);
    replace it with the install domain. When ``admin_password`` is supplied, its argon2id
    hash replaces the shipped upstream EXAMPLE hash in users_database.yml so the SSO never
    boots with the well-known `authelia` password (audit H#1). Other secrets are NOT here —
    Authelia reads them from the runtime .env as AUTHELIA_* env. Staged atomically; the dir
    is left WRITABLE (Authelia chowns it + writes db.sqlite3/notification.txt at runtime).
    """
    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        for name in ("configuration.yml", "users_database.yml"):
            text = (
                (authelia_src / name)
                .read_text(encoding="utf-8")
                .replace("__AGMIND_DOMAIN__", domain)
            )
            if name == "users_database.yml" and admin_password:
                text = _replace_authelia_password_hash(text, admin_password)
            (staged / name).write_text(text, encoding="utf-8")
        _replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_squid_config(squid_src: Path, target_dir: Path) -> None:
    """Stage squid.conf as the ssrf-proxy's single read-only config file.

    The descriptor mounts ``/etc/agmind/ssrf-proxy/squid.conf`` :ro; a single-file
    bind mount whose host source is missing makes Docker create a DIRECTORY there
    (squid then crash-loops), so the file MUST exist before deploy. Staged
    atomically so a partial write never replaces a good config.
    """
    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _copy_file_atomic(squid_src, staged / "squid.conf")
        _replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_directory_contents(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        _copytree_contents(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _materialize_runtime_files(
    config: InstallConfig,
    runtime_env: dict[str, str],
    callback: ProgressCallback,
    step_id: str,
) -> None:
    selected = set(config.services)
    templates_dir = DEFAULT_REPO_ROOT / "templates"
    data_dir = config.models_dir.parent

    # DB SERVER passwords as 0600 files (POSTGRES/MYSQL/MONGO *_PASSWORD_FILE) so the server
    # container does not carry the secret in its env (`docker inspect` / socket-proxy inspect).
    # Consumers keep the env var (their images lack _FILE support). live-audit 2026-06-05
    # db-secrets-plaintext-docker-inspect / secrets-plaintext-env.
    from agmind.install.secret_keys import DB_SECRET_FILE_READER_UID, DB_SECRET_FILES

    for svc, fname, env_key in DB_SECRET_FILES:
        if svc in selected and runtime_env.get(env_key):
            _write_secret_file(
                data_dir / "secrets" / fname,
                runtime_env[env_key],
                reader_uid=DB_SECRET_FILE_READER_UID.get(fname),
            )

    if "traefik" in selected:
        if config.cf_api_token:
            _write_secret_file(data_dir / "secrets" / "cf_dns_api_token", config.cf_api_token)
        _stage_directory_contents(
            templates_dir / "traefik" / "dynamic",
            data_dir / "traefik" / "dynamic",
        )
        (data_dir / "traefik" / "letsencrypt").mkdir(parents=True, exist_ok=True)

    observability_dir = templates_dir / "observability"
    if "prometheus" in selected:
        _stage_prometheus_config(observability_dir, config.config_dir / "prometheus")
    if "grafana" in selected:
        _stage_directory_contents(
            observability_dir / "grafana" / "provisioning",
            config.config_dir / "grafana" / "provisioning",
        )
    if "loki" in selected:
        _stage_directory_contents(observability_dir / "loki", config.config_dir / "loki")
    if "alloy" in selected:
        _stage_directory_contents(observability_dir / "alloy", config.config_dir / "alloy")
    if "authelia" in selected:
        # Use the in-memory generated env (NOT a re-read of .env): on a fresh install AND
        # on the sudo path the .env does not exist yet when this runs, so a file re-read
        # would yield admin_password='' → the upstream EXAMPLE hash survives (review HIGH
        # authelia-default-password-ordering / alertmanager-sudo-empty-values).
        _stage_authelia_config(
            templates_dir / "authelia",
            config.config_dir / "authelia",
            domain=config.domain,
            admin_password=runtime_env.get("AUTHELIA_ADMIN_PASSWORD", ""),
        )
    if "alertmanager" in selected:
        _stage_alertmanager_config(
            observability_dir,
            config.config_dir / "alertmanager",
            chat_id=runtime_env.get("AGMIND_ALERT_TELEGRAM_CHAT_ID", ""),
            bot_token=runtime_env.get("AGMIND_ALERT_TELEGRAM_BOT_TOKEN", ""),
            webhook_url=runtime_env.get("ALERT_WEBHOOK_URL", ""),
            smtp_smarthost=runtime_env.get("SMTP_SMARTHOST", ""),
            smtp_from=runtime_env.get("SMTP_FROM", ""),
            smtp_to=runtime_env.get("SMTP_TO", ""),
            smtp_auth_username=runtime_env.get("SMTP_AUTH_USERNAME", ""),
            smtp_auth_password=runtime_env.get("SMTP_AUTH_PASSWORD", ""),
        )
    if "ssrf-proxy" in selected:
        _stage_squid_config(
            templates_dir / "squid" / "squid.conf",
            config.config_dir / "ssrf-proxy",
        )

    callback(
        _make_event(
            step_id,
            ProgressKind.LOG,
            f"runtime files ready: data={data_dir} config={config.config_dir}",
        )
    )


def _stage_runtime_payload(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    config.install_dir.mkdir(parents=True, exist_ok=True)
    # env_text is the authoritative, already-preserved+generated runtime env (built by
    # EnvWriteStep from _runtime_env(existing)). Parse it in-memory and feed the
    # config-materialization so it never depends on a .env that is not on disk yet.
    _materialize_runtime_files(config, parse_env_text(env_text), callback, step_id)
    write_env(config.install_dir / ".env", env_text, mode=0o600)
    write_env(config.install_dir / "version.env", version_env_text, mode=0o644)


def _write_runtime_payload_local(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    _stage_runtime_payload(config, env_text, version_env_text, callback, step_id)


def _run_sudo_runtime_command(
    config: InstallConfig,
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
) -> None:
    if config.sudo_password is None:
        raise PermissionError("sudo password not provided for root-owned runtime paths")
    rc, _ = _stream_subprocess(
        ["sudo", "-S", "-p", "", "--", *cmd],
        callback,
        step_id,
        stdin_payload=_sudo_stdin_payload(config),
    )
    if rc != 0:
        raise OSError(f"sudo command failed rc={rc}: {cmd[0]}")


def _write_private_text_maybe_sudo(
    config: InstallConfig,
    path: Path,
    content: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    """Write a 0600 file at ``path``; fall back to ``sudo install`` if the dir is not writable.

    The install dir (e.g. ``/opt/agmind``) is created root/agmind-owned via the sudo runtime
    payload, so a non-root install user cannot even ``mkstemp`` inside it — ``write_private_text``
    raised PermissionError and ``credentials.txt`` was silently skipped, leaving the operator with
    no password record (live-audit 2026-06-05 credentials-txt-write-no-sudo-path). Mirror the
    .env write: stage to a user-writable temp, then place it root:root 0600 via sudo.
    """
    try:
        write_private_text(path, content)
        return
    except PermissionError:
        pass
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=".agmind-creds-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o600)
        _run_sudo_runtime_command(
            config,
            ["install", "-m", "0600", "-o", "root", "-g", "root", tmp, str(path)],
            callback,
            step_id,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _ensure_models_dir(
    config: InstallConfig,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    """Ensure ``config.models_dir`` exists and is writable by the current user.

    The runtime data root (e.g. ``/var/lib/agmind``) is created root-owned via sudo by
    the runtime-payload step, so a plain user-level ``mkdir`` of ``<data>/models`` fails
    with ``[Errno 13]`` (the real model_pull failure on first install). If we cannot
    create/write it directly, create it and hand ownership to the invoking uid/gid via
    sudo — consistent with how the other system-path writes are privileged — so the
    multi-GB download itself still runs unprivileged.
    """
    models_dir = config.models_dir
    if models_dir.is_dir() and os.access(models_dir, os.W_OK):
        return
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
        if os.access(models_dir, os.W_OK):
            return
    except PermissionError:
        pass
    # Root-owned parent: create + chown to the current user via sudo.
    _run_sudo_runtime_command(
        config,
        [
            "install",
            "-d",
            "-o",
            str(os.getuid()),
            "-g",
            str(os.getgid()),
            "-m",
            "0755",
            str(models_dir),
        ],
        callback,
        step_id,
    )


def _sudo_runtime_target_args(staged_root: Path, target_root: Path) -> list[str]:
    args: list[str] = ["directory", str(target_root)]
    if not staged_root.exists():
        return args
    for item in staged_root.rglob("*"):
        if item.is_dir():
            kind = "directory"
        elif item.is_file():
            kind = "file"
        else:
            continue
        args.extend([kind, str(target_root / item.relative_to(staged_root))])
    return args


def _assert_sudo_runtime_targets_safe(
    config: InstallConfig,
    staged_install: Path,
    staged_data: Path,
    staged_config: Path,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    args = [
        "directory",
        str(config.install_dir),
        "file",
        str(config.install_dir / ".env"),
        "file",
        str(config.install_dir / "version.env"),
        *_sudo_runtime_target_args(staged_data, config.models_dir.parent),
        *_sudo_runtime_target_args(staged_config, config.config_dir),
    ]
    rc, lines = _stream_subprocess(
        [
            "sudo",
            "-S",
            "-p",
            "",
            "--",
            "sh",
            "-c",
            _RUNTIME_TARGET_GUARD_SCRIPT,
            "agmind-runtime-target-guard",
            *args,
        ],
        callback,
        step_id,
        stdin_payload=_sudo_stdin_payload(config),
    )
    if rc != 0:
        detail = lines[-1] if lines else "unsafe runtime target path"
        raise OSError(detail)


def _write_runtime_payload_sudo(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    data_dir = config.models_dir.parent
    with tempfile.TemporaryDirectory(prefix="agmind-runtime-payload-") as tmp:
        stage = Path(tmp)
        staged_install = stage / "install"
        staged_data = stage / "data"
        staged_config = stage / "config"
        staged_config.mkdir(parents=True, exist_ok=True)
        staged = replace(
            config,
            install_dir=staged_install,
            models_dir=staged_data / "models",
            config_dir=staged_config,
        )
        _stage_runtime_payload(staged, env_text, version_env_text, callback, step_id)

        _assert_sudo_runtime_targets_safe(
            config,
            staged_install,
            staged_data,
            staged_config,
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-d",
                "-m",
                "0755",
                str(config.install_dir),
                str(data_dir),
                str(config.config_dir),
            ],
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-m",
                "0600",
                str(staged_install / ".env"),
                str(config.install_dir / ".env"),
            ],
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-m",
                "0644",
                str(staged_install / "version.env"),
                str(config.install_dir / "version.env"),
            ],
            callback,
            step_id,
        )
        if staged_data.exists():
            _run_sudo_runtime_command(
                config,
                ["cp", "-R", "--no-preserve=ownership", f"{staged_data}/.", str(data_dir)],
                callback,
                step_id,
            )
        if staged_config.exists():
            _run_sudo_runtime_command(
                config,
                [
                    "cp",
                    "-R",
                    "--no-preserve=ownership",
                    f"{staged_config}/.",
                    str(config.config_dir),
                ],
                callback,
                step_id,
            )
        secret_file = data_dir / "secrets" / "cf_dns_api_token"
        if (staged_data / "secrets" / "cf_dns_api_token").exists():
            _run_sudo_runtime_command(
                config,
                ["chmod", "0700", str(data_dir / "secrets")],
                callback,
                step_id,
            )
            _run_sudo_runtime_command(
                config,
                ["chmod", "0600", str(secret_file)],
                callback,
                step_id,
            )


def _env_line(key: str, value: str) -> str:
    # docker-compose env-file quoting (NOT shell): bare for simple values,
    # double-quoted+escaped for space/quote/special so writer↔reader round-trip.
    return f"{key}={compose_env_quote(value) if value else ''}"


def _runtime_env(existing_env: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {
        "MINIO_ROOT_USER": existing_env.get("MINIO_ROOT_USER") or "agmind",
        "N8N_TIMEZONE": existing_env.get("N8N_TIMEZONE") or "UTC",
    }
    # Per-key generators live in agmind.install.secret_keys (single source of
    # truth shared with `agmind ops rotate-secrets`): 32-byte token, Authelia
    # 64-char, homarr 64-hex (the base64 default aborts homarr at boot).
    for key in (*_RUNTIME_SECRET_KEYS, *_AUTHELIA_SECRET_KEYS):
        values[key] = existing_env.get(key) or _generate_runtime_secret(key)
    for key in (*_ALERTMANAGER_TELEGRAM_KEYS, *_ALERTMANAGER_MULTICHANNEL_KEYS):
        values[key] = existing_env.get(key, "")
    return values


def _version_key(service_name: str) -> str:
    return f"{service_name.upper().replace('-', '_')}_VERSION"


def _image_tag(image: str) -> str:
    image_without_digest = image.split("@", 1)[0]
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")
    if last_colon <= last_slash or last_colon == len(image_without_digest) - 1:
        return ""
    return image_without_digest[last_colon + 1 :]


def _image_digest(image: str) -> str:
    if "@" not in image:
        return ""
    return image.rsplit("@", 1)[1].removeprefix("sha256:")


def _runtime_version_env(service_names: list[str]) -> str:
    from agmind import __version__
    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors()
    if not service_names:
        raise ValueError("no selected services for version.env")
    selected_names = sorted(set(service_names))
    missing = [name for name in selected_names if name not in descriptors]
    if missing:
        raise ValueError(f"unknown selected services for version.env: {', '.join(missing)}")

    lines = [
        "# AGmind runtime version manifest — written by `agmind install`.",
        "# Non-secret file used by operators, backup, and drift reviews.",
        _env_line("AGMIND_VERSION", __version__),
        "",
    ]
    for service_name in selected_names:
        descriptor = descriptors[service_name]
        tag = _image_tag(descriptor.image)
        digest = (descriptor.digest or _image_digest(descriptor.image)).removeprefix("sha256:")
        if not tag and not digest:
            continue
        key = _version_key(service_name)
        lines.append(_env_line(key, tag))
        lines.append(_env_line(f"{key}_IMAGE", descriptor.image))
        if digest:
            lines.append(_env_line(f"{key}_DIGEST", f"sha256:{digest}"))
    return "\n".join(lines) + "\n"


def _parse_existing_runtime_env(config: InstallConfig, env_path: Path) -> dict[str, str]:
    try:
        return parse_env_file(env_path)
    except PermissionError as exc:
        if config.sudo_password is None:
            raise
        result = subprocess.run(
            ["sudo", "-S", "-p", "", "--", "cat", str(env_path)],
            capture_output=True,
            text=True,
            check=False,
            input=f"{config.sudo_password}\n",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or str(exc)).strip()
            raise PermissionError(f"cannot read existing runtime env via sudo: {detail}") from exc
        return parse_env_text(result.stdout)


def _kill_on_cancel(
    proc: subprocess.Popen[str],
    cancel_event: threading.Event | None,
) -> None:
    """Spawn a daemon watchdog that terminates *proc* promptly when *cancel_event*
    fires.

    Without this the worker thread blocks in ``proc.stdout`` / ``proc.wait()`` and the
    whole TUI hangs on Cancel/Close — Textual cannot force-kill a thread worker, so app
    exit waits (up to ~300s) for the blocked thread. The watchdog is a daemon thread,
    so it never itself blocks interpreter shutdown, and it exits on its own when the
    process finishes normally.
    """
    if cancel_event is None:
        return

    def _watch() -> None:
        while not cancel_event.wait(0.25):
            if proc.poll() is not None:
                return  # finished normally
        # cancel fired — terminate, then hard-kill if it lingers
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    threading.Thread(target=_watch, name="agmind-cancel-watch", daemon=True).start()


def _stream_subprocess(
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin_payload: bytes | None = None,
    extra_emit: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, list[str]]:
    """Run subprocess, stream stdout+stderr line-by-line via callback.

    Returns (returncode, captured_lines). `extra_emit` callable получает
    каждую строку и может emit'ить дополнительные ProgressEvent (e.g.
    парсить progress %). Все строки также эмитятся как ProgressKind.LOG.

    If `cancel_event` is provided, a daemon watchdog kills the child when it fires so
    Cancel/Close never hangs the worker on a long subprocess.
    """
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        text=True,
        bufsize=1,
    )
    _kill_on_cancel(proc, cancel_event)
    try:
        if stdin_payload is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_payload.decode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass

        captured: list[str] = []
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                captured.append(line)
                callback(_make_event(step_id, ProgressKind.LOG, line))
                if extra_emit is not None:
                    try:
                        extra_emit(line)
                    except Exception:  # noqa: BLE001
                        pass
        rc = proc.wait()
        return rc, captured
    finally:
        # Never leave a child running / pipes open if the loop or a callback
        # raised before proc.wait().
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def _make_event(
    step_id: str,
    kind: ProgressKind,
    text: str,
    pct: int | None = None,
) -> ProgressEvent:
    """Local import to avoid circular: создать ProgressEvent без import outside."""
    from agmind.install.orchestrator import ProgressEvent

    return ProgressEvent(step_id=step_id, kind=kind, text=text, progress_pct=pct)


def _docker_compose_cmd(
    config: InstallConfig,
    args: list[str],
    env_file: Path | None = None,
    progress: str | None = None,
) -> list[str]:
    # `--progress`/`--env-file` are GLOBAL flags — they go before the subcommand.
    global_flags = ["--progress", progress] if progress is not None else []
    env_args = ["--env-file", str(env_file)] if env_file is not None else []
    compose = ["docker", "compose", *global_flags, *env_args, *args]
    if config.sudo_password is None:
        return compose
    docker_config = _user_docker_config_dir()
    if docker_config:
        compose = ["env", f"DOCKER_CONFIG={docker_config}", *compose]
    return ["sudo", "-S", "-p", "", "--", *compose]


def _user_docker_config_dir() -> str | None:
    return user_docker_config_dir()


def _pull_progress_pct(line: str, services: set[str], pulled: set[str]) -> int | None:
    """Coarse pull progress from a `docker compose --progress plain` line.

    Compose emits ``<service> Pulled`` when a service image is fully pulled (and
    layer lines like ``<sha> Downloading``/``Pull complete`` in between). Count
    distinct completed *services* over the total; returns the new pct on a fresh
    completion, else None. Best-effort and side-effecting (mutates *pulled*).
    """
    parts = line.split()
    if len(parts) < 2 or parts[-1] != "Pulled":
        return None
    service = parts[0]
    if service not in services or service in pulled:
        return None
    pulled.add(service)
    total = len(services)
    return int(len(pulled) / total * 100) if total else 0


def _write_compose_env_file(path: Path, values: dict[str, str]) -> None:
    content = "".join(_env_line(key, values[key]) + "\n" for key in sorted(values))
    write_env(path, content, mode=0o600)


def _sudo_stdin_payload(config: InstallConfig) -> bytes | None:
    if config.sudo_password is None:
        return None
    return f"{config.sudo_password}\n".encode()


def _cloudflare_zone_candidates(domain: str) -> list[str]:
    labels = [part for part in domain.strip().strip(".").lower().split(".") if part]
    if len(labels) < 2:
        return []
    return [".".join(labels[index:]) for index in range(0, len(labels) - 1)]


def _cloudflare_payload_errors(payload: dict[str, object], status: int) -> str:
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        return f"HTTP {status}"
    parts: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        if code and message:
            parts.append(f"{code}: {message}")
        elif message:
            parts.append(str(message))
        elif code:
            parts.append(str(code))
    return "; ".join(parts) if parts else f"HTTP {status}"


def _cloudflare_request_json(
    token: str,
    path: str,
    query: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    url = f"{_CLOUDFLARE_API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "agmind-installer/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = int(exc.code)
    except (OSError, urllib.error.URLError) as exc:
        raise ConnectionError(f"Cloudflare API request failed: {exc}") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cloudflare API returned invalid JSON (HTTP {status})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Cloudflare API returned non-object JSON (HTTP {status})")
    return status, payload


# ---------- Step 1: doctor ----------


class DoctorStep(InstallStep):
    """Preflight — `agmind doctor`. Hard fail if any check returns 'fail'."""

    step_id = "doctor"
    label = "Preflight diagnostics"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from agmind.diagnostics.doctor import run_preflight

        try:
            report = run_preflight()
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"doctor crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        for check in report.checks:
            glyph = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}.get(check.status, "?")
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"  {glyph}  {check.name:<22} {check.message}",
                )
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        ok_n = sum(1 for c in report.checks if c.status == "ok")
        warn_n = sum(1 for c in report.checks if c.status == "warn")
        if report.has_failures:
            failed = [c.name for c in report.checks if c.status == "fail"]
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"hard fail in checks: {', '.join(failed)}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"{ok_n} ok / {warn_n} warn / 0 fail",
            elapsed=elapsed,
        )


# ---------- Step 2: Cloudflare token ----------


class CloudflareTokenStep(InstallStep):
    """Validate the DNS-01 token before deploy can reach the ACME path."""

    step_id = "cloudflare_token"
    label = "Validate Cloudflare DNS token"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if "traefik" not in set(config.services):
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message="traefik not selected — Cloudflare token not needed",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        if len(config.cf_api_token.strip()) < 20:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="Cloudflare token missing or too short",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        try:
            status, payload = _cloudflare_request_json(
                config.cf_api_token,
                "/user/tokens/verify",
            )
        except (ConnectionError, ValueError) as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=_redact_install_secrets(str(exc), config),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        if status != 200 or payload.get("success") is not True:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=_redact_install_secrets(
                    f"Cloudflare token validation failed: "
                    f"{_cloudflare_payload_errors(payload, status)}",
                    config,
                ),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        candidates = _cloudflare_zone_candidates(config.domain)
        if not candidates:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot derive Cloudflare zone candidate from domain {config.domain}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        for candidate in candidates:
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"checking Cloudflare zone access: {candidate}",
                )
            )
            try:
                zone_status, zone_payload = _cloudflare_request_json(
                    config.cf_api_token,
                    "/zones",
                    {"name": candidate, "status": "active", "per_page": "1"},
                )
            except (ConnectionError, ValueError) as exc:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=_redact_install_secrets(str(exc), config),
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            if zone_status != 200 or zone_payload.get("success") is not True:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=_redact_install_secrets(
                        f"Cloudflare zone lookup failed for {candidate}: "
                        f"{_cloudflare_payload_errors(zone_payload, zone_status)}",
                        config,
                    ),
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            zones = zone_payload.get("result")
            if isinstance(zones, list) and zones:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=True,
                    message=f"Cloudflare token valid; zone access OK ({candidate})",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        return InstallStepResult(
            step_id=self.step_id,
            success=False,
            message=(
                "Cloudflare token is valid but cannot access an active Cloudflare zone "
                f"for domain {config.domain} (tried: {', '.join(candidates)})"
            ),
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


# ---------- Step 3: bootstrap (Ansible, sudo) ----------


class BootstrapStep(InstallStep):
    """Run `ansible-playbook install.yml --tags bootstrap` с sudo password.

    Sudo password передаётся как `ansible_become_password` внутри extra-vars,
    записанных в РЕАЛЬНЫЙ temp-файл с правами 0600 на tmpfs (`/dev/shm`, в RAM —
    не на персистентный диск) и переданный через `--extra-vars @<path>`. Файл
    удаляется в `finally`. В argv видно только путь, не сами секреты.

    ВАЖНО: НЕ передавать секреты Ansible через pipe-FD (`/dev/fd/N`) ни в одном
    file-аргументе. Ansible прогоняет такие пути через `os.path.realpath`, а realpath
    не резолвит pipe-FD: `/dev/fd/N` → `/proc/<pid>/fd/pipe:[inode]` (не существует),
    что ломает И `--become-password-file /dev/fd/N` ("The password file ... was not
    found"), И `--extra-vars @/dev/fd/N` ("Unable to retrieve file contents ...
    /proc/<pid>/fd/pipe:[inode]"). Оба падают rc=1. Отсюда — реальный temp-файл.
    """

    step_id = "bootstrap"
    label = "System bootstrap (apt, groups, dirs)"

    PLAYBOOK_RELATIVE = "ansible/install.yml"
    ANSIBLE_DIR_RELATIVE = "ansible"
    GALAXY_DIR_NAME = ".galaxy"
    ANSIBLE_TAGS = "bootstrap"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if config.sudo_password is None:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="sudo password not provided (cannot run apt/usermod)",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        ansible_dir = DEFAULT_REPO_ROOT / self.ANSIBLE_DIR_RELATIVE
        playbook = DEFAULT_REPO_ROOT / self.PLAYBOOK_RELATIVE
        if not playbook.exists():
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"playbook not found: {playbook}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        requirements = ansible_dir / "requirements.yml"
        if requirements.exists():
            galaxy_dir = ansible_dir / self.GALAXY_DIR_NAME
            galaxy_cmd = [
                resolve_ansible_command("ansible-galaxy"),
                "collection",
                "install",
                "-r",
                str(requirements),
                "-p",
                str(galaxy_dir),
            ]
            # requirements.yml pins ranges, so even pre-staged collections trigger a network
            # version-resolution call to galaxy.ansible.com. BootstrapStep is in default_steps
            # and runs BEFORE the offline-aware DeployStep, so without --offline an air-gap
            # install aborts HERE (same orphaned-guard class as the earlier offline-pull bug).
            # --offline (ansible-core ≥2.16) uses only the collections pre-staged into ansible/
            # .galaxy — see docs/installation/offline-install.md.
            if _offline_install_enabled():
                galaxy_cmd.append("--offline")
            rc, _ = _stream_subprocess(
                galaxy_cmd,
                callback,
                self.step_id,
                cwd=ansible_dir,
                cancel_event=self.cancel_event,
            )
            if rc != 0:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"ansible-galaxy collection install failed with rc={rc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        # Sudo password + sensitive vars reach Ansible via `--extra-vars @<file>`. We
        # write a REAL temp file (0600) on a tmpfs (/dev/shm, RAM-backed — never hits
        # persistent disk) and unlink it in `finally`. We must NOT use a pipe FD
        # (`/dev/fd/N`) for ANY Ansible file argument: Ansible realpath-canonicalizes
        # those paths and cannot resolve a pipe FD — `/dev/fd/N` becomes
        # `/proc/<pid>/fd/pipe:[inode]` (ENOENT), which breaks BOTH
        # `--become-password-file` and `--extra-vars @/dev/fd/N`. The visible argv
        # contains only the temp-file path, never the secret values.
        secrets_dir = (
            "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
        )
        vars_fd, vars_path = tempfile.mkstemp(
            prefix=".agmind-bootstrap-", suffix=".json", dir=secrets_dir
        )
        proc: subprocess.Popen[str] | None = None
        try:
            extra_vars_payload = json.dumps(
                {
                    "agmind_domain": config.domain,
                    "agmind_cf_api_token": config.cf_api_token,
                    "ansible_become_password": config.sudo_password,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            # mkstemp created vars_path with 0600 perms; write the secret through its fd.
            os.write(vars_fd, extra_vars_payload)
            os.close(vars_fd)
            vars_fd = -1

            cmd = [
                resolve_ansible_command("ansible-playbook"),
                str(playbook),
                "--tags",
                self.ANSIBLE_TAGS,
                "--extra-vars",
                f"@{vars_path}",
                # Use the repo inventory (localhost ∈ agmind_nodes/agmind_master
                # + agmind_* vars). A bare `-i localhost,` puts localhost only in
                # the implicit `all` group, so the bootstrap play (hosts:
                # agmind_nodes) matches ZERO hosts and silently no-ops every task
                # incl. the data-dir chown — leaving dirs root:root → crash-loops.
                "-i",
                "inventory/hosts.yml",
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                cwd=str(ansible_dir),
            )
            _kill_on_cancel(proc, self.cancel_event)
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.rstrip()
                    if not line:
                        continue
                    line = _redact_install_secrets(line, config)
                    callback(_make_event(self.step_id, ProgressKind.LOG, line))
            rc = proc.wait()
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                if proc.stdout is not None:
                    proc.stdout.close()
            if vars_fd >= 0:
                try:
                    os.close(vars_fd)
                except OSError:
                    pass
            try:
                os.unlink(vars_path)
            except OSError:
                pass

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"ansible-playbook failed with rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="apt prereqs + groups + dirs ready",
            elapsed=elapsed,
        )


# ---------- Step 3: docker image pull ----------


class ComposeConfigStep(InstallStep):
    """Validate the exact selected Compose config before real pulls/deploy."""

    step_id = "compose_config"
    label = "Validate Docker Compose config"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for compose config",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        import tempfile

        from agmind.services.renderer import render_to_string

        try:
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"compose render failed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        with tempfile.TemporaryDirectory(prefix="agmind-compose-config-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
            compose_env = _parse_existing_runtime_env(config, config.install_dir / ".env")
            compose_env_file = tmpdir / ".env"
            _write_compose_env_file(compose_env_file, compose_env)
            rc, _ = _stream_subprocess(
                _docker_compose_cmd(config, ["config", "--quiet"], env_file=compose_env_file),
                callback,
                self.step_id,
                cwd=tmpdir,
                stdin_payload=_sudo_stdin_payload(config),
                cancel_event=self.cancel_event,
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"docker compose config rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="compose config OK",
            elapsed=elapsed,
        )


def _offline_install_enabled() -> bool:
    """True when ``AGMIND_OFFLINE`` requests an air-gap install (no network pulls).

    Delegates to :func:`agmind.deploy.runner._offline_pull_enabled` — the single source
    of truth lives in the deploy layer (the REAL pull path) so the flag can't be honored
    in one place and ignored in another. docker save/load strips an image's RepoDigest, so
    a digest-pinned ``pull --policy missing`` re-pulls from the network and fails in an
    air-gap; offline switches the pull to ``--policy never`` (DeployStep then runs
    ``up --pull never``), using preloaded images.
    """
    from agmind.deploy.runner import _offline_pull_enabled

    return _offline_pull_enabled()


class ImagePullStep(InstallStep):
    """`docker compose pull` после bootstrap (user уже в docker group).

    Требует чтобы compose был отрендерен — обычно делается deploy step,
    но pull раньше = lower TTR (time to running). Используем temporary
    render через `agmind render compose` без write.
    """

    step_id = "image_pull"
    label = "Docker image pull"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for image pull",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        # Lazy import чтобы не тянуть тяжёлый renderer на загрузке модуля.
        # Render compose в temp dir чтобы вызвать `docker compose pull`.
        import tempfile

        from agmind.services.renderer import render_to_string

        try:
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"compose render failed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        with tempfile.TemporaryDirectory(prefix="agmind-pull-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
            compose_env = _parse_existing_runtime_env(config, config.install_dir / ".env")
            compose_env_file = tmpdir / ".env"
            _write_compose_env_file(compose_env_file, compose_env)
            # NO --quiet: it suppressed every per-layer line and froze the bar at 0%
            # during multi-GB pulls. --progress plain (global flag) streams readable,
            # non-ANSI lines that RichLog can render; --policy missing keeps it idempotent.
            # AGMIND_OFFLINE → --policy never so an air-gap install never hits the network
            # (images are preloaded via `docker load`; see docs/installation/offline-install.md).
            from agmind.deploy.runner import resolve_pull_policy

            offline = _offline_install_enabled()
            if offline:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        "AGMIND_OFFLINE: skipping network pull (--policy never); "
                        "images must be preloaded via `docker load`",
                    )
                )
            cmd = _docker_compose_cmd(
                config,
                ["pull", "--policy", resolve_pull_policy(offline)],
                env_file=compose_env_file,
                progress="plain",
            )
            service_set = set(config.services)
            pulled: set[str] = set()

            def emit_pull_progress(line: str) -> None:
                pct = _pull_progress_pct(line, service_set, pulled)
                if pct is None:
                    return
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.PROGRESS,
                        f"pulled {len(pulled)}/{len(service_set)} images",
                        pct=pct,
                    )
                )

            rc, _ = _stream_subprocess(
                cmd,
                callback,
                self.step_id,
                cwd=tmpdir,
                stdin_payload=_sudo_stdin_payload(config),
                extra_emit=emit_pull_progress,
                cancel_event=self.cancel_event,
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"docker compose pull rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="images pulled",
            elapsed=elapsed,
        )


# ---------- Step 4: model download ----------


class ModelDownloadStep(InstallStep):
    """Download up to 3 GGUF models from HF (LLM + Embed + Rerank).

    Phase M5.1: каждая role (llm/embed/rerank) скачивается отдельным
    call'ом — pair (repo, file) of empty/None → skipped. Order: LLM →
    Embed → Rerank (LLM blocking, embeds tiny, rerank optional).

    Detect logic per file (skip re-download если модель уже скачана):
      1. `{models_dir}/{file}` (default /var/lib/agmind/models/) → reuse
      2. User fallback `~/.local/share/agmind/models/{file}` → move в models_dir
      3. None of above → curl download с resume support

    Минимальный размер чтобы считать "real model" = 100 MiB. Embed/rerank
    модели могут быть < 100 MiB — для них порог снижен до 10 MiB.
    """

    step_id = "model_pull"
    label = "Model download"

    PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    # M4.6: curl --progress-bar also writes speed/ETA, parse it for richer events
    SPEED_RE = re.compile(r"(\d+\.?\d*)\s*([KMG])\s")
    MIN_VALID_SIZE = 100 * 1024 * 1024  # 100 MiB — filter empty placeholders / partial
    MIN_VALID_SIZE_SMALL = 10 * 1024 * 1024  # 10 MiB — для embed/rerank (BGE-M3 = 600 MiB)
    DISK_SPACE_BUFFER_BYTES = 256 * 1024 * 1024

    @staticmethod
    def _fallback_dirs(config: InstallConfig) -> list[Path]:
        """Other locations to check for already-downloaded model."""
        from os.path import expanduser

        candidates = [
            Path(expanduser("~/.local/share/agmind/models")),  # XDG user fallback
            # Future: Hugging Face HOME cache directory if user has model there.
        ]
        # Drop duplicates / models_dir itself
        seen = {config.models_dir.resolve()}
        out: list[Path] = []
        for c in candidates:
            r = c.resolve() if c.exists() else c
            if r in seen:
                continue
            seen.add(r)
            out.append(c)
        return out

    def _detect_existing(
        self,
        models_dir: Path,
        file_name: str,
        min_size: int,
        config: InstallConfig,
    ) -> tuple[Path | None, str]:
        """Return (path, status_msg) — где модель уже есть. None если nowhere."""
        from agmind.models import safe_model_target

        target = safe_model_target(models_dir, file_name)
        if target.exists() and target.stat().st_size >= min_size:
            return target, f"already present в {target.parent}"
        for fb in self._fallback_dirs(config):
            cand = safe_model_target(fb, file_name)
            if cand.exists() and cand.stat().st_size >= min_size:
                return cand, f"found in fallback {fb}"
        return None, "not present anywhere"

    @staticmethod
    def _expected_download_size_bytes(repo: str, file_name: str, min_size: int) -> int:
        """Best-effort expected download size without network calls."""
        try:
            from agmind.install.models import CURATED_MODELS
        except Exception:  # noqa: BLE001
            return min_size
        for entry in CURATED_MODELS:
            if entry.repo == repo and entry.file == file_name and entry.size_gib > 0:
                return int(entry.size_gib * 1024 * 1024 * 1024)
        return min_size

    def _check_model_disk_space(
        self,
        *,
        role: str,
        repo: str,
        file_name: str,
        target: Path,
        partial: Path,
        min_size: int,
    ) -> str | None:
        expected_size = self._expected_download_size_bytes(repo, file_name, min_size)
        partial_size = partial.stat().st_size if partial.exists() else 0
        remaining = max(expected_size - partial_size, min_size)
        buffer = max(self.DISK_SPACE_BUFFER_BYTES, expected_size // 20)
        free = shutil.disk_usage(target.parent).free
        if free >= remaining + buffer:
            return None
        free_mb = free // (1024 * 1024)
        needed_mb = (remaining + buffer) // (1024 * 1024)
        return (
            f"{role}: not enough free space in {target.parent} for {file_name}: "
            f"{free_mb} MiB free, need at least {needed_mb} MiB"
        )

    def _download_one(
        self,
        role: str,
        repo: str | None,
        file_name: str | None,
        config: InstallConfig,
        callback: ProgressCallback,
        revision: str | None = None,
    ) -> tuple[bool, str]:
        """Download single (repo, file). Returns (success, message)."""
        if not repo or not file_name:
            return True, f"{role}: no model — skipped"

        from agmind.models import hf_resolve_url, safe_model_target

        min_size = self.MIN_VALID_SIZE if role == "llm" else self.MIN_VALID_SIZE_SMALL
        try:
            target = safe_model_target(config.models_dir, file_name)
            # revision pins /resolve/<rev>/ (immutable); None → mutable main (back-compat).
            url = hf_resolve_url(repo, file_name, revision=revision)
        except ValueError as exc:
            return False, f"{role}: {exc}"
        config.models_dir.mkdir(parents=True, exist_ok=True)

        existing, status = self._detect_existing(config.models_dir, file_name, min_size, config)
        if existing is not None:
            size_mb = existing.stat().st_size // (1024 * 1024)
            if existing == target:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        f"{role}: skip download {existing} ({size_mb} MiB) — {status}",
                    )
                )
                return True, f"{role}: reused {size_mb} MiB"
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"{role}: moving {existing} → {target} (saves re-download {size_mb} MiB)",
                )
            )
            try:
                shutil.move(str(existing), str(target))
            except OSError as exc:
                try:
                    _copy_file_atomic(existing, target)
                    existing.unlink()
                except OSError as exc2:
                    return False, f"{role}: cannot relocate model: {exc2} (initial: {exc})"
            return True, f"{role}: relocated {size_mb} MiB"

        # Not present anywhere. In air-gap (AGMIND_OFFLINE) the curl download below cannot run —
        # fast-fail with the exact path the operator must pre-stage, rather than a confusing
        # curl network error after a long hang (review MEDIUM model-download-no-offline-fastfail).
        if _offline_install_enabled():
            return (
                False,
                f"{role}: AGMIND_OFFLINE and model not present — pre-stage '{file_name}' at "
                f"{target} (or {config.models_dir}/); air-gap installs do not download from HF.",
            )

        partial = target.with_name(f".{target.name}.part")
        if target.is_file() and target.stat().st_size < min_size:
            try:
                if partial.exists():
                    target.unlink()
                else:
                    target.replace(partial)
            except OSError as exc:
                return False, f"{role}: cannot stage partial model download: {exc}"
        disk_error = self._check_model_disk_space(
            role=role,
            repo=repo,
            file_name=file_name,
            target=target,
            partial=partial,
            min_size=min_size,
        )
        if disk_error is not None:
            return False, disk_error
        if shutil.which("curl") is None:
            return False, f"{role}: curl not found on PATH (required to download models)"
        cmd = [
            "curl",
            "-fL",
            "-C",
            "-",
            "-o",
            str(partial),
            "--progress-bar",
            # Network stall guards: this download streams through an uncancellable
            # worker thread, so a half-open HF socket with no timeout would hang the
            # whole TUI. Fail fast on a dead connect (30s) or a transfer that drops
            # below 1 KiB/s for 60s; do NOT set --max-time (slow-but-progressing
            # multi-GB downloads must still succeed).
            "--connect-timeout",
            "30",
            "--speed-limit",
            "1024",
            "--speed-time",
            "60",
            "--retry",
            "3",
            "--retry-connrefused",
            url,
        ]
        last_pct = [-1]

        def parse_curl_pct(line: str) -> None:
            m = self.PROGRESS_RE.search(line)
            if not m:
                return
            try:
                pct = int(float(m.group(1)))
            except (ValueError, IndexError):
                return
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            speed_m = self.SPEED_RE.search(line)
            speed_label = ""
            if speed_m:
                speed_label = f" @ {speed_m.group(1)}{speed_m.group(2)}/s"
            try:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.PROGRESS,
                        f"{role} download {pct}%{speed_label}",
                        pct=pct,
                    )
                )
            except (ValueError, IndexError):
                pass

        rc, _ = _stream_subprocess(
            cmd, callback, self.step_id, extra_emit=parse_curl_pct, cancel_event=self.cancel_event
        )
        if rc != 0:
            # Genuine interrupt/error (incl. cancel): keep the partial so a retry can
            # `curl -C -` resume it instead of re-downloading from scratch.
            return False, f"{role}: curl rc={rc} (download failed)"
        partial_size = partial.stat().st_size if partial.exists() else 0
        if partial_size < min_size:
            # curl reported success (rc=0) yet the file is too small — an HF
            # redirect/resume glitch or an error body, NOT a resumable partial. Leaving
            # it would poison the next `curl -C -` (the real "100% then 0 MiB" loop), so
            # clear it; the retry then starts clean and downloads correctly.
            with contextlib.suppress(OSError):
                partial.unlink()
            size_mb = partial_size // (1024 * 1024)
            min_mb = min_size // (1024 * 1024)
            return False, (
                f"{role}: downloaded file too small ({size_mb} MiB < {min_mb} MiB); "
                "cleared partial for retry"
            )
        # Integrity (audit H#10): a curl rc=0 can still yield a truncated/error body that
        # clears the absolute min_size floor yet is far below the real model size. When the
        # curated catalog knows the expected size, reject anything grossly short of it.
        expected = self._expected_download_size_bytes(repo, file_name, min_size)
        if expected > min_size and partial_size < int(expected * 0.88):
            with contextlib.suppress(OSError):
                partial.unlink()
            return False, (
                f"{role}: downloaded {partial_size // (1024 * 1024)} MiB but expected "
                f"~{expected // (1024 * 1024)} MiB (>12% short — likely truncated); cleared partial"
            )
        partial.replace(target)
        size_mb = target.stat().st_size // (1024 * 1024)
        return True, f"{role}: downloaded {size_mb} MiB → {target.name}"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()

        # The models dir lives under a root-owned runtime root; make it user-writable
        # (via sudo if needed) before downloading, else mkdir/curl fail with [Errno 13].
        try:
            _ensure_models_dir(config, callback, self.step_id)
        except (OSError, PermissionError) as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot prepare models dir {config.models_dir}: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        roles = (
            ("llm", config.model_repo, config.model_file, config.model_revision),
            ("embed", config.embed_repo, config.embed_file, config.embed_revision),
            ("rerank", config.rerank_repo, config.rerank_file, config.rerank_revision),
        )

        messages: list[str] = []
        for role, repo, file_name, revision in roles:
            ok, msg = self._download_one(role, repo, file_name, config, callback, revision=revision)
            messages.append(msg)
            if not ok:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=msg,
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="; ".join(messages),
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


# ---------- Step 5: compose deploy ----------


class DeployStep(InstallStep):
    """Run `agmind deploy --apply` (reuse Phase L.B runner)."""

    # First-run model load (multi-GB GGUF -> unified memory) can take many minutes;
    # the runner default of 300s would false-rollback an otherwise-healthy stack.
    HEALTHCHECK_TIMEOUT = 900

    step_id = "deploy"
    label = "Deploy compose stack + healthcheck"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for deploy",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        from agmind.deploy.runner import deploy as _deploy

        def deploy_progress(step: str, msg: str) -> None:
            safe_step = _redact_install_secrets(step, config)
            safe_msg = _redact_install_secrets(msg, config)
            callback(_make_event(self.step_id, ProgressKind.LOG, f"[{safe_step}] {safe_msg}"))

        try:
            result = _deploy(
                profiles=[],  # render по services list, см. config
                install_dir=config.install_dir,
                domain=config.domain,
                apply=True,
                no_prompt=True,
                progress=deploy_progress,
                services=config.services,
                sudo_password=config.sudo_password,
                # First-run deploy must outlast a multi-GB LLM load; the runner default
                # (300s) false-rolls-back an otherwise-healthy stack (BREA02).
                healthcheck_timeout=self.HEALTHCHECK_TIMEOUT,
                # Let Cancel break out of the long healthcheck wait promptly.
                cancel_event=self.cancel_event,
            )
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"deploy crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if not result.success:
            extra = " (rolled back)" if result.rollback_performed else ""
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"{result.message}{extra}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=result.message,
            elapsed=elapsed,
        )


def _check_proxmox_config_staged(config: InstallConfig) -> str | None:
    """proxmox-exporter mounts ``/etc/agmind/proxmox-exporter/pve.yml`` :ro (a single FILE).
    ``agmind install`` never collects PVE credentials, so unless the operator provisioned that
    file (the ansible ``services`` role writes it from ``agmind_proxmox_exporter_*`` vars) Docker
    creates a DIRECTORY at the :ro source and the exporter crash-loops. Fail fast with guidance
    instead of shipping a crash-loop (review MEDIUM proxmox-pve-config-not-staged)."""
    if "proxmox-exporter" not in config.services:
        return None
    pve = config.config_dir / "proxmox-exporter" / "pve.yml"
    if pve.is_file():
        return None
    return (
        f"proxmox-exporter selected but {pve} is not provisioned. `agmind install` does not "
        "collect Proxmox API credentials — provision that file first (the ansible `services` "
        "role writes it from agmind_proxmox_exporter_user / token_name / token_value), or "
        "deselect the proxmox profile."
    )


# ---------- step list factory ----------


class EnvWriteStep(InstallStep):
    """Write `/opt/agmind/.env` с runtime settings (model file, ctx, KV cache).

    `templates/services/llama-llm.yaml` ссылается на эти env vars через
    docker compose ${VAR} substitution.
    """

    step_id = "env_write"
    label = "Write runtime .env"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        env_path = config.install_dir / ".env"
        try:
            version_env_text = _runtime_version_env(config.services)
        except ValueError as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=str(exc),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        proxmox_error = _check_proxmox_config_staged(config)
        if proxmox_error is not None:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=proxmox_error,
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        runtime_env = _runtime_env(_parse_existing_runtime_env(config, env_path))

        # Phase M5.2: separate LLM/Embed/Rerank env vars так чтобы templates
        # параметризовали каждый llama-* service независимо. Legacy AGMIND_CTX_SIZE
        # сохраняется для backward compat с уже-deployed compose stacks (template
        # llama-llm.yaml имеет ${AGMIND_CTX_SIZE:-fallback}).
        lines = [
            "# AGmind runtime env — written by `agmind install` Phase M5.2.",
            "# Hand-edit allowed; existing runtime secrets are preserved on rerun.",
            _env_line("AGMIND_DOMAIN", config.domain),
            "",
            "# ---- LLM (token generation) ----",
            _env_line("AGMIND_MODEL_FILE", config.model_file or ""),
            _env_line("AGMIND_LLM_CTX_SIZE", str(config.ctx_size)),
            _env_line("AGMIND_LLM_KV_CACHE", config.kv_cache_type),
            _env_line("AGMIND_LLM_THREADS", str(config.threads)),
            _env_line("AGMIND_LLM_PARALLEL", str(config.parallel_slots)),
            "",
            "# Legacy aliases (pre-M5.2 templates) — same values as LLM block.",
            _env_line("AGMIND_CTX_SIZE", str(config.ctx_size)),
            _env_line("AGMIND_KV_CACHE", config.kv_cache_type),
            _env_line("AGMIND_THREADS", str(config.threads)),
            _env_line("AGMIND_PARALLEL", str(config.parallel_slots)),
            "",
            "# ---- Embed (dense embeddings for RAG) ----",
            _env_line("AGMIND_EMBED_FILE", config.embed_file or ""),
            _env_line("AGMIND_EMBED_CTX_SIZE", str(config.embed_ctx_size)),
            _env_line("AGMIND_EMBED_KV_CACHE", config.embed_kv_cache),
            _env_line("AGMIND_EMBED_PARALLEL", str(config.embed_parallel)),
            "",
            "# ---- Rerank (cross-encoder ordering) ----",
            _env_line("AGMIND_RERANK_FILE", config.rerank_file or ""),
            _env_line("AGMIND_RERANK_CTX_SIZE", str(config.rerank_ctx_size)),
            "",
            "# ---- Alerting (optional Telegram receiver) ----",
            _env_line(
                "AGMIND_ALERT_TELEGRAM_CHAT_ID",
                runtime_env["AGMIND_ALERT_TELEGRAM_CHAT_ID"],
            ),
            _env_line(
                "AGMIND_ALERT_TELEGRAM_BOT_TOKEN",
                runtime_env["AGMIND_ALERT_TELEGRAM_BOT_TOKEN"],
            ),
            "# ---- Alerting (optional email + webhook channels; set then re-run install) ----",
            _env_line("SMTP_SMARTHOST", runtime_env["SMTP_SMARTHOST"]),
            _env_line("SMTP_FROM", runtime_env["SMTP_FROM"]),
            _env_line("SMTP_TO", runtime_env["SMTP_TO"]),
            _env_line("SMTP_AUTH_USERNAME", runtime_env["SMTP_AUTH_USERNAME"]),
            _env_line("SMTP_AUTH_PASSWORD", runtime_env["SMTP_AUTH_PASSWORD"]),
            _env_line("ALERT_WEBHOOK_URL", runtime_env["ALERT_WEBHOOK_URL"]),
            "",
            "# ---- Runtime service credentials (Compose requires non-empty values) ----",
            _env_line("POSTGRES_PASSWORD", runtime_env["POSTGRES_PASSWORD"]),
            _env_line("GRAFANA_PASSWORD", runtime_env["GRAFANA_PASSWORD"]),
            _env_line("MYSQL_ROOT_PASSWORD", runtime_env["MYSQL_ROOT_PASSWORD"]),
            _env_line("MINIO_ROOT_USER", runtime_env["MINIO_ROOT_USER"]),
            _env_line("MINIO_ROOT_PASSWORD", runtime_env["MINIO_ROOT_PASSWORD"]),
            _env_line("REDIS_PASSWORD", runtime_env["REDIS_PASSWORD"]),
            _env_line("N8N_ENCRYPTION_KEY", runtime_env["N8N_ENCRYPTION_KEY"]),
            _env_line("N8N_TIMEZONE", runtime_env["N8N_TIMEZONE"]),
            _env_line(
                "HOMARR_SECRET_ENCRYPTION_KEY",
                runtime_env["HOMARR_SECRET_ENCRYPTION_KEY"],
            ),
            _env_line(
                "DIFY_PLUGIN_DAEMON_KEY",
                runtime_env["DIFY_PLUGIN_DAEMON_KEY"],
            ),
            _env_line(
                "DIFY_PLUGIN_INNER_API_KEY",
                runtime_env["DIFY_PLUGIN_INNER_API_KEY"],
            ),
            "",
            "# ---- Authelia (security profile) — read by the container as AUTHELIA_* ----",
            _env_line("AUTHELIA_SESSION_SECRET", runtime_env["AUTHELIA_SESSION_SECRET"]),
            _env_line(
                "AUTHELIA_STORAGE_ENCRYPTION_KEY",
                runtime_env["AUTHELIA_STORAGE_ENCRYPTION_KEY"],
            ),
            _env_line(
                "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
                runtime_env["AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET"],
            ),
        ]
        # Divergence guard (Правило #11): the hand-maintained list above must never
        # silently drop a generated secret when a new key is added to secret_keys.py.
        # Append every generated secret not already emitted, so EnvWriteStep and
        # RUNTIME_SECRET_KEYS cannot diverge — the exact gap that let the KOMODO
        # ops-profile secrets fall through (compose `${VAR:?}` then reds on a fresh
        # ops deploy while CI hand-injected the values and stayed green).
        _emitted = {
            ln.split("=", 1)[0] for ln in lines if "=" in ln and not ln.lstrip().startswith("#")
        }
        _ungrouped = [
            key for key in (*_RUNTIME_SECRET_KEYS, *_AUTHELIA_SECRET_KEYS) if key not in _emitted
        ]
        if _ungrouped:
            lines.append("")
            lines.append("# ---- Additional generated secrets (divergence guard) ----")
            lines.extend(_env_line(key, runtime_env[key]) for key in _ungrouped)
        env_text = "\n".join(lines) + "\n"
        try:
            _write_runtime_payload_local(config, env_text, version_env_text, callback, self.step_id)
        except PermissionError as exc:
            if config.sudo_password is None:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"cannot write runtime files without sudo: {exc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            try:
                _write_runtime_payload_sudo(
                    config, env_text, version_env_text, callback, self.step_id
                )
            except OSError as sudo_exc:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"sudo runtime file write failed: {sudo_exc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
        except OSError as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot write runtime files: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        callback(
            _make_event(
                self.step_id,
                ProgressKind.LOG,
                f"wrote {env_path} with model={config.model_file} ctx={config.ctx_size}",
            )
        )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f".env written ({len(lines)} vars)",
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


class CredentialsStep(InstallStep):
    """Write ``${install_dir}/credentials.txt`` (chmod 600) from descriptors + rendered ``.env``.

    Final, best-effort step: gives the operator a persistent, human-readable list of URLs /
    logins / passwords plus copy-paste OpenAI-compatible model-endpoint blocks. Never fails the
    install — the stack is already deployed; this is a convenience artifact.
    """

    step_id = "credentials"
    label = "Write credentials.txt"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from datetime import UTC, datetime

        from agmind.services.access import build_access_report, render_credentials_txt
        from agmind.services.renderer import load_descriptors

        try:
            selected = set(config.services)
            descriptors = {n: d for n, d in load_descriptors().items() if n in selected}
            env_path = config.install_dir / ".env"
            # Read via the sudo-aware helper, NOT bare parse_env_file: the .env is root:root 0600
            # (written through the sudo payload path), so a non-root install user hits
            # PermissionError and credentials.txt is silently skipped — the operator then has no
            # record of the generated passwords. _parse_existing_runtime_env falls back to
            # `sudo cat` with the install's sudo password.
            env = _parse_existing_runtime_env(config, env_path) if env_path.exists() else {}
            report = build_access_report(descriptors, env, domain=config.domain)
            generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            text = render_credentials_txt(
                report, generated_at=generated_at, llama_model=config.model_file
            )
            creds_path = config.install_dir / "credentials.txt"
            _write_private_text_maybe_sudo(config, creds_path, text, callback, self.step_id)
        except Exception as exc:  # noqa: BLE001 — never fail install on a convenience artifact
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message=f"credentials.txt skipped ({exc})",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"credentials.txt written ({len(report)} endpoints)",
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


def default_steps() -> list[InstallStep]:
    """Stock install pipeline. Order matters."""
    return [
        DoctorStep(),
        CloudflareTokenStep(),
        BootstrapStep(),
        EnvWriteStep(),  # before compose/deploy — compose parses ${VAR:?} guards
        ComposeConfigStep(),  # fail fast before deploy runner pulls images
        ModelDownloadStep(),
        DeployStep(),
        CredentialsStep(),  # final: persist credentials.txt for the operator
    ]


__all__ = [
    "BootstrapStep",
    "CloudflareTokenStep",
    "ComposeConfigStep",
    "CredentialsStep",
    "DeployStep",
    "DoctorStep",
    "EnvWriteStep",
    "ImagePullStep",
    "ModelDownloadStep",
    "default_steps",
]
