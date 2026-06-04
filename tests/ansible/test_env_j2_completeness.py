"""Review MEDIUM ansible-env-j2-secret-divergence: the Ansible `services` role's env.j2 must
generate EVERY catalog-required ${VAR:?} secret — and with the right VALUE FORMAT. The Python
installer (EnvWriteStep) has a divergence guard; env.j2 is the parallel generator on the
`ansible-playbook install.yml -t services` path and was silently missing 8 secrets, so any
security/ui/ops profile crashed at the role's own `docker compose config` gate."""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]
_ENV_J2 = _REPO / "ansible" / "roles" / "services" / "templates" / "env.j2"
_SERVICES_DIR = _REPO / "templates" / "services"

# 64-hex format key (must match agmind.install.secret_keys._HEX_SECRET_KEYS).
_HEX_KEYS = {"HOMARR_SECRET_ENCRYPTION_KEY"}
# 64-char format keys (Authelia session/storage/jwt).
_LONG_KEYS = {
    "AUTHELIA_SESSION_SECRET",
    "AUTHELIA_STORAGE_ENCRYPTION_KEY",
    "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
}


def _catalog_required_vars() -> set[str]:
    """Every ${VAR:?} (required, no default) interpolated across the descriptor catalog."""
    required: set[str] = set()
    for path in glob.glob(str(_SERVICES_DIR / "*.yaml")):
        text = Path(path).read_text(encoding="utf-8")
        for match in re.finditer(r"\$\{([A-Z][A-Z0-9_]*)(:[?-][^}]*)?\}", text):
            suffix = match.group(2) or ""
            if suffix.startswith(":?"):
                required.add(match.group(1))
    return required


def _env_j2_lines() -> dict[str, str]:
    """Map each emitted KEY → its raw RHS (for format assertions)."""
    out: dict[str, str] = {}
    for raw in _ENV_J2.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, rhs = line.partition("=")
        out[key.strip()] = rhs
    return out


def test_env_j2_covers_every_catalog_required_secret() -> None:
    emitted = set(_env_j2_lines())
    required = _catalog_required_vars()
    missing = sorted(required - emitted)
    assert not missing, (
        f"env.j2 does not generate these catalog-required ${{VAR:?}} secrets: {missing}.\n"
        "Add them (with the matching value format) — a security/ui/ops `-t services` deploy "
        "crashes at `docker compose config` without them."
    )


def test_env_j2_uses_64_hex_for_homarr() -> None:
    """The homarr key must be 64 LOWERCASE hex — the alnum/base64 default aborts homarr boot."""
    rhs = _env_j2_lines()["HOMARR_SECRET_ENCRYPTION_KEY"]
    assert "length=64" in rhs
    assert "chars=digits,abcdef" in rhs or "chars=hexdigits" in rhs, rhs


def test_env_j2_uses_64_char_for_authelia_secrets() -> None:
    lines = _env_j2_lines()
    for key in _LONG_KEYS:
        assert "length=64" in lines[key], f"{key} must be 64-char: {lines[key]}"


def test_format_buckets_match_installer_source_of_truth() -> None:
    """Guard the format buckets above against agmind.install.secret_keys so this test and the
    installer cannot silently disagree on which keys are hex / 64-char."""
    from agmind.install import secret_keys

    assert set(secret_keys._HEX_SECRET_KEYS) == _HEX_KEYS
    assert set(secret_keys.AUTHELIA_SECRET_KEYS) == _LONG_KEYS
