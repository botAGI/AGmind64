"""Phase 09-07 (M8): `wipe_secrets()` must not oversell memory hygiene.

It rebinds `cf_api_token`/`sudo_password` to ""/None — dropping AGmind's references. Python
strings are immutable, so this is NOT zeroization of the secret bytes in memory. The docstring
must say so honestly (the old wording claimed "zero-out … в памяти")."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install.orchestrator import InstallConfig

pytestmark = pytest.mark.backend_any


def _cfg() -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["traefik"],
        install_dir=Path("/tmp/agmind-test"),
        sudo_password="sup3rs3cret",
    )


def test_wipe_secrets_drops_references() -> None:
    cfg = _cfg()
    cfg.wipe_secrets()
    assert cfg.cf_api_token == ""
    assert cfg.sudo_password is None


def test_wipe_secrets_docstring_is_honest_about_immutable_strings() -> None:
    doc = (InstallConfig.wipe_secrets.__doc__ or "").lower()
    assert "immutable" in doc, "must disclose Python strings are immutable (no true zeroization)"
    assert "not" in doc and ("zeroiz" in doc or "erasure" in doc), (
        "must qualify that this is not guaranteed memory erasure"
    )
