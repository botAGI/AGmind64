"""node-exporter must read the host textfile directory where the AMD GPU exporter
(scripts/ops/amdgpu_textfile.sh) writes amdgpu.prom — otherwise the GPU metrics are
generated on the host but never reach Prometheus.

NB: amdgpu_textfile.sh writes to ``${OUTPUT}.tmp`` then ``mv`` into place — an atomic
within-directory rename, which is the Prometheus-recommended textfile-collector pattern
(node-exporter re-globs the dir each scrape). That script is correct and unchanged; the gap
is purely node-exporter not being pointed at the textfile dir.
"""

from __future__ import annotations

import pytest

from agmind.services.renderer import DEFAULT_SERVICES_DIR, load_descriptors

pytestmark = pytest.mark.backend_any

_TEXTFILE_FLAG = "--collector.textfile.directory="


def test_node_exporter_enables_textfile_collector_directory() -> None:
    node_exporter = load_descriptors(DEFAULT_SERVICES_DIR)["node-exporter"]
    command = node_exporter.command or []
    assert any(arg.startswith(_TEXTFILE_FLAG) for arg in command), command


def test_node_exporter_mounts_the_textfile_directory() -> None:
    node_exporter = load_descriptors(DEFAULT_SERVICES_DIR)["node-exporter"]
    command = node_exporter.command or []
    textdir = next(arg[len(_TEXTFILE_FLAG) :] for arg in command if arg.startswith(_TEXTFILE_FLAG))
    # the host dir holding amdgpu.prom must be bind-mounted into the container at that path
    assert any(textdir in volume for volume in node_exporter.volumes), node_exporter.volumes


def test_node_exporter_sets_path_rootfs_for_host_filesystem_metrics() -> None:
    """Review MEDIUM node-exporter-no-rootfs: without --path.rootfs=/host the filesystem
    collector reports the container overlay as mountpoint='/', so DiskSpaceLow never fires."""
    node_exporter = load_descriptors(DEFAULT_SERVICES_DIR)["node-exporter"]
    command = node_exporter.command or []
    assert "--path.rootfs=/host" in command, command
    # and the host root must actually be mounted at /host
    assert any(v.startswith("/:/host") for v in node_exporter.volumes), node_exporter.volumes
