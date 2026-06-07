"""Hermeticity fixtures for the config-validate tests.

These tests build a compose containing a DB service (postgres / mysql /
komodo-mongo) and assert ``report.ok``. The A8 secret-file check
(:func:`agmind.config.validation._check_secret_files`) resolves a DB service's
secret to ``_secret_source_path(...)``, which FALLS BACK to
``_DEFAULT_SECRETS_DIR / <filename>`` when the test compose has no matching
secret bind-mount. ``_DEFAULT_SECRETS_DIR`` is the LIVE host path
``/var/lib/agmind/secrets`` — so on a dev box where a real stack created that
dir the check passes, but on a clean GitHub-hosted runner it does not exist and
A8 emits ``secret-file-missing`` (error), breaking every "ok" assertion.

The autouse fixture below points the secrets dir at a per-test tmp directory so
no config-validate test EVER reads the host ``/var/lib/agmind``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_DEFAULT_SECRETS_DIR`` at a per-test tmp dir (host-state isolation).

    Returns the empty tmp secrets dir so tests that WANT a present secret can
    stage one (happy path) and tests that want ``secret-file-missing`` can leave
    it empty (the natural state).
    """
    secrets = tmp_path / "agmind-secrets"
    secrets.mkdir()
    monkeypatch.setattr("agmind.config.validation._DEFAULT_SECRETS_DIR", secrets)
    return secrets
