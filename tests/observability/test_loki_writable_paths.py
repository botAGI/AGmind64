"""Crash-loop blocker: loki's ruler dir was on the read-only config mount.

`ruler.storage.local.directory: /etc/loki/rules` lives under the :ro `/etc/loki`
mount; Loki 3.x unconditionally mkdir's it on startup -> EROFS -> ruler-storage
init fails -> crash loop. Point persistence at the WRITABLE data mount `/loki`
(/var/lib/agmind/loki:/loki). Also fixes a latent data-loss bug: path_prefix /
chunks / rules / compactor pointed at the UNMOUNTED /var/lib/loki (ephemeral layer).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOKI = _REPO_ROOT / "templates" / "observability" / "loki" / "loki.yml"


@pytest.fixture(scope="module")
def loki_cfg() -> dict:
    return yaml.safe_load(_LOKI.read_text(encoding="utf-8"))


def test_ruler_dir_on_writable_mount_not_ro_config(loki_cfg: dict) -> None:
    directory = loki_cfg["ruler"]["storage"]["local"]["directory"]
    assert directory == "/loki/rules", "ruler dir must be on the writable /loki mount, not /etc/loki"


def test_persistence_paths_under_mounted_loki(loki_cfg: dict) -> None:
    common = loki_cfg["common"]
    assert common["path_prefix"] == "/loki"
    assert common["storage"]["filesystem"]["chunks_directory"] == "/loki/chunks"
    assert common["storage"]["filesystem"]["rules_directory"] == "/loki/rules"
    assert loki_cfg["compactor"]["working_directory"] == "/loki/compactor"


def test_no_unmounted_var_lib_loki_paths(loki_cfg: dict) -> None:
    # The host mount is /var/lib/agmind/loki:/loki — in-container /var/lib/loki is NOT
    # mounted, so any persistence path there would be ephemeral (data lost on recreate).
    raw = _LOKI.read_text(encoding="utf-8")
    assert "/var/lib/loki" not in raw
