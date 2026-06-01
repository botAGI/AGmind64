"""Real-compose smoke lane — H.3.

Purpose
-------
Renders the NON-GPU AGmind subset to a temp compose file, runs
``docker compose -p agmind-ci -f <file> --env-file <ci-env> up -d --wait
--wait-timeout 180``, asserts every container reaches healthy (compose
--wait fails if any container with a healthcheck exits or never becomes
healthy), then runs a post-wait ``docker compose ps --format json``
assertion that catches no-healthcheck crash-loops (State in restarting/
exited/unhealthy), and ALWAYS runs ``down -v`` in a finally block.

Subset selection
----------------
Profiles: ``core``, ``core,observability``, ``core,rag``,
          ``rag-milvus``, ``rag-weaviate``, ``security``,
          ``automation``, ``ragflow``  (eight lanes)
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

To prove the crash-loop assertion (post-wait ps check) catches no-healthcheck
crash-loops (I.1 evidence for the dify-worker class):

    1. Add ``command: ["false"]`` to any service without a healthcheck, e.g.
       dify-worker in ``templates/services/dify.yaml``.
    2. Render and run the affected smoke lane.
    3. Assert the test FAILS with the crash-looping containers named in the
       assertion message (State=exited or State=restarting).
    4. Revert the command change and re-run to confirm GREEN.

This procedure is the self-hosted lane's manual RED evidence (the test
infrastructure verifies the mechanism; the self-hosted runner confirms the
real crash-loop classes).
"""

from __future__ import annotations

import json
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

# Non-GPU profile lanes — compose profile strings to smoke.
# The first three are the original lanes; the next five cover descriptor fixes
# in Phase 08 (milvus/weaviate/authelia/n8n/ragflow boot-validate) and
# confirm the redis-auth fix in plan 08-04 (ragflow/security lanes must boot
# without AUTH errors).
_SMOKE_PROFILES = [
    "core",
    "core,observability",
    "core,rag",
    "rag-milvus",
    "rag-weaviate",
    "security",
    "automation",
    "ragflow",
]

# Container states that indicate a crash-loop or failed start.
# ``--wait`` is blind to services with no healthcheck: a container that is
# restarting or has already exited after a crash is NOT detected by --wait
# alone (dify-worker had RestartCount=49 and would pass a lucky --wait poll).
_CRASH_LOOP_STATES = frozenset({"restarting", "exited", "unhealthy"})

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
    profile: str = "",
) -> list[str]:
    """Build a ``docker compose`` command list.

    ``profile`` (comma-separated lane, e.g. ``"core,observability"``) activates
    the rendered compose-level ``profiles:`` keys. The renderer stamps EVERY
    service with its AGmind profile(s), so ``docker compose up`` selects nothing
    and errors ``no service selected`` unless the lane's profiles are activated.
    """
    profile_flags: list[str] = []
    for name in (p.strip() for p in profile.split(",") if p.strip()):
        profile_flags += ["--profile", name]
    return [
        "docker",
        "compose",
        *profile_flags,
        "-p",
        _PROJECT_NAME,
        "-f",
        str(compose_file),
        "--env-file",
        str(env_file),
        *extra_args,
    ]


def _parse_ps_json(stdout: str) -> list[dict[str, object]]:
    """Parse ``docker compose ps --format json`` output robustly.

    Docker Compose v2 emits one JSON object per line (JSON-lines format).
    Older versions may emit a single JSON array.  Both forms are handled:

    1. Try to parse each non-empty line as an independent JSON object.
    2. If that yields nothing or raises, fall back to ``json.loads`` of the
       entire stdout (JSON-array form).

    Returns a list of container-info dicts.  An unparseable line is silently
    skipped so a stray blank or debug line does not abort the assertion.
    """
    containers: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                containers.append(obj)
            elif isinstance(obj, list):
                # The whole output was a JSON array on the first non-empty line.
                containers.extend(item for item in obj if isinstance(item, dict))
                return containers
        except json.JSONDecodeError:
            pass  # skip non-JSON lines (e.g., debug output)

    if containers:
        return containers

    # Final fallback: try to parse the entire stdout as a JSON array.
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    return containers


def _assert_no_crash_loops(profile: str, compose_file: Path, env_file: Path) -> None:
    """Assert no container is in a crash-loop or failed state after ``up --wait``.

    ``docker compose up -d --wait`` returns 0 if every container with a
    ``healthcheck:`` becomes healthy.  It is BLIND to containers with no
    healthcheck: they are marked ``running`` the moment the process starts,
    regardless of whether the process immediately crashes and restarts.

    This function runs ``docker compose ps --format json``, collects any
    container whose ``State`` is in ``_CRASH_LOOP_STATES``, and fails with a
    clear message naming each offending container and its state.

    The function is tolerant of empty ``ps`` output (no containers) and of
    docker compose versions that emit either JSON-lines or a JSON array.
    """
    ps_result = subprocess.run(
        _compose_cmd(
            "ps",
            "--format",
            "json",
            compose_file=compose_file,
            env_file=env_file,
            profile=profile,
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    # A non-zero ps returncode is unexpected but not a crash-loop per se; let
    # the assertion below handle the empty container list case gracefully.
    containers = _parse_ps_json(ps_result.stdout)
    bad = [
        {"Name": c.get("Name", "<unknown>"), "State": c.get("State", "<unknown>")}
        for c in containers
        if c.get("State") in _CRASH_LOOP_STATES
    ]
    assert not bad, (
        f"[{profile}] containers in crash-loop state after compose up --wait:\n"
        + "\n".join(f"  {b['Name']}: State={b['State']}" for b in bad)
        + "\n\nThis catches no-healthcheck crash-loops (e.g., dify-worker "
        "RestartCount=49 class) that --wait cannot detect.\n"
        f"Full ps stdout:\n{ps_result.stdout[-3000:]}"
    )


@pytest.fixture(scope="module")
def _skip_if_no_docker() -> None:
    """Skip the entire module when Docker is unavailable."""
    if not _docker_available():
        pytest.skip("Docker daemon not available — skipping compose-up smoke")


@pytest.mark.parametrize("profile", _SMOKE_PROFILES)
def test_compose_up_smoke(profile: str, _skip_if_no_docker: None) -> None:  # noqa: PT019
    """Render → up --wait → no-restart assertion → down -v.

    Each lane renders the NON-GPU compose subset, starts it with
    ``--wait --wait-timeout 180`` (compose fails non-zero if any
    healthchecked container does not reach ``healthy`` within the window),
    then runs a ``docker compose ps --format json`` assertion that catches
    no-healthcheck crash-loops (dify-worker RestartCount=49 class), then
    tears down unconditionally.

    Profiles: core, core,observability, core,rag (original three lanes) +
    rag-milvus, rag-weaviate, security, automation, ragflow (Phase 08 lanes
    confirming milvus/weaviate/authelia/n8n/ragflow boot-validate and the
    redis-auth fix from plan 08-04).

    This test catches the perms / command / config / group_add crash-loop
    classes documented in DEPLOY-BLOCKERS-2026-05-30.md, plus the
    no-healthcheck restart class (T-08-14).
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
                profile=profile,
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
                    profile=profile,
                ),
                capture_output=True,
                text=True,
                timeout=_WAIT_TIMEOUT + 30,
                env={**os.environ, "AGMIND_CI": "1"},
            )

            assert up_result is not None
            assert up_result.returncode == 0, (
                f"[{profile}] compose up --wait failed (returncode={up_result.returncode}).\n"
                f"A container did not reach 'healthy' within {_WAIT_TIMEOUT}s — "
                "check perms/command/config/group_add for crash-loop classes.\n"
                f"stdout: {up_result.stdout[-4000:]}\n"
                f"stderr: {up_result.stderr[-4000:]}"
            )

            # Post-wait crash-loop assertion: --wait is blind to containers with no
            # healthcheck.  A container can enter ``running`` momentarily, pass the
            # --wait poll, then immediately restart (RestartCount=49 class, e.g.
            # dify-worker).  Run ``ps --format json`` and assert no container is in
            # a crash-loop state.  Handles both docker compose v2 output shapes:
            #   * JSON-lines (one JSON object per line) — modern compose
            #   * Single JSON array — some older compose versions
            _assert_no_crash_loops(profile, compose_file, env_file)
        finally:
            # ALWAYS tear down — belt-and-suspenders even if up was interrupted.
            subprocess.run(
                _compose_cmd(
                    "down",
                    "-v",
                    compose_file=compose_file,
                    env_file=env_file,
                    profile=profile,
                ),
                capture_output=True,
                timeout=60,
            )
