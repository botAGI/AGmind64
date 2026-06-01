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
    svc = renderer.descriptor_to_compose_service(_descriptor(group_add=["video", "render"]))
    # GPU groups fall back to sane numeric defaults when the render host lacks them.
    assert svc["group_add"] == ["44", "992"]


def test_non_gpu_group_resolves_to_numeric_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any host group (e.g. docker) must render NUMERIC, never pass through as a
    # bare name — a name crashes minimal images ("unable to find group docker").
    monkeypatch.setattr(renderer.grp, "getgrnam", lambda name: _Grp(1234))
    svc = renderer.descriptor_to_compose_service(_descriptor(group_add=["docker"]))
    assert svc["group_add"] == ["1234"]
    assert all(g.isdigit() for g in svc["group_add"]), "group_add must be all-numeric"


def test_unresolvable_group_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A name with no host GID and no known GPU fallback must raise at render time,
    # never silently emit a crash-looping bare NAME into the rendered compose.
    def _missing(name: str) -> _Grp:
        raise KeyError(name)

    monkeypatch.setattr(renderer.grp, "getgrnam", _missing)
    with pytest.raises(ValueError, match="unresolvable NAME"):
        renderer.descriptor_to_compose_service(_descriptor(group_add=["nosuchgroup"]))
