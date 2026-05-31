"""Real-compose smoke lane — H.3.

Purpose
-------
Renders the NON-GPU AGmind subset to a temp compose file, runs
``docker compose -p agmind-ci -f <file> --env-file <ci-env> up -d --wait
--wait-timeout 180``, asserts every container reaches healthy (compose
--wait fails if any container with a healthcheck exits or never becomes
healthy), then ALWAYS runs ``down -v`` in a finally block.

Subset selection
----------------
Profiles: ``core``, ``core,observability``, ``core,rag``  (three lanes)
Exclusions: all service names starting with ``llama-`` (no GPU on the
smoke box; GPU images require /dev/dri + /dev/kfd which the self-hosted
lane may not expose).

Isolation
---------
Project name ``-p agmind-ci`` keeps networks/containers/volumes separate
from any live stack on the same host.  The CI env file carries dummy
passwords (identical to those used in compose-validate).

Opt-in marker
-------------
Module-level ``pytestmark = pytest.mark.integration`` means this file is
EXCLUDED from the default ``pytest`` run (which carries
``addopts = -m "not integration"``).  To run locally::

    pytest -m integration tests/integration/test_compose_up_smoke.py

RED procedure (mutation verification)
--------------------------------------
To prove the lane fails closed on a crash-loop class, reintroduce ONE
incident class — e.g. remove the loki ruler writable-mount so the ruler
dir is EROFS — then render and run the smoke:

    1. In ``templates/services/loki.yaml`` change the ruler volume from
       ``/var/lib/agmind/loki:/loki:rw`` to ``/var/lib/agmind/loki:/loki:ro``
       (or equivalently strip the ``writable_mounts`` declaration).
    2. Run: ``pytest -m integration tests/integration/test_compose_up_smoke.py -v``
    3. Assert the test FAILS (compose exits non-zero because loki's ruler
       sub-process cannot write, making the container never reach healthy).
    4. Revert the change and re-run to confirm GREEN.

This procedure is the self-hosted lane's manual RED evidence (the test
infrastructure verifies the mechanism; the self-hosted runner confirms the
real crash-loop classes).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# CI env file content — mirrors compose-validate's heredoc in ci.yml
# so the rendered compose config --quiet always passes before up.
# ---------------------------------------------------------------------------
_CI_ENV_CONTENT = textwrap.dedent("""\
    POSTGRES_PASSWORD=ci-postgres-password
    GRAFANA_PASSWORD=ci-grafana-password
    MYSQL_ROOT_PASSWORD=ci-mysql-root-password
    MINIO_ROOT_USER=ci-minio
    MINIO_ROOT_PASSWORD=ci-minio-password
    REDIS_PASSWORD=ci-redis-password
    HOMARR_SECRET_ENCRYPTION_KEY=ci-homarr-secret-encryption-key
    N8N_ENCRYPTION_KEY=ci-n8n-encryption-key
    AUTHELIA_SESSION_SECRET=ci-authelia-session-secret-0000000000000000000000000000
    AUTHELIA_STORAGE_ENCRYPTION_KEY=ci-authelia-storage-encryption-key-00000000000000000
    AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET=ci-authelia-jwt-secret-00000000000000000000000000000000
""")

# Non-GPU profile lanes — the three compose profile strings to smoke.
_SMOKE_PROFILES = [
    "core",
    "core,observability",
    "core,rag",
]

# Project isolation name — NEVER collides with the live stack.
_PROJECT_NAME = "agmind-ci"

# Wait timeout in seconds — matches 07-VALIDATION.md spec.
_WAIT_TIMEOUT = 180


def _docker_available() -> bool:
    """Return True when ``docker`` is on PATH and the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def _render_compose(profile: str, output_path: Path, domain: str = "ci.example.com") -> None:
    """Render the compose file for *profile* to *output_path* via agmind CLI."""
    # Import here so the module-level import does not require agmind installed
    # (the test is already gated by the docker skip below).
    from agmind.services.renderer import render_to_string

    profiles = [p.strip() for p in profile.split(",")]
    composed = render_to_string(
        profiles=profiles,
        domain=domain,
    )
    output_path.write_text(composed, encoding="utf-8")


def _filter_llama_services(compose_path: Path) -> None:
    """Remove all llama-* services from the rendered compose file in-place.

    This keeps the non-GPU subset safe to run on a host without /dev/dri
    GPU access.  The filter is intentionally simple: drop any top-level
    service key whose name starts with ``llama-``.
    """
    import yaml  # available because agmind[dev] includes PyYAML

    with compose_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        return

    services = data.get("services", {})
    llama_keys = [k for k in services if k.startswith("llama-")]
    for k in llama_keys:
        del services[k]

    # Also remove dangling depends_on references to llama-* services
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        deps = svc.get("depends_on")
        if isinstance(deps, dict):
            for dep_key in llama_keys:
                deps.pop(dep_key, None)
        elif isinstance(deps, list):
            svc["depends_on"] = [d for d in deps if d not in llama_keys]

    with compose_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)


def _compose_cmd(
    *extra_args: str,
    compose_file: Path,
    env_file: Path,
) -> list[str]:
    """Build a ``docker compose`` command list."""
    return [
        "docker",
        "compose",
        "-p",
        _PROJECT_NAME,
        "-f",
        str(compose_file),
        "--env-file",
        str(env_file),
        *extra_args,
    ]


@pytest.fixture(scope="module")
def _skip_if_no_docker() -> None:
    """Skip the entire module when Docker is unavailable."""
    if not _docker_available():
        pytest.skip("Docker daemon not available — skipping compose-up smoke")


@pytest.mark.parametrize("profile", _SMOKE_PROFILES)
def test_compose_up_smoke(profile: str, _skip_if_no_docker: None) -> None:  # noqa: PT019
    """Render → up --wait → assert returncode 0 → down -v.

    Each lane renders the NON-GPU compose subset, starts it with
    ``--wait --wait-timeout 180`` (compose fails non-zero if any
    healthchecked container does not reach ``healthy`` within the window),
    then tears down unconditionally.

    This test catches the perms / command / config / group_add crash-loop
    classes documented in DEPLOY-BLOCKERS-2026-05-30.md.
    """
    with tempfile.TemporaryDirectory(prefix="agmind-smoke-") as tmpdir:
        tmp = Path(tmpdir)
        compose_file = tmp / f"compose-{profile.replace(',', '_')}.yml"
        env_file = tmp / "agmind-compose-ci.env"

        # Write CI env file
        env_file.write_text(_CI_ENV_CONTENT, encoding="utf-8")

        # Render the compose file
        _render_compose(profile, compose_file)

        # Strip GPU-only services (llama-*)
        _filter_llama_services(compose_file)

        # Belt: validate compose config before up (surface config errors fast)
        config_result = subprocess.run(
            _compose_cmd(
                "config",
                "--quiet",
                compose_file=compose_file,
                env_file=env_file,
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if config_result.returncode != 0:
            pytest.fail(
                f"[{profile}] compose config --quiet failed:\n"
                f"stdout: {config_result.stdout}\n"
                f"stderr: {config_result.stderr}"
            )

        up_result: subprocess.CompletedProcess[str] | None = None
        try:
            up_result = subprocess.run(
                _compose_cmd(
                    "up",
                    "-d",
                    "--wait",
                    f"--wait-timeout={_WAIT_TIMEOUT}",
                    compose_file=compose_file,
                    env_file=env_file,
                ),
                capture_output=True,
                text=True,
                timeout=_WAIT_TIMEOUT + 30,
                env={**os.environ, "AGMIND_CI": "1"},
            )
        finally:
            # ALWAYS tear down — belt-and-suspenders even if up was interrupted.
            subprocess.run(
                _compose_cmd(
                    "down",
                    "-v",
                    compose_file=compose_file,
                    env_file=env_file,
                ),
                capture_output=True,
                timeout=60,
            )

        assert up_result is not None
        assert up_result.returncode == 0, (
            f"[{profile}] compose up --wait failed (returncode={up_result.returncode}).\n"
            f"A container did not reach 'healthy' within {_WAIT_TIMEOUT}s — "
            "check perms/command/config/group_add for crash-loop classes.\n"
            f"stdout: {up_result.stdout[-4000:]}\n"
            f"stderr: {up_result.stderr[-4000:]}"
        )
