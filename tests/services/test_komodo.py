"""Phase 11 (M8.C) — Komodo operator console catalog contract.

Locks in the facts verified by the live isolated boot (2026-06-04): the 3-service
topology renders under the opt-in ``ops`` profile, the core↔periphery handshake env
that the boot proved is REQUIRED is present, and the secrets are wired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install.secret_keys import RUNTIME_SECRET_KEYS, classify
from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_SERVICES_DIR = Path("templates/services")
_KOMODO = {"komodo-core", "komodo-mongo", "komodo-periphery"}

# amd64 digests verified via `docker manifest inspect ... linux/amd64` (Strix Halo).
_EXPECTED_DIGESTS = {
    "komodo-core": "5c294b803e89f371ef5c12c16880f0379c5d6bbdadb5151d485eb6ca67c6c71f",
    "komodo-mongo": "9e53b28fb3904fa3c68e561902b64ac996a5c71fc56dda279f4964b7f937a749",
    "komodo-periphery": "c4c8395cd0beed03e81088f9c416e04ca6147760b6aaca37a4a042bd4b5dde76",
}


def _komodo_descriptors() -> dict:
    d = load_descriptors(_SERVICES_DIR)
    return {n: s for n, s in d.items() if n in _KOMODO}


def test_three_komodo_services_on_ops_profile() -> None:
    komodo = _komodo_descriptors()
    assert set(komodo) == _KOMODO
    for name, svc in komodo.items():
        assert svc.profiles == ["ops"], f"{name} must be opt-in on the ops profile only"


def test_digests_are_verified_amd64() -> None:
    for name, svc in _komodo_descriptors().items():
        assert svc.digest == _EXPECTED_DIGESTS[name], name


def test_core_carries_periphery_pubkey_env() -> None:
    """Verified live: without KOMODO_PERIPHERY_PUBLIC_KEY core rejects the agent
    ('Core failed to validate Periphery public key') and periphery never registers."""
    core = _komodo_descriptors()["komodo-core"]
    assert core.env.get("KOMODO_PERIPHERY_PUBLIC_KEY") == "file:/config/keys/periphery.pub"
    # Shared keys dir is how core reads the key periphery writes.
    assert any(v.endswith(":/config/keys") for v in core.volumes)


def test_periphery_root_dir_is_agmind_stacks() -> None:
    """Lifecycle A: Komodo only ever sees AGmind-provisioned stacks."""
    periphery = _komodo_descriptors()["komodo-periphery"]
    assert periphery.env.get("PERIPHERY_ROOT_DIRECTORY") == "/opt/agmind/stacks"
    assert "/opt/agmind/stacks:/opt/agmind/stacks" in periphery.volumes


def test_core_and_mongo_share_the_database_password_var() -> None:
    komodo = _komodo_descriptors()
    mongo_pw = komodo["komodo-mongo"].env["MONGO_INITDB_ROOT_PASSWORD"]
    core_pw = komodo["komodo-core"].env["KOMODO_DATABASE_PASSWORD"]
    assert "KOMODO_DATABASE_PASSWORD" in mongo_pw
    assert "KOMODO_DATABASE_PASSWORD" in core_pw


def test_komodo_secrets_are_generated_and_classified() -> None:
    # Tuple membership = _runtime_env() GENERATES a value. That a value also reaches the
    # written .env (the gap that broke the ops deploy) is the LIVE check in
    # tests/services/test_env_completeness.py::test_envwritestep_actually_writes_every_generated_secret.
    for key in (
        "KOMODO_DATABASE_PASSWORD",
        "KOMODO_INIT_ADMIN_PASSWORD",
        "KOMODO_WEBHOOK_SECRET",
        "KOMODO_JWT_SECRET",
    ):
        assert key in RUNTIME_SECRET_KEYS, f"{key} missing from the secret generator set"
        assert classify(key) in ("rotatable", "init_only"), key
