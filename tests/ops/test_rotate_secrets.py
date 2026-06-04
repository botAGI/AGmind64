"""agmind ops rotate-secrets — selective .env secret rotation by 4-bucket taxonomy.

Offline-testable core: the bucket classification, the installer-identical
generators (so install/rotate cannot drift), the rotation plan, the in-place
.env rewrite, and the descriptor-derived consumer map. The live
`up -d --force-recreate` of holders is GPU/live-gated and mocked here.
"""

from __future__ import annotations

import re

import pytest

from agmind.install.secret_keys import (
    ALL_GENERATED_SECRET_KEYS,
    AUTHELIA_SECRET_KEYS,
    RUNTIME_SECRET_KEYS,
    classify,
    generate_for,
)
from agmind.ops.rotate import (
    apply_rotation,
    plan_rotation,
    rewrite_env_text,
    secret_consumers,
)

pytestmark = pytest.mark.backend_any


# ---- secret_keys shared module ----


def test_all_generated_keys_cover_runtime_and_authelia() -> None:
    assert set(ALL_GENERATED_SECRET_KEYS) == set(RUNTIME_SECRET_KEYS) | set(AUTHELIA_SECRET_KEYS)
    assert len(ALL_GENERATED_SECRET_KEYS) == 17


def test_every_generated_key_is_classified() -> None:
    for key in ALL_GENERATED_SECRET_KEYS:
        assert classify(key) in ("rotatable", "init_only", "encrypt_at_rest"), key


def test_bucket_membership() -> None:
    assert classify("REDIS_PASSWORD") == "rotatable"
    assert classify("AUTHELIA_SESSION_SECRET") == "rotatable"
    assert classify("POSTGRES_PASSWORD") == "init_only"
    assert classify("GRAFANA_PASSWORD") == "init_only"
    assert classify("N8N_ENCRYPTION_KEY") == "encrypt_at_rest"
    assert classify("AUTHELIA_STORAGE_ENCRYPTION_KEY") == "encrypt_at_rest"
    assert classify("HOMARR_SECRET_ENCRYPTION_KEY") == "encrypt_at_rest"


def test_generators_match_installer_formats() -> None:
    homarr = generate_for("HOMARR_SECRET_ENCRYPTION_KEY")
    assert re.fullmatch(r"[0-9a-f]{64}", homarr), "homarr must be 64 hex chars"
    # Authelia keys use the installer's generate_secret(64) (64 bytes → ≥64 chars,
    # satisfying Authelia's length requirement); longer than the default 32-byte token.
    assert len(generate_for("AUTHELIA_SESSION_SECRET")) >= 64
    assert len(generate_for("AUTHELIA_SESSION_SECRET")) > len(generate_for("REDIS_PASSWORD"))
    assert generate_for("REDIS_PASSWORD") != generate_for("REDIS_PASSWORD")  # random


# ---- rotation plan ----


def _env() -> dict[str, str]:
    return {
        "AGMIND_DOMAIN": "lab.example.com",
        "REDIS_PASSWORD": "oldredis",
        "DIFY_PLUGIN_DAEMON_KEY": "olddaemon",
        "POSTGRES_PASSWORD": "oldpg",
        "N8N_ENCRYPTION_KEY": "oldn8n",
        "AUTHELIA_SESSION_SECRET": "oldsess",
    }


def test_plan_default_rotates_only_rotatable() -> None:
    plan = plan_rotation(_env())
    assert "REDIS_PASSWORD" in plan.rotate
    assert "DIFY_PLUGIN_DAEMON_KEY" in plan.rotate
    assert "AUTHELIA_SESSION_SECRET" in plan.rotate
    assert "POSTGRES_PASSWORD" in plan.skipped_init
    assert "N8N_ENCRYPTION_KEY" in plan.refused_encrypt
    assert "POSTGRES_PASSWORD" not in plan.rotate
    assert "N8N_ENCRYPTION_KEY" not in plan.rotate


def test_plan_include_rotates_init_only() -> None:
    plan = plan_rotation(_env(), include=["POSTGRES_PASSWORD"])
    assert "POSTGRES_PASSWORD" in plan.rotate
    assert plan.warnings, "init-only rotation must warn about the in-DB reset"


def test_plan_force_destructive_rotates_encrypt_at_rest() -> None:
    plan = plan_rotation(_env(), force_destructive=True)
    assert "N8N_ENCRYPTION_KEY" in plan.rotate


def test_apply_rotation_only_changes_planned_keys() -> None:
    env = _env()
    plan = plan_rotation(env)
    new = apply_rotation(env, plan)
    assert new["REDIS_PASSWORD"] != "oldredis"
    assert new["POSTGRES_PASSWORD"] == "oldpg"  # skipped
    assert new["N8N_ENCRYPTION_KEY"] == "oldn8n"  # refused
    assert new["AGMIND_DOMAIN"] == "lab.example.com"  # non-secret untouched


# ---- in-place .env rewrite ----


def test_rewrite_env_text_preserves_comments_and_unrotated() -> None:
    text = "# header\nAGMIND_DOMAIN=lab.example.com\nREDIS_PASSWORD=oldredis\n\nPOSTGRES_PASSWORD=oldpg\n"
    out = rewrite_env_text(text, {"REDIS_PASSWORD": "newredis"})
    assert "# header" in out
    assert "AGMIND_DOMAIN=lab.example.com" in out
    assert "POSTGRES_PASSWORD=oldpg" in out
    assert "REDIS_PASSWORD=newredis" in out
    assert "oldredis" not in out


# ---- consumer map (for force-recreate) ----


def test_secret_consumers_maps_redis_to_multiple_services() -> None:
    from agmind.services.renderer import load_descriptors

    consumers = secret_consumers(load_descriptors())
    assert "REDIS_PASSWORD" in consumers
    holders = set(consumers["REDIS_PASSWORD"])
    assert "redis" in holders
    assert len(holders) >= 3, "REDIS_PASSWORD is a shared secret across many services"


# ---- CLI dry-run ----


def test_rotate_cli_dry_run_makes_no_changes(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    install = tmp_path / "opt"
    install.mkdir()
    env_path = install / ".env"
    env_path.write_text("REDIS_PASSWORD=oldredis\nPOSTGRES_PASSWORD=oldpg\n", encoding="utf-8")

    result = CliRunner().invoke(
        _make_app(),
        ["ops", "rotate-secrets", "--install-dir", str(install), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert (
        env_path.read_text(encoding="utf-8") == "REDIS_PASSWORD=oldredis\nPOSTGRES_PASSWORD=oldpg\n"
    )
    assert "REDIS_PASSWORD" in result.output
