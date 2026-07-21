"""Runtime config/secret materialization for `agmind install`.

Split out of the historical single-file ``agmind/install/steps.py``; every name
here is re-exported from the package ``__init__`` so existing imports of
``agmind.install.steps`` are unaffected.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import yaml

from agmind.config.env import write_env
from agmind.core.env import parse_env_text
from agmind.core.proc import sudo_argv
from agmind.install.orchestrator import (
    InstallConfig,
    ProgressCallback,
    ProgressKind,
)

from ._common import _make_event, _sudo_stdin_payload

# The install test-suite monkeypatches these helpers on the PACKAGE object
# (`monkeypatch.setattr(steps, "_copy_file_atomic", ...)`, `steps.DEFAULT_REPO_ROOT`,
# `steps.write_private_text`, `steps._stream_subprocess`, `steps._run_sudo_runtime_command`)
# and expects the staging/sudo chain below to pick the patched version up — the behaviour
# a single-module `steps.py` gave for free via shared module globals. Resolving them
# through the package module object at CALL time (instead of binding them into this
# module's globals at import time) keeps that contract intact after the package split.
# The package is always in ``sys.modules`` by the time a submodule of it executes.
_steps = sys.modules["agmind.install.steps"]


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
            _steps._copy_file_atomic(item, destination)


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
        _steps._replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        shutil.copy2(source, staged)
        _steps._replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _write_secret_file(path: Path, value: str, reader_uid: int | None = None) -> None:
    secret_dir = path.parent
    if secret_dir.exists() and (secret_dir.is_symlink() or not secret_dir.is_dir()):
        raise OSError(f"runtime secret directory must be a real directory: {secret_dir}")
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_dir.chmod(0o700)
    _steps.write_private_text(path, value)
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
        _steps._copy_file_atomic(observability_dir / "prometheus.yml", staged / "prometheus.yml")
        _steps._copytree_contents(observability_dir / "prometheus" / "rules", staged / "rules")
        _steps._replace_path_atomic(staged, prometheus_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_single_file_config(source: Path, target_dir: Path, target_name: str) -> None:
    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _steps._copy_file_atomic(source, staged / target_name)
        _steps._replace_path_atomic(staged, target_dir)
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
        _steps.write_private_text(staged / "tg_bot_token", bot_token)
        # Only materialize a channel's secret file when that channel is configured,
        # matching the conditional injection in the rendered config above.
        if webhook_url:
            _steps.write_private_text(staged / "webhook_url", webhook_url)
        if smtp_auth_password:
            _steps.write_private_text(staged / "smtp_password", smtp_auth_password)
        _steps._replace_path_atomic(staged, target_dir)
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
        _steps._replace_path_atomic(staged, target_dir)
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
        _steps._copy_file_atomic(squid_src, staged / "squid.conf")
        _steps._replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_directory_contents(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        _steps._copytree_contents(source, staged)
        _steps._replace_path_atomic(staged, target)
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
    templates_dir = _steps.DEFAULT_REPO_ROOT / "templates"
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

    # Authelia (consumer, not a DB server) reads its 4 secrets via the native `_FILE` convention —
    # same single-source registry consumed by rotate-secrets (SPEC-15.4, parity with DB_SECRET_FILES).
    from agmind.install.secret_keys import AUTHELIA_SECRET_FILES

    for svc, fname, env_key in AUTHELIA_SECRET_FILES:
        if svc in selected and runtime_env.get(env_key):
            _write_secret_file(data_dir / "secrets" / fname, runtime_env[env_key])

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
    rc, _ = _steps._stream_subprocess(
        sudo_argv(cmd),
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
        _steps.write_private_text(path, content)
        return
    except PermissionError:
        pass
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=".agmind-creds-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o600)
        _steps._run_sudo_runtime_command(
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
    _steps._run_sudo_runtime_command(
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
    rc, lines = _steps._stream_subprocess(
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
        _steps._run_sudo_runtime_command(
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
        _steps._run_sudo_runtime_command(
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
        _steps._run_sudo_runtime_command(
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
            _steps._run_sudo_runtime_command(
                config,
                ["cp", "-R", "--no-preserve=ownership", f"{staged_data}/.", str(data_dir)],
                callback,
                step_id,
            )
        if staged_config.exists():
            _steps._run_sudo_runtime_command(
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
            _steps._run_sudo_runtime_command(
                config,
                ["chmod", "0700", str(data_dir / "secrets")],
                callback,
                step_id,
            )
            _steps._run_sudo_runtime_command(
                config,
                ["chmod", "0600", str(secret_file)],
                callback,
                step_id,
            )
