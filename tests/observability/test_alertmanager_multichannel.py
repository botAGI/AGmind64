"""Multi-channel Alertmanager: file-backed email + webhook on top of Telegram.

Mirrors the proven Telegram file-backed pattern (NOT the parent's inline-sed
model): webhook and email are injected into each receiver only when configured,
so an unconfigured stack stays byte-equivalent to the Telegram-only base and the
"always boots" invariant holds. Email needs a non-empty smarthost at config-load
(amtool-confirmed, no _file variant) so it is strictly conditional.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTMANAGER = _REPO_ROOT / "templates" / "observability" / "alertmanager.yml"
_AM_IMAGE = "prom/alertmanager:v0.33.1"


def _base_text() -> str:
    return _ALERTMANAGER.read_text(encoding="utf-8")


def _receivers(text: str) -> list[dict[str, object]]:
    return yaml.safe_load(text)["receivers"]


# ---- build_alertmanager_config (pure) ----


def test_no_channels_leaves_config_telegram_only() -> None:
    from agmind.install.steps import build_alertmanager_config

    out = build_alertmanager_config(_base_text())
    for receiver in _receivers(out):
        assert "email_configs" not in receiver
        assert "webhook_configs" not in receiver
        assert receiver["telegram_configs"], "telegram base must survive untouched"


def test_webhook_injects_url_file_into_every_receiver() -> None:
    from agmind.install.steps import build_alertmanager_config

    out = build_alertmanager_config(_base_text(), webhook_url="https://hooks.example/x")
    receivers = _receivers(out)
    assert receivers
    for receiver in receivers:
        hooks = receiver["webhook_configs"]
        assert hooks[0]["url_file"] == "/etc/alertmanager/webhook_url"
        assert "url" not in hooks[0], "must use url_file, never inline url"


def test_email_injected_only_when_smarthost_and_to_present() -> None:
    from agmind.install.steps import build_alertmanager_config

    out = build_alertmanager_config(
        _base_text(),
        smtp_smarthost="smtp.example:587",
        smtp_from="alerts@example",
        smtp_to="ops@example",
        smtp_auth_username="alerts@example",
    )
    for receiver in _receivers(out):
        email = receiver["email_configs"][0]
        assert email["to"] == "ops@example"
        assert email["smarthost"] == "smtp.example:587"
        assert email["auth_username"] == "alerts@example"
        assert email["auth_password_file"] == "/etc/alertmanager/smtp_password"
        assert "auth_password" not in email, "password must be file-backed, never inline"


def test_email_not_injected_without_recipient() -> None:
    from agmind.install.steps import build_alertmanager_config

    out = build_alertmanager_config(_base_text(), smtp_smarthost="smtp.example:587")
    for receiver in _receivers(out):
        assert "email_configs" not in receiver


def test_email_without_username_omits_auth_password_file() -> None:
    from agmind.install.steps import build_alertmanager_config

    out = build_alertmanager_config(
        _base_text(), smtp_smarthost="smtp.example:25", smtp_to="ops@example"
    )
    for receiver in _receivers(out):
        email = receiver["email_configs"][0]
        assert "auth_password_file" not in email
        assert "auth_username" not in email


# ---- _stage_alertmanager_config writes the secret-grade files ----


def test_stage_writes_webhook_and_smtp_files_when_configured(tmp_path: Path) -> None:
    from agmind.install.steps import _stage_alertmanager_config

    obs = tmp_path / "observability"
    obs.mkdir()
    (obs / "alertmanager.yml").write_text(_base_text(), encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"

    _stage_alertmanager_config(
        obs,
        target,
        chat_id="",
        bot_token="",
        webhook_url="https://hooks.example/x",
        smtp_smarthost="smtp.example:587",
        smtp_to="ops@example",
        smtp_auth_username="alerts@example",
        smtp_auth_password="s3cret",
    )

    assert (target / "webhook_url").read_text(encoding="utf-8") == "https://hooks.example/x"
    assert (target / "smtp_password").read_text(encoding="utf-8") == "s3cret"
    rendered = yaml.safe_load((target / "alertmanager.yml").read_text(encoding="utf-8"))
    assert rendered["receivers"][0]["webhook_configs"][0]["url_file"]
    assert rendered["receivers"][0]["email_configs"][0]["to"] == "ops@example"


def test_stage_bearer_secret_files_are_0600(tmp_path: Path) -> None:
    """Telegram bot token, SMTP password, webhook URL are bearer secrets — they must be 0600
    at rest, not rely solely on the /etc/agmind 0750 parent-dir bit (security audit 2026-06-04;
    the sudo `cp --no-preserve=ownership` preserves this source mode)."""
    import stat

    from agmind.install.steps import _stage_alertmanager_config

    obs = tmp_path / "observability"
    obs.mkdir()
    (obs / "alertmanager.yml").write_text(_base_text(), encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"
    _stage_alertmanager_config(
        obs,
        target,
        chat_id="123456",
        bot_token="bot:tok",
        webhook_url="https://hooks.example/x",
        smtp_smarthost="smtp.example:587",
        smtp_to="ops@example",
        smtp_auth_username="alerts@example",
        smtp_auth_password="s3cret",
    )
    for secret in ("tg_bot_token", "webhook_url", "smtp_password"):
        mode = stat.S_IMODE((target / secret).stat().st_mode)
        assert mode == 0o600, f"{secret} mode is {oct(mode)}, expected 0o600 (bearer secret)"


def test_stage_chowns_bearer_secrets_to_alertmanager_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#20: the container runs as nobody (65534) and reads these *_file secrets at notify-time,
    so a root:root 0600 file is unreadable → the alert is silently dropped. They must be chowned
    to 65534 (keeping 0600). Real install runs as root; here os.chown is mocked so the assertion
    is hermetic and does not depend on the test running as root."""
    import os

    from agmind.install.steps import _stage_alertmanager_config

    chowned: dict[str, tuple[int, int]] = {}
    monkeypatch.setattr(os, "chown", lambda p, uid, gid: chowned.update({Path(p).name: (uid, gid)}))

    obs = tmp_path / "observability"
    obs.mkdir()
    (obs / "alertmanager.yml").write_text(_base_text(), encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"
    _stage_alertmanager_config(
        obs,
        target,
        chat_id="123456",
        bot_token="bot:tok",
        webhook_url="https://hooks.example/x",
        smtp_smarthost="smtp.example:587",
        smtp_to="ops@example",
        smtp_auth_username="alerts@example",
        smtp_auth_password="s3cret",
    )

    assert chowned.get("tg_bot_token") == (65534, 65534)
    assert chowned.get("webhook_url") == (65534, 65534)
    assert chowned.get("smtp_password") == (65534, 65534)
    # chat_id / alertmanager.yml must NOT be chowned — they stay 0644 so the config still loads
    assert "tg_chat_id" not in chowned
    assert "alertmanager.yml" not in chowned


def test_stage_omits_channel_files_when_unconfigured(tmp_path: Path) -> None:
    from agmind.install.steps import _stage_alertmanager_config

    obs = tmp_path / "observability"
    obs.mkdir()
    (obs / "alertmanager.yml").write_text(_base_text(), encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"

    _stage_alertmanager_config(obs, target, chat_id="", bot_token="")

    assert not (target / "webhook_url").exists()
    assert not (target / "smtp_password").exists()
    rendered = yaml.safe_load((target / "alertmanager.yml").read_text(encoding="utf-8"))
    assert "email_configs" not in rendered["receivers"][0]
    assert "webhook_configs" not in rendered["receivers"][0]


# ---- EnvWriteStep preserves the new operator-set keys ----


def test_env_write_preserves_smtp_and_webhook_keys(tmp_path: Path) -> None:
    from agmind.core.env import parse_env_file
    from agmind.install.orchestrator import InstallConfig
    from agmind.install.steps import EnvWriteStep

    cfg = InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=["alertmanager"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "lib" / "agmind" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )
    cfg.install_dir.mkdir(parents=True)
    (cfg.install_dir / ".env").write_text(
        "SMTP_SMARTHOST=smtp.example:587\n"
        "SMTP_FROM=alerts@example\n"
        "SMTP_TO=ops@example\n"
        "SMTP_AUTH_USERNAME=alerts@example\n"
        "SMTP_AUTH_PASSWORD=s3cret\n"
        "ALERT_WEBHOOK_URL=https://hooks.example/x\n",
        encoding="utf-8",
    )

    result = EnvWriteStep().run(lambda _event: None, cfg)
    assert result.success, result.message
    env = parse_env_file(cfg.install_dir / ".env")
    assert env["SMTP_SMARTHOST"] == "smtp.example:587"
    assert env["SMTP_TO"] == "ops@example"
    assert env["SMTP_AUTH_PASSWORD"] == "s3cret"
    assert env["ALERT_WEBHOOK_URL"] == "https://hooks.example/x"


# ---- amtool deep validation (image-guarded; the real config-load gate) ----


def _amtool_available() -> bool:
    if shutil.which("docker") is None:
        return False
    inspect = subprocess.run(
        ["docker", "image", "inspect", _AM_IMAGE],
        capture_output=True,
        check=False,
    )
    return inspect.returncode == 0


def _amtool_check(config_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0",
            "-v",
            f"{config_dir}:/etc/alertmanager:ro",
            "--entrypoint",
            "amtool",
            _AM_IMAGE,
            "check-config",
            "/etc/alertmanager/alertmanager.yml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not _amtool_available(), reason=f"{_AM_IMAGE} not present locally")
def test_amtool_accepts_multichannel_and_unconfigured(tmp_path: Path) -> None:
    from agmind.install.steps import _stage_alertmanager_config

    obs = tmp_path / "observability"
    obs.mkdir()
    (obs / "alertmanager.yml").write_text(_base_text(), encoding="utf-8")

    configured = tmp_path / "configured"
    _stage_alertmanager_config(
        obs,
        configured,
        chat_id="123",
        bot_token="bot:tok",
        webhook_url="https://hooks.example/x",
        smtp_smarthost="smtp.example:587",
        smtp_from="alerts@example",
        smtp_to="ops@example",
        smtp_auth_username="alerts@example",
        smtp_auth_password="s3cret",
    )
    res = _amtool_check(configured)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "SUCCESS" in (res.stdout + res.stderr)

    unconfigured = tmp_path / "unconfigured"
    _stage_alertmanager_config(obs, unconfigured, chat_id="", bot_token="")
    res2 = _amtool_check(unconfigured)
    assert res2.returncode == 0, res2.stdout + res2.stderr
