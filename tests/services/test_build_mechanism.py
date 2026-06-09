"""Tests for the descriptor ``build:`` mechanism — AGmind-authored images built on-host.

A service with a ``build:`` block is built locally from shipped source (compose-native
``build:``) instead of pulled from a registry, so it carries no registry digest and is exempt
from the fail-closed digest-pin gate. This backs the agent-core profiles, which ship AGmind's
own FastAPI images without a registry/publish step.
"""

from __future__ import annotations

import pytest

from agmind.schemas.service import BuildConfig, ServiceDescriptor
from agmind.services.renderer import descriptor_to_compose_service

pytestmark = pytest.mark.backend_any


def _build_svc() -> ServiceDescriptor:
    return ServiceDescriptor(
        name="agent-x",
        image="agmind-agent-x:0.1.0",
        tier="app",
        build=BuildConfig(dockerfile="docker/Dockerfile.agent-x"),
    )


def test_build_descriptor_parses_without_digest() -> None:
    d = _build_svc()
    assert d.build is not None
    assert d.build.context == "."  # default = repo root
    assert d.build.dockerfile == "docker/Dockerfile.agent-x"
    assert d.digest is None
    # fq_image is the plain local tag (no @sha256) — what `docker compose up --build` produces.
    assert d.fq_image() == "agmind-agent-x:0.1.0"


def test_build_config_requires_dockerfile() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BuildConfig()  # type: ignore[call-arg]  # dockerfile is required


def test_renderer_emits_compose_build_block() -> None:
    from agmind.services.renderer import REPO_ROOT

    svc = descriptor_to_compose_service(_build_svc())
    # The context is resolved to the ABSOLUTE repo path (not the descriptor's relative ".") so the
    # on-host build finds docker/ + services/ at deploy time — compose runs with cwd=install_dir
    # (/opt/agmind), which does NOT contain the source tree. A relative "." would break the build.
    assert svc["build"]["context"] == str(REPO_ROOT.resolve())
    assert svc["build"]["dockerfile"] == "docker/Dockerfile.agent-x"
    assert svc["image"] == "agmind-agent-x:0.1.0"


def test_non_build_service_emits_no_build_block() -> None:
    d = ServiceDescriptor(name="pulled", image="vendor/x:1.2.3", tier="app", digest="a" * 64)
    svc = descriptor_to_compose_service(d)
    assert "build" not in svc


def test_digest_gate_exempts_build_services_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A build-service with no digest passes; a plain service with no digest still fails."""
    from scripts.checks.digest_check import check_digest_pins

    (tmp_path / "ok-build.yaml").write_text(
        "name: ok-build\nimage: agmind-ok:0.1.0\ntier: app\n"
        "build:\n  dockerfile: docker/Dockerfile.ok\n",
        encoding="utf-8",
    )
    (tmp_path / "bad-nodigest.yaml").write_text(
        "name: bad-nodigest\nimage: vendor/bad:1.0\ntier: app\n",
        encoding="utf-8",
    )
    issues, count = check_digest_pins(services_dir=tmp_path)
    assert count == 2
    flagged = {i["kind"] and i.get("service") or i for i in issues}  # tolerate issue shape
    names = {i.get("service") for i in issues}
    assert "bad-nodigest" in names, f"plain digest-less service must still fail: {issues}"
    assert "ok-build" not in names, f"build-service must be exempt: {issues}"
    assert flagged  # at least one issue (the bad one)
