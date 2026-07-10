"""Real-compose smoke lane — H.3.

Purpose
-------
Renders the NON-GPU AGmind subset to a temp compose file, runs
``docker compose -p agmind-smoke -f <file> --env-file <ci-env> up -d --wait
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
Exclusions: services that can't boot from a bare ``docker compose up`` —
``llama-*`` (need GPU + a downloaded GGUF model), the install-setup set
``_INSTALL_SETUP_SERVICES`` (prometheus/grafana/loki/alloy/alertmanager/
authelia/n8n — need ``agmind install`` config materialization or a bootstrap
data-dir chown), and ``_SLOW_MODEL_LOADERS`` (docling — loads OCR/layout models
on boot, healthy only after minutes, variable under load). Those are validated
by the full ``agmind install`` (DoD criterion 3) + the A5 ownership gate, not
this bare-compose smoke. The active
lane profiles are passed via ``docker compose --profile`` so the rendered
compose-level ``profiles:`` keys actually select services; a lane left empty
after filtering (e.g. ``automation`` = only n8n) is skipped.

Isolation
---------
Project name ``-p agmind-smoke`` scopes Compose-managed names. The rendered AGmind
compose file also contains fixed ``container_name``, fixed host ports, a fixed
network name, and bind mounts under ``/var/lib/agmind``; those bypass project
scoping and can collide with or write into a live stack on the same self-hosted
runner. The smoke rewrites those deploy-time fields before ``up``:

* remove ``container_name`` so Compose generates ``agmind-smoke-...`` names;
* remove host ``ports`` because inter-container readiness does not need them;
* remove the fixed default network name so ``-p`` scopes the network;
* remap ``/var/lib/agmind`` bind mounts into the test temp directory.

The CI env file carries dummy passwords (identical to those used in
compose-validate).

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
    ELASTIC_PASSWORD=ci-elastic-password
    MILVUS_MINIO_ROOT_PASSWORD=ci-milvus-minio-password
    HOMARR_SECRET_ENCRYPTION_KEY=ci-homarr-secret-encryption-key
    N8N_ENCRYPTION_KEY=ci-n8n-encryption-key
    AUTHELIA_SESSION_SECRET=ci-authelia-session-secret-0000000000000000000000000000
    AUTHELIA_STORAGE_ENCRYPTION_KEY=ci-authelia-storage-encryption-key-00000000000000000
    AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET=ci-authelia-jwt-secret-00000000000000000000000000000000
    AGENT_DB_PASSWORD=ci-agent-db-password
""")

# DB SERVER password files (db-secrets→FILE): the installer writes these so the server reads
# *_PASSWORD_FILE instead of carrying the secret in env. The smoke has no installer, so it
# synthesises the files — the VALUE must match the consumer's _CI_ENV password above (else the
# DB inits with one password and the consumer connects with another → crash-loop).
_CI_SECRET_FILE_VALUES = {
    "postgres_password": "ci-postgres-password",
    "mysql_root_password": "ci-mysql-root-password",
    "agent_db_password": "ci-agent-db-password",
}

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
    "tracing",  # phoenix standalone (SQLite, no model/secret deps) — boot-proven 2026-06-07
    # Agent runtimes boot bare: the agent app is the dify-api class (pure-env FastAPI), agent-db
    # is the postgres class (root self-chown + synthesised secret FILE). 2026-06-08.
    "agents-pydantic",
    "agents-agno",
]

# Container states that indicate a crash-loop or failed start.
# ``--wait`` is blind to services with no healthcheck: a container that is
# restarting or has already exited after a crash is NOT detected by --wait
# alone (dify-worker had RestartCount=49 and would pass a lucky --wait poll).
_CRASH_LOOP_STATES = frozenset({"restarting", "exited", "unhealthy"})

# Project isolation name. The smoke strips deploy-time fixed names/ports/binds
# so this actually isolates from the live stack too.
#
# NOT "agmind-ci": historic smoke runs (compose 5.1.x, before the network-name strip)
# created the live stack's fixed-name networks first, so agmind_data-net/agmind_mgmt-net
# on the runner carry the stale label com.docker.compose.project=agmind-ci. Compose
# >= 5.3 reconciles networks BY PROJECT LABEL and tries to remove/recreate those live
# networks on every "-p agmind-ci" up (fails: active endpoints -> lane red, 2026-07-10).
# A never-before-used project name cannot match any stale label. If you rename this,
# pick a name that has never run on the self-hosted runner.
_PROJECT_NAME = "agmind-smoke"

# Wait timeout in seconds. MUST exceed the largest service healthcheck ``start_period``
# with margin for the first post-start-period probe to pass — otherwise ``docker compose
# up --wait`` quits while the slowest container is still inside its start grace and the
# lane can NEVER go green. ragflow's start_period alone is 180s (retries: 20), so the old
# 180s wait was unwinnable for the ragflow/rag-milvus lanes (chronically red, never once
# green on the self-hosted runner). Overridable via AGMIND_SMOKE_WAIT_TIMEOUT.
_WAIT_TIMEOUT = int(os.environ.get("AGMIND_SMOKE_WAIT_TIMEOUT", "300"))

_AGMIND_DATA_ROOT = Path("/var/lib/agmind")


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


def _restore_temp_permissions(path: Path) -> None:
    """Return root-owned bind-mount leftovers to the runner user before rmtree."""
    from agmind.services.renderer import load_descriptors

    qdrant_ref = load_descriptors()["qdrant"].fq_image()
    uid = os.getuid()
    gid = os.getgid()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{path}:/cleanup",
            "--entrypoint",
            "/bin/sh",
            qdrant_ref,
            "-c",
            f"chmod -R a+rwX /cleanup || true; chown -R {uid}:{gid} /cleanup || true",
        ],
        capture_output=True,
        timeout=60,
    )


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


# Services that CANNOT boot from a bare ``docker compose up`` because they require
# ``agmind install`` prerequisite steps the smoke does NOT perform — config
# materialization (``_materialize_runtime_files`` stages their config file) or a
# bootstrap data-dir chown for their non-root uid. Without those they fatal-crash
# on missing config / unwritable data dir (verified: authelia "configuration
# errors", prometheus/grafana/loki/alloy/alertmanager restart, n8n uid-1001 dir).
# They are validated by the full ``agmind install`` (clean-room / DoD criterion 3)
# + the A5 data-dir-ownership gate + config staging — NOT by this bare-compose
# smoke, which targets the deploy-blocker classes that live in the config-free
# services (redis-auth, milvus storageType, perms, command/healthcheck-vs-image).
_INSTALL_SETUP_SERVICES = frozenset(
    {
        "traefik",  # needs staged dynamic config, CF token, and owns host ports 80/443
        "prometheus",  # needs staged /etc/prometheus/prometheus.yml
        "grafana",  # needs staged provisioning dir + uid-472 data dir
        "loki",  # needs staged loki config
        "alloy",  # needs staged alloy config
        "alertmanager",  # needs staged alertmanager.yml
        "authelia",  # needs staged /config/{configuration,users_database}.yml
        "n8n",  # needs bootstrap-chowned uid-1001 data dir
        "ssrf-proxy",  # needs staged /etc/agmind/ssrf-proxy/squid.conf (single-file :ro mount)
        "dify-sandbox",  # depends_on ssrf-proxy, which needs config materialization
    }
)

# Heavyweight model-loaders whose healthcheck legitimately takes many minutes and varies with
# CPU load (docling loads OCR/layout models on boot). They DO eventually go healthy, but not
# within a bounded bare-compose ``--wait`` — especially co-tenant with the live stack's own copy
# on the self-hosted runner — and slow model-load is not the crash-loop/perms/config deploy-blocker
# class this smoke targets. Boot-validated by the full ``agmind install`` instead (like ``llama-*``).
# (agent-ui is an Agno build: service → already excluded by the build-service filter below.)
_SLOW_MODEL_LOADERS = frozenset({"docling"})


def _rewrite_agmind_bind_mount(spec: str, data_root: Path) -> str:
    """Redirect ``/var/lib/agmind`` bind mounts into the smoke temp directory."""
    parts = spec.split(":")
    if len(parts) < 2:
        return spec

    host = Path(parts[0])
    try:
        rel = host.relative_to(_AGMIND_DATA_ROOT)
    except ValueError:
        return spec

    mapped = data_root / rel
    if rel.parts and rel.parts[0] == "secrets":
        # Secret FILE mount (e.g. secrets/postgres_password) — the installer would have written
        # it; synthesise a FILE (not a dir) with the matching _CI_ENV value so the DB server's
        # *_PASSWORD_FILE reads a real secret and consumers connect with the same value.
        mapped.parent.mkdir(parents=True, exist_ok=True)
        mapped.write_text(_CI_SECRET_FILE_VALUES.get(rel.name, "ci-secret"), encoding="utf-8")
        parts[0] = str(mapped)
        return ":".join(parts)
    mapped.mkdir(parents=True, exist_ok=True)
    # The smoke does not run the installer bootstrap chown step. World-writable
    # temp dirs keep the runtime smoke focused on image command/config/health
    # regressions; ownership is covered separately by the bootstrap gate.
    mapped.chmod(0o777)
    parts[0] = str(mapped)
    return ":".join(parts)


def _filter_unbootable_services(compose_path: Path, data_root: Path) -> int:
    """Drop services that can't boot from a bare ``docker compose up`` and return
    the count of services that REMAIN.

    Removes ``llama-*`` (need GPU + a downloaded GGUF model) and
    ``_INSTALL_SETUP_SERVICES`` (need agmind-install config materialization /
    data-dir chown). Dangling ``depends_on`` references to removed services are
    cleaned up. Fixed deploy-time names/ports/binds/network fields are rewritten
    so ``-p agmind-smoke`` can isolate the smoke stack from live ``agmind-*``
    services on the same self-hosted runner. The remaining set is the subset
    that boots from committed descriptors alone — where the phase's
    deploy-blocker classes actually live. A lane that becomes empty (e.g.
    ``automation`` = only n8n) is skipped by the caller.
    """
    import yaml  # available because agmind[dev] includes PyYAML

    with compose_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        return 0

    services = data.get("services", {})
    removed = [
        k
        for k, svc in services.items()
        if k.startswith("llama-")
        or k in _INSTALL_SETUP_SERVICES
        or k in _SLOW_MODEL_LOADERS
        # build-services (AGmind-authored, compose `build:`) cannot build in the smoke's temp
        # context — the rendered compose sits in a temp dir with no source/Dockerfile, so
        # `docker compose up` would try to build and fail on a fresh runner. Their image build +
        # boot is validated by the live deploy + the build-mechanism unit tests, not this smoke.
        or (isinstance(svc, dict) and "build" in svc)
    ]
    for k in removed:
        del services[k]

    networks = data.get("networks")
    if isinstance(networks, dict):
        # Strip the fixed ``name:`` from EVERY network so ``-p agmind-smoke`` actually scopes
        # them. The renderer stamps the secondary networks (data-net/mgmt-net/ssrf-net) with
        # fixed names (e.g. ``agmind_data-net``); if left intact the smoke's containers join
        # the LIVE stack's network of that name and resolve service DNS (``milvus-minio`` →
        # live container, different MinIO root password) → S3 signature mismatch → the heavy
        # lanes never reach healthy. Stripping ALL names (not just ``default``) makes the
        # smoke truly isolated from a live stack on the same self-hosted runner.
        for net_spec in networks.values():
            if isinstance(net_spec, dict):
                net_spec.pop("name", None)

    # Also remove dangling depends_on references to any removed service.
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        svc.pop("container_name", None)
        svc.pop("ports", None)
        volumes = svc.get("volumes")
        if isinstance(volumes, list):
            svc["volumes"] = [
                _rewrite_agmind_bind_mount(volume, data_root) if isinstance(volume, str) else volume
                for volume in volumes
            ]
        deps = svc.get("depends_on")
        if isinstance(deps, dict):
            for dep_key in removed:
                deps.pop(dep_key, None)
        elif isinstance(deps, list):
            svc["depends_on"] = [d for d in deps if d not in removed]

    with compose_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)
    return len(services)


def test_filter_unbootable_services_rewrites_live_stack_fields(tmp_path: Path) -> None:
    """Regression: ``-p agmind-smoke`` cannot isolate fixed deploy-time fields.

    A live stack may already own ``agmind-qdrant``. The smoke must remove fixed
    container names before ``docker compose up`` so Compose can generate
    project-scoped names such as ``agmind-smoke-qdrant-1``. It must also avoid live
    host ports, the live ``agmind`` network, and live ``/var/lib/agmind`` bind
    mounts.
    """
    import yaml

    compose_path = tmp_path / "compose.yml"
    data_root = tmp_path / "data"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "qdrant": {
                        "image": "qdrant/qdrant:v1.18.0",
                        "container_name": "agmind-qdrant",
                        "ports": ["127.0.0.1:6333:6333"],
                        "volumes": ["/var/lib/agmind/qdrant:/qdrant/storage"],
                    },
                    "llama-llm": {
                        "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049",
                        "container_name": "agmind-llama-llm",
                    },
                },
                "networks": {"default": {"name": "agmind", "driver": "bridge"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    remaining = _filter_unbootable_services(compose_path, data_root)

    assert remaining == 1
    rendered = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert set(rendered["services"]) == {"qdrant"}
    qdrant = rendered["services"]["qdrant"]
    assert "container_name" not in qdrant
    assert "ports" not in qdrant
    assert qdrant["volumes"] == [f"{data_root}/qdrant:/qdrant/storage"]
    assert "name" not in rendered["networks"]["default"]


def test_filter_excludes_docling_slow_model_loader(tmp_path: Path) -> None:
    """docling is a heavyweight CPU model-loader whose ``/health`` legitimately takes many
    minutes and varies with CPU load (it loads OCR/layout models on boot). It eventually goes
    healthy but not within a bounded bare-compose wait — especially co-tenant with the live
    stack's own docling on the self-hosted runner — and a slow model-load is NOT the
    crash-loop/perms/config deploy-blocker class this smoke targets. Like ``llama-*`` it is
    boot-validated by the full ``agmind install``, so the filter drops it from the bare smoke.
    """
    import yaml

    compose_path = tmp_path / "compose.yml"
    data_root = tmp_path / "data"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "qdrant": {"image": "qdrant/qdrant:v1.18.0"},
                    "docling": {"image": "quay.io/docling-project/docling-serve-cpu:v1.18.0"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    remaining = _filter_unbootable_services(compose_path, data_root)

    rendered = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "docling" not in rendered["services"], "docling must be excluded (slow model-loader)"
    assert "qdrant" in rendered["services"]
    assert remaining == 1


def test_filter_strips_every_network_name_for_live_stack_isolation(tmp_path: Path) -> None:
    """Chronic compose-up-smoke red — the real root cause.

    ``-p agmind-smoke`` only project-scopes UNNAMED networks. The renderer stamps the
    secondary networks (``data-net``/``mgmt-net``/``ssrf-net``) with a FIXED ``name:``
    (e.g. ``agmind_data-net``). When the smoke left those names intact, its milvus joined
    the LIVE stack's ``agmind_data-net`` and resolved ``milvus-minio`` to the live container
    (whose MinIO root password differs from the CI env) → S3 ``SignatureDoesNotMatch`` →
    milvus never reaches healthy → the lane is unwinnable while a live stack is up. The
    filter must strip the fixed ``name`` from EVERY network, not just ``default``.
    """
    import yaml

    compose_path = tmp_path / "compose.yml"
    data_root = tmp_path / "data"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "milvus": {
                        "image": "milvusdb/milvus:v2.6.17",
                        "networks": ["default", "data-net"],
                    },
                },
                "networks": {
                    "default": {"name": "agmind", "driver": "bridge"},
                    "data-net": {"name": "agmind_data-net", "driver": "bridge", "internal": True},
                    "mgmt-net": {"name": "agmind_mgmt-net", "driver": "bridge"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    _filter_unbootable_services(compose_path, data_root)

    rendered = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    for net_name, spec in rendered["networks"].items():
        assert "name" not in spec, (
            f"network {net_name!r} kept its fixed name → smoke joins the live stack's "
            f"network and resolves service DNS to live containers (cross-talk)"
        )


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

        # Strip services that can't boot from a bare compose up (GPU/model llama-*,
        # config-materialization + data-dir-chown services). A lane left empty is
        # validated only by the full `agmind install`, so skip it here.
        remaining = _filter_unbootable_services(compose_file, tmp / "data")
        if remaining == 0:
            pytest.skip(
                f"[{profile}] only install-setup services remain after filtering — "
                "validated by the full `agmind install` (DoD criterion 3), not the "
                "bare-compose smoke"
            )

        # Belt: validate compose config before up (surface config errors fast)
        subprocess.run(
            _compose_cmd(
                "down",
                "-v",
                "--remove-orphans",
                compose_file=compose_file,
                env_file=env_file,
                profile=profile,
            ),
            capture_output=True,
            timeout=60,
        )
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
                    "--remove-orphans",
                    compose_file=compose_file,
                    env_file=env_file,
                    profile=profile,
                ),
                capture_output=True,
                timeout=60,
            )
            _restore_temp_permissions(tmp)
