"""Crash-loop blocker: alertmanager shipped `chat_id: 0` (invalid) and crash-looped.

`chat_id: 0` is Go's zero value == unset, so config validation fails on every boot
("missing chat_id or chat_id_file on telegram_config"). Switch to `chat_id_file`
(read at notify-time, NOT at config-load), so the config validates and the process
BOOTS whether or not Telegram is configured — and the chat_id/bot_token are wired
from the runtime .env (AGMIND_ALERT_TELEGRAM_CHAT_ID / _BOT_TOKEN) into mounted files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTMANAGER = _REPO_ROOT / "templates" / "observability" / "alertmanager.yml"


def _telegram_configs() -> list[dict[str, object]]:
    cfg = yaml.safe_load(_ALERTMANAGER.read_text(encoding="utf-8"))
    out: list[dict[str, object]] = []
    for receiver in cfg["receivers"]:
        for tg in receiver.get("telegram_configs", []):
            out.append(tg)
    return out


def test_no_invalid_chat_id_placeholder() -> None:
    raw = _ALERTMANAGER.read_text(encoding="utf-8")
    assert "chat_id: 0" not in raw, "the invalid chat_id:0 placeholder crash-loops alertmanager"


def test_telegram_receivers_use_chat_id_file() -> None:
    tgs = _telegram_configs()
    assert tgs, "expected telegram receivers"
    for tg in tgs:
        assert "chat_id" not in tg, "chat_id:0 must be replaced by chat_id_file"
        assert tg.get("chat_id_file") == "/etc/alertmanager/tg_chat_id"


def test_bot_token_file_in_mounted_config_dir() -> None:
    # /run/secrets is NOT mounted into the container; the config dir IS.
    for tg in _telegram_configs():
        assert tg.get("bot_token_file") == "/etc/alertmanager/tg_bot_token"


def test_stage_alertmanager_writes_env_values_into_mounted_files(tmp_path: Path) -> None:
    from agmind.install.steps import _stage_alertmanager_config

    obs_dir = tmp_path / "observability"
    obs_dir.mkdir()
    (obs_dir / "alertmanager.yml").write_text("receivers: []\n", encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"

    _stage_alertmanager_config(obs_dir, target, chat_id="123456", bot_token="bot:tok")

    assert (target / "alertmanager.yml").exists()
    assert (target / "tg_chat_id").read_text(encoding="utf-8") == "123456"
    assert (target / "tg_bot_token").read_text(encoding="utf-8") == "bot:tok"


def test_stage_alertmanager_handles_unset_env(tmp_path: Path) -> None:
    """Unset telegram env still writes (empty) files so the config-referenced paths
    exist and alertmanager boots; sending is simply a no-op until configured."""
    from agmind.install.steps import _stage_alertmanager_config

    obs_dir = tmp_path / "observability"
    obs_dir.mkdir()
    (obs_dir / "alertmanager.yml").write_text("receivers: []\n", encoding="utf-8")
    target = tmp_path / "etc" / "alertmanager"

    _stage_alertmanager_config(obs_dir, target, chat_id="", bot_token="")

    assert (target / "tg_chat_id").read_text(encoding="utf-8") == ""
    assert (target / "tg_bot_token").read_text(encoding="utf-8") == ""
