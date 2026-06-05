"""Live-audit 2026-06-05 (MED dify-plugin-daemon-no-storage-volume): the daemon has
PLUGIN_WORKING_PATH=/app/storage/cwd but NO volume for /app/storage, so installed Dify
plugins live on the ephemeral container layer and are LOST on every restart/recreate."""

from __future__ import annotations

import pytest

from agmind.services.renderer import descriptor_to_compose_service, load_descriptors

pytestmark = pytest.mark.backend_any


def test_dify_plugin_daemon_persists_storage() -> None:
    d = load_descriptors()["dify-plugin-daemon"]
    targets = {vol.split(":")[1] for vol in d.volumes if ":" in vol}
    assert "/app/storage" in targets, "plugin storage must be a persistent bind, not ephemeral"
    # bind under the agmind data root (rule 5: no anonymous/ephemeral data)
    storage = next(v for v in d.volumes if v.split(":")[1] == "/app/storage")
    assert storage.split(":")[0].startswith("/var/lib/agmind/"), storage
    # the working path the daemon writes to is under the persisted mount
    assert d.env["PLUGIN_WORKING_PATH"].startswith("/app/storage")


def test_dify_plugin_storage_renders_into_compose() -> None:
    svc = descriptor_to_compose_service(load_descriptors()["dify-plugin-daemon"])
    assert any(str(v).endswith(":/app/storage") for v in svc.get("volumes", []))
