"""Crash-loop blocker: group_add by NAME fails inside minimal GPU images.

The llama.cpp vulkan image has no `render`/`video` entries in its /etc/group.
Docker resolves `group_add` group NAMES against the CONTAINER's group db, so
`group_add: [video, render]` → "Unable to find group render: no matching entries
in group file" → the container never starts → deploy fails.

Fix: the COMPOSE renderer maps the AMD-GPU group names to the host's numeric GID
(resolvable in any container). The DESCRIPTOR keeps the names (the k8s renderer keys
off them for its portability warning).
"""

from __future__ import annotations

import pytest

import agmind.services.renderer as renderer
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any


def _descriptor(**overrides: object) -> ServiceDescriptor:
    base: dict[str, object] = {
        "name": "llama-llm",
        "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049",
        "tier": "inference",
        "purpose": "LLM",
    }
    base.update(overrides)
    return ServiceDescriptor.model_validate(base)


class _Grp:
    def __init__(self, gid: int) -> None:
        self.gr_gid = gid


def test_gpu_group_names_resolved_to_host_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        renderer.grp,
        "getgrnam",
        lambda name: _Grp({"render": 992, "video": 44}[name]),
    )
    svc = renderer.descriptor_to_compose_service(_descriptor(group_add=["video", "render"]))
    assert svc["group_add"] == ["44", "992"], "GPU group names must become host numeric GIDs"


def test_gpu_group_names_fall_back_to_default_gid_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(name: str) -> _Grp:
        raise KeyError(name)

    monkeypatch.setattr(renderer.grp, "getgrnam", _missing)
    svc = renderer.descriptor_to_compose_service(
        _descriptor(group_add=["video", "render", "docker"])
    )
    # GPU groups fall back to sane defaults (numeric, so still resolvable); a
    # non-GPU group name passes through unchanged.
    assert svc["group_add"] == ["44", "992", "docker"]


def test_non_gpu_group_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer.grp, "getgrnam", lambda name: _Grp(1234))
    svc = renderer.descriptor_to_compose_service(_descriptor(group_add=["docker"]))
    assert svc["group_add"] == ["docker"]
