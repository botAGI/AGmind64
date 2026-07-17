"""Phase H'.C: Python renderer для docker-compose.yml из ServiceDescriptor catalog.

Заменяет Ansible Jinja2 шаблон (`ansible/roles/services/templates/docker-compose.yml.j2`)
на типизированный Python код. Используется через `agmind render compose` CLI.

Контракт:
    descriptors = load_descriptors(SERVICES_DIR)
    selected = filter_by_profile(descriptors, ["core", "rag"])
    compose = render_compose(selected.values(), traefik_enabled=True)
    yaml_text = to_yaml(compose)

Что генерирует:
- compose v3.9 структура: services{} + networks{}
- Traefik docker provider labels из ServiceDescriptor.routing (если задано)
- Prometheus docker_sd labels из ServiceDescriptor.observability
- Loki Alloy labels (loki.scrape=true по умолчанию)
- AGmind metadata labels (agmind.service/tier/owner)
- Logging defaults: json-file 50m × 3 файла (no 100GB log files)
- Healthcheck в нативном compose формате
- Network `agmind` shared bridge

См. ADR-0006 (когда напишем).
"""

from __future__ import annotations

import grp
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agmind.core.domain import validate_domain
from agmind.core.paths import data_root
from agmind.schemas import ServiceDescriptor

# AMD-GPU groups that minimal container images (e.g. llama.cpp vulkan) do NOT carry in
# their /etc/group. Docker resolves group_add NAMES against the container's group db, so
# a name like `render` fails with "no matching entries in group file". Emit the host's
# numeric GID instead (resolvable in any container). Defaults match the common
# Strix-Halo/Debian layout when the rendering host lacks the group.
_GPU_GROUP_DEFAULT_GID: dict[str, str] = {"render": "992", "video": "44"}


def _resolve_group_add(group_add: list[str]) -> list[str]:
    """Resolve every group to a numeric host GID (resolvable in any container).

    Docker resolves group_add NAMES against the CONTAINER's /etc/group, which
    minimal images lack -> the container crashes ("unable to find group <name>").
    So we always emit numeric GIDs: already-numeric entries pass through; names
    are resolved via the render host's group db. GPU groups keep a known-GID
    fallback when the render host itself lacks the group. Any other unresolvable
    name fails the render LOUDLY instead of shipping a crash-looping container
    (Правила Карпатого #9 — group_add must render numeric, never a bare name).
    """
    resolved: list[str] = []
    for name in group_add:
        if str(name).isdigit():
            resolved.append(str(name))
            continue
        try:
            resolved.append(str(grp.getgrnam(name).gr_gid))
        except KeyError:
            if name in _GPU_GROUP_DEFAULT_GID:
                resolved.append(_GPU_GROUP_DEFAULT_GID[name])
            else:
                raise ValueError(
                    f"group_add '{name}' has no numeric GID on the render host and "
                    f"no known fallback; it would render as an unresolvable NAME and "
                    f"crash the container. Add a fallback GID or use a numeric GID "
                    f"in the descriptor."
                )
    # Dedup order-preservingly: a name resolving to the same GID as a numeric entry would
    # otherwise emit a duplicate group_add (review POLISH resolve-group-add-duplicates).
    return list(dict.fromkeys(resolved))


REPO_ROOT = data_root()
DEFAULT_SERVICES_DIR = REPO_ROOT / "templates" / "services"

# Compose-spec без `version:` field — современный формат (2026).
# Docker Compose v2.0+ deprecated top-level `version:` (https://docs.docker.com/compose/compose-file/04-version-and-name/).
# Старый v3.9 был оставлен изначально, но docker compose выдаёт WARN.
# Set None чтобы НЕ писать `version:` в output (compose сам подхватит latest spec).
COMPOSE_VERSION: str | None = None

# Logging defaults для предотвращения накопления GB логов
# (deep-dive 04 §10, deep-dive 03 §9)
DEFAULT_LOGGING = {
    "driver": "json-file",
    "options": {"max-size": "50m", "max-file": "3"},
}

# AGmind shared bridge network — все сервисы видят друг друга и Traefik.
DEFAULT_NETWORK_NAME = "agmind"

# Namespacing defaults. With these exact values a render is BYTE-IDENTICAL to the historical
# single-stack output; pass non-default values (e.g. for a scenario stack or CI smoke) to
# isolate a second compose project from a live `agmind` stack on the same host.
DEFAULT_PROJECT_NAME = "agmind"
DEFAULT_DATA_ROOT = "/var/lib/agmind"
DEFAULT_CONFIG_ROOT = "/etc/agmind"

# A compose project namespace, NOT a path: lowercase alnum + - / _. Blocks the traversal a
# free-text `agmind render scenario --project '../../tmp/evil'` would otherwise inject into
# data_root=/var/lib/<project> and the compose identifiers (review MEDIUM
# render-project-unvalidated-traversal). Accepts `agmind` + every `agmind-<scenario>` name.
_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")


def _validate_render_namespace(project_name: str, data_root: str, config_root: str) -> None:
    if not _PROJECT_NAME_RE.match(project_name):
        raise ValueError(
            f"invalid compose project name {project_name!r}: must match "
            f"{_PROJECT_NAME_RE.pattern} (a namespace, not a path — no '/', '..', or spaces)"
        )
    for label, root in (("data_root", data_root), ("config_root", config_root)):
        if ".." in Path(root).parts:
            raise ValueError(f"{label} {root!r} must not contain '..' (path traversal)")


def _rewrite_volume_host_root(volume: str, data_root: str, config_root: str) -> str:
    """Rewrite the host side of a bind mount to a namespaced root.

    ``/var/lib/agmind/<svc>:...`` → ``{data_root}/<svc>:...`` and ``/etc/agmind/...`` →
    ``{config_root}/...``. Named volumes and unrelated host paths (``/var/run/docker.sock``,
    ``/var/log``, ``/``) are left alone. With the default roots this is the identity, so a
    default render stays byte-identical.
    """
    host, sep, rest = volume.partition(":")
    if not sep:
        return volume
    for default_root, new_root in (
        (DEFAULT_DATA_ROOT, data_root),
        (DEFAULT_CONFIG_ROOT, config_root),
    ):
        if host == default_root or host.startswith(default_root + "/"):
            return f"{new_root}{host[len(default_root) :]}{sep}{rest}"
    return volume


# Attributes for extra (non-default) networks a descriptor may join. `ssrf-net`
# is `internal: true` so a service caged on it (dify-sandbox) has NO host/egress
# route except through the dual-homed ssrf-proxy.
_EXTRA_NETWORK_ATTRS: dict[str, dict[str, Any]] = {
    "ssrf-net": {"driver": "bridge", "internal": True},
    # data-net: internal (no host route) tier for stateful backends. A datastore on data-net
    # ONLY is unreachable from the shared net — only its dual-homed consumers reach it, so a
    # compromised internet-facing app can't pivot to it. live-audit 2026-06-05 (flat-network /
    # etcd-no-auth exposure). Rolled out incrementally (etcd + milvus-minio first).
    "data-net": {"driver": "bridge", "internal": True},
    # mgmt-net: internal (no host route) tier for the Docker-management plane. The
    # docker-socket-proxy (+ raw-socket holders) live here ONLY, so a compromised internet-facing
    # app on the shared net can no longer reach docker-socket-proxy:2375 (which leaks every
    # container's env via CONTAINERS=1) or the updater. Only the legit docker_api consumers are
    # dual-homed onto it. live-audit 2026-06-07 (SEC-1 app→mgmt-plane pivot).
    "mgmt-net": {"driver": "bridge", "internal": True},
}


def load_descriptors(services_dir: Path = DEFAULT_SERVICES_DIR) -> dict[str, ServiceDescriptor]:
    """Load all `templates/services/*.yaml` → {name: ServiceDescriptor}.

    Сортирует по имени для детерминированного output (важно для diff/tests).
    """
    out: dict[str, ServiceDescriptor] = {}
    if not services_dir.exists():
        return out
    for path in sorted(services_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        descriptor = ServiceDescriptor.model_validate(data)
        if descriptor.name in out:
            raise ValueError(f"Duplicate service name '{descriptor.name}' in {path}")
        out[descriptor.name] = descriptor
    return out


def filter_by_profile(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str],
) -> dict[str, ServiceDescriptor]:
    """Keep only services with at least one profile from `profiles`.

    Если profiles=["full"] — возвращает все сервисы (special-case).
    """
    if "full" in profiles:
        return dict(descriptors)
    wanted = set(profiles)
    return {name: d for name, d in descriptors.items() if set(d.profiles) & wanted}


def available_profiles(descriptors: dict[str, ServiceDescriptor]) -> set[str]:
    """Return profile names understood by the descriptor catalog."""
    profiles = {profile for descriptor in descriptors.values() for profile in descriptor.profiles}
    profiles.add("full")
    return profiles


def unknown_profiles(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str] | None,
) -> list[str]:
    """Return requested profile names that are absent from the descriptor catalog."""
    if not profiles:
        return []
    return sorted(set(profiles).difference(available_profiles(descriptors)))


def filter_by_services(
    descriptors: dict[str, ServiceDescriptor],
    services: list[str],
) -> dict[str, ServiceDescriptor]:
    """Keep only explicit named services (Phase J.1.8: per-service selection)."""
    wanted = set(services)
    missing = sorted(wanted.difference(descriptors))
    if missing:
        raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    return {name: d for name, d in descriptors.items() if name in wanted}


def select_services(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str] | None = None,
    services: list[str] | None = None,
) -> dict[str, ServiceDescriptor]:
    """Combined selection: explicit `services` list takes precedence over `profiles`.

    Usage:
        select_services(d, services=["traefik", "llama-llm", "qdrant"])
        select_services(d, profiles=["core", "observability"])
    """
    if services is not None:
        return filter_by_services(descriptors, services)
    return filter_by_profile(descriptors, profiles or [])


def check_missing_dependencies(
    selected: dict[str, ServiceDescriptor],
    all_descriptors: dict[str, ServiceDescriptor],
) -> dict[str, list[str]]:
    """Return {service_name: [missing_dep_names]} — depends_on которые не выбраны.

    Используется TUI чтобы warn user'а если он выбрал dify-api без postgres.
    """
    selected_names = set(selected.keys())
    missing: dict[str, list[str]] = {}
    for name, d in selected.items():
        gaps = [dep for dep in d.depends_on if dep not in selected_names]
        if gaps:
            missing[name] = gaps
    return missing


def render_traefik_labels(
    d: ServiceDescriptor, *, network_name: str = DEFAULT_NETWORK_NAME
) -> dict[str, str]:
    """Generate Traefik docker provider labels from ServiceDescriptor.routing.

    Если routing=None — пустой dict (internal-only service).
    Если routing задан — полный набор labels для:
    - Host(...) router rule
    - websecure entrypoint + TLS via `le` cert resolver
    - middleware chain (chain-llm | chain-internal | chain-public from file provider)
    - healthcheck integration
    - SSE-safe settings если routing.sse=True (см. deep-dive 01 §1)
    - traefik.docker.network для multi-homed сервисов (AUTH-1, см. ниже)
    """
    if d.routing is None:
        return {}

    name = d.name
    routing = d.routing
    # Multi-component-on-one-host (Dify): path_prefixes scope this router to specific paths
    # (Host && (PathPrefix(a) || PathPrefix(b) …)); priority ranks it above the host-only
    # catch-all sibling. Empty path_prefixes → plain Host(...) rule (unchanged for every
    # other service).
    if routing.path_prefixes:
        prefix_expr = " || ".join(f"PathPrefix(`{p}`)" for p in routing.path_prefixes)
        rule = f"Host(`{routing.host}`) && ({prefix_expr})"
    else:
        rule = f"Host(`{routing.host}`)"
    labels: dict[str, str] = {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": rule,
        f"traefik.http.routers.{name}.entrypoints": "websecure",
        f"traefik.http.routers.{name}.tls": "true",
        f"traefik.http.routers.{name}.tls.certresolver": "le",
        f"traefik.http.routers.{name}.middlewares": f"{routing.middleware_chain}@file",
    }
    if routing.priority:
        labels[f"traefik.http.routers.{name}.priority"] = str(routing.priority)

    # Port — routing.port override (edge/UI port ≠ published port), else container-side of the
    # first port mapping. live-audit 2026-06-05: RAGFlow must route to nginx :80, not API 9380.
    container_port = str(routing.port) if routing.port is not None else _first_container_port(d)
    if container_port:
        labels[f"traefik.http.services.{name}.loadbalancer.server.port"] = container_port

    # Healthcheck integration: Traefik исключит unhealthy из роутинга
    labels[f"traefik.http.services.{name}.loadbalancer.healthcheck.path"] = routing.healthcheck_path
    labels[f"traefik.http.services.{name}.loadbalancer.healthcheck.interval"] = "30s"
    labels[f"traefik.http.services.{name}.loadbalancer.passhostheader"] = "true"

    # SSE-safe: обязательно для llama-server streaming (deep-dive 01 §1)
    if routing.sse:
        labels[f"traefik.http.services.{name}.loadbalancer.responseforwarding.flushinterval"] = (
            "1ms"
        )
        labels[f"traefik.http.routers.{name}.tls.options"] = "no-http2@file"

    # Multi-homed routed service: pin the network Traefik dials the backend on. A service joined to
    # >1 network (e.g. default + data-net) otherwise lets Traefik's docker provider pick an arbitrary
    # one — if it picks an internal data/mgmt net Traefik can't reach, the loadbalancer has no usable
    # server and the router returns 503. For Authelia this 503'd the forward-auth endpoint → EVERY
    # gated app 302'd to a dead portal (AUTH-1, whole-stack outage). Pin the shared edge (default)
    # network, which Traefik is always on. live-audit 2026-06-08.
    if d.networks and len(d.networks) > 1:
        labels["traefik.docker.network"] = network_name

    return labels


def render_observability_labels(d: ServiceDescriptor) -> dict[str, str]:
    """Generate Prometheus docker_sd + Alloy loki.scrape + AGmind metadata labels.

    Эти labels подхватываются:
    - prometheus.* → Prometheus docker_sd_configs (relabel_configs whitelist)
    - loki.* → Grafana Alloy discovery.docker (loki.source.docker target filter)
    - agmind.* → free-form metadata, доступны как labels в Prometheus/Loki queries
    """
    labels: dict[str, str] = {
        "agmind.service": d.name,
        "agmind.tier": d.tier,
        "agmind.owner": d.owner,
    }

    obs = d.observability
    if obs.prometheus_scrape:
        labels["prometheus.scrape"] = "true"
        labels["prometheus.path"] = obs.metrics_path
        # Port — explicit (metrics_port) or fallback to first container port
        port = obs.metrics_port
        if port is None:
            container_port = _first_container_port(d)
            if container_port:
                try:
                    port = int(container_port)
                except ValueError:
                    port = None
        if port is not None:
            labels["prometheus.port"] = str(port)

    if obs.loki_scrape:
        labels["loki.scrape"] = "true"

    return labels


def _first_container_port(d: ServiceDescriptor) -> str | None:
    """Extract container-side port из первого port mapping (для loadbalancer.server.port).

    Format: `[ip:]host:container` → возвращаем container часть.
    """
    if not d.ports:
        return None
    spec = d.ports[0]
    parts = spec.split(":")
    if not parts:
        return None
    # `_PORT_RE` rejects any /proto suffix at descriptor validation, so the split below is a
    # defensive no-op for catalog-loaded ports; kept for directly-constructed descriptors.
    return parts[-1].split("/", 1)[0]


def descriptor_to_compose_service(
    d: ServiceDescriptor,
    traefik_enabled: bool = True,
    selected_descriptors: Mapping[str, ServiceDescriptor] | None = None,
    *,
    project_name: str = DEFAULT_PROJECT_NAME,
    data_root: str = DEFAULT_DATA_ROOT,
    config_root: str = DEFAULT_CONFIG_ROOT,
    network_name: str = DEFAULT_NETWORK_NAME,
) -> dict[str, Any]:
    """Build single compose service definition (compose v3.9 format).

    ``project_name``/``data_root``/``config_root`` namespace the container name and bind-mount
    host roots; their defaults reproduce the historical ``agmind-*`` / ``/var/lib/agmind``
    output byte-for-byte.
    """
    svc: dict[str, Any] = {
        "image": d.fq_image(),
        "container_name": f"{project_name}-{d.name}",
        "restart": "unless-stopped",
    }

    # AGmind-authored image built on-host from shipped source (compose-native build:), instead
    # of pulled from a registry. `image` is the resulting local tag; `docker compose up --build`
    # builds it. Used by the agent cores so AGmind ships its own apps without a registry/publish.
    # The context is resolved to an ABSOLUTE repo path: at real deploy time compose runs with
    # cwd=install_dir (/opt/agmind), which does NOT contain docker/ + services/ — so a relative
    # "." context would point at install_dir and the build would fail. `context: "."` in the
    # descriptor means "the repo root", resolved here against REPO_ROOT (the live source tree).
    if d.build is not None:
        svc["build"] = {
            "context": str((REPO_ROOT / d.build.context).resolve()),
            "dockerfile": d.build.dockerfile,
        }

    if d.profiles:
        svc["profiles"] = list(d.profiles)
    if d.depends_on:
        svc["depends_on"] = render_depends_on(d, selected_descriptors or {})
    if d.resources.cpus is not None:
        svc["cpus"] = str(d.resources.cpus)
    if d.resources.mem_limit:
        svc["mem_limit"] = d.resources.mem_limit
    if d.resources.mem_reservation:
        # Soft scheduling floor (OPT-1): the kernel tries to keep this much available under
        # memory pressure. Distinct from mem_limit (the hard OOM cap). Defaults absent so a
        # descriptor that omits it stays byte-identical to the historical render.
        svc["mem_reservation"] = d.resources.mem_reservation
    if d.ports:
        svc["ports"] = list(d.ports)
    if d.env:
        svc["environment"] = dict(d.env)
    if d.volumes:
        svc["volumes"] = [_rewrite_volume_host_root(v, data_root, config_root) for v in d.volumes]

    if d.health is not None:
        hc: dict[str, Any] = {
            "test": list(d.health.test),
            "interval": d.health.interval,
            "timeout": d.health.timeout,
            "retries": d.health.retries,
        }
        if d.health.start_period:
            hc["start_period"] = d.health.start_period
        svc["healthcheck"] = hc

    # Compose-native runtime fields (compose-spec)
    if d.command:
        svc["command"] = list(d.command)
    if d.devices:
        svc["devices"] = list(d.devices)
    if d.group_add:
        svc["group_add"] = _resolve_group_add(list(d.group_add))
    sec_opts = list(d.security_opt)
    if d.no_new_privileges and "no-new-privileges:true" not in sec_opts:
        sec_opts.append("no-new-privileges:true")
    if sec_opts:
        svc["security_opt"] = sec_opts
    if d.cap_add:
        svc["cap_add"] = list(d.cap_add)
    if d.cap_drop:
        svc["cap_drop"] = list(d.cap_drop)
    if d.read_only:
        svc["read_only"] = True
    if d.pids_limit is not None:
        svc["pids_limit"] = d.pids_limit
    if d.cgroupns_mode is not None:
        # Compose top-level `cgroup:` selects the cgroup NAMESPACE mode ('host'/'private').
        # 'host' lets a container (cAdvisor) walk every cgroup under cgroup v2 instead of only
        # its own — without it cAdvisor emits no per-container series (CRITICAL-1).
        svc["cgroup"] = d.cgroupns_mode
    if d.privileged:
        svc["privileged"] = True
    if d.networks:
        # Non-empty → join ONLY these networks (compose long-form mapping). Empty
        # stays absent so every other service is byte-identical on `default`.
        svc["networks"] = {name: None for name in d.networks}

    # Logging defaults для предотвращения log bloat
    svc["logging"] = DEFAULT_LOGGING

    # Combined labels: observability + traefik + agmind metadata
    labels = render_observability_labels(d)
    if traefik_enabled:
        labels.update(render_traefik_labels(d, network_name=network_name))
    if labels:
        svc["labels"] = labels

    return svc


def render_depends_on(
    d: ServiceDescriptor,
    selected_descriptors: Mapping[str, ServiceDescriptor],
) -> dict[str, dict[str, bool | str]]:
    """Render Compose long-syntax dependency gates for a descriptor."""
    depends_on: dict[str, dict[str, bool | str]] = {}
    for dependency in d.depends_on:
        dependency_descriptor = selected_descriptors.get(dependency)
        condition = (
            "service_healthy"
            if dependency_descriptor and dependency_descriptor.health
            else "service_started"
        )
        depends_on[dependency] = {
            "condition": condition,
            "restart": True,
        }
    return depends_on


def inject_capability_env(
    selected: dict[str, ServiceDescriptor],
) -> dict[str, dict[str, str]]:
    """Phase O.B: compute env vars to add to each consumer based on capability bindings.

    Walks consumers (services with .consumes non-empty), resolves who provides
    each capability among selected services, and looks up BINDINGS table.

    Returns: {consumer_name: {ENV_VAR: value}} — to merge into descriptor env.
    Consumers without resolvable provider или no binding entry → empty dict.
    """
    from agmind.services.capability_bindings import env_for_consumer
    from agmind.services.compatibility import resolve_capability_provider_for_consumer

    extra_env: dict[str, dict[str, str]] = {}
    for name, d in selected.items():
        if not d.consumes:
            continue
        consumer_env: dict[str, str] = {}
        for cap in d.consumes:
            provider = resolve_capability_provider_for_consumer(selected, cap, name)
            if provider is None:
                continue
            consumer_env.update(env_for_consumer(cap, provider, name))
        if consumer_env:
            extra_env[name] = consumer_env
    return extra_env


def descriptors_with_capability_env(
    descriptors: list[ServiceDescriptor],
) -> list[ServiceDescriptor]:
    """Return descriptor copies with capability env merged into consumers.

    Descriptor-authored env wins over injected defaults, matching Docker Compose
    renderer behavior and keeping manual overrides possible.
    """
    selected_map = {d.name: d for d in descriptors}
    capability_env = inject_capability_env(selected_map)
    resolved: list[ServiceDescriptor] = []
    for descriptor in descriptors:
        extra = capability_env.get(descriptor.name)
        if not extra:
            resolved.append(descriptor)
            continue
        env = {**extra, **descriptor.env}
        resolved.append(descriptor.model_copy(update={"env": env}))
    return resolved


def _check_unresolved_consumes(
    selected: dict[str, ServiceDescriptor],
) -> None:
    """Fail-closed on unresolved non-optional consumes (C2 — Wave C renderer hardening).

    For each selected service, for each capability it consumes:
    - Skip if cap is in OPTIONAL_MISSING_CAPABILITIES (e.g. dify_external_kb, reranker).
    - Skip if (service_name, cap) is in KNOWN_CROSS_PROFILE_CONSUMES (e.g. dify-api
      consuming llm_inference from llama-llm in a different profile — always co-deployed).
    - Otherwise: resolve the provider; if None → raise ValueError naming the consumer + cap.

    This mirrors the existing missing-depends_on raise in render_to_string() and converts
    a silent half-configured container into a loud render-time failure.
    """
    from agmind.services.compatibility import (
        OPTIONAL_MISSING_CAPABILITIES,
        resolve_capability_provider_for_consumer,
    )
    from agmind.services.topology_checks import (
        CLOSURE_PULLED_CAPABILITIES,
        KNOWN_CROSS_PROFILE_CONSUMES,
    )

    unresolved: list[tuple[str, str]] = []
    for name, d in selected.items():
        for cap in d.consumes:
            if cap in OPTIONAL_MISSING_CAPABILITIES:
                continue
            if cap in CLOSURE_PULLED_CAPABILITIES:
                continue  # sole provider is closure-pulled (e.g. docker_api → docker-socket-proxy)
            if (name, cap) in KNOWN_CROSS_PROFILE_CONSUMES:
                continue
            provider = resolve_capability_provider_for_consumer(selected, cap, name)
            if provider is None:
                unresolved.append((name, cap))

    if unresolved:
        details = "; ".join(
            f"'{name}' consumes '{cap}' but no provider is selected"
            for name, cap in sorted(unresolved)
        )
        raise ValueError(
            f"Unresolved capability consumes in render — add the provider to your profile "
            f"selection or mark it optional: {details}"
        )


def render_compose(
    descriptors: list[ServiceDescriptor],
    traefik_enabled: bool = True,
    network_name: str | None = None,
    *,
    project_name: str = DEFAULT_PROJECT_NAME,
    data_root: str = DEFAULT_DATA_ROOT,
    config_root: str = DEFAULT_CONFIG_ROOT,
) -> dict[str, Any]:
    """Build full docker-compose.yml structure as Python dict.

    Args:
        descriptors: services to include (отсортированы по имени для детерминизма)
        traefik_enabled: добавлять Traefik labels из routing config
        network_name: имя shared bridge сети (default: == project_name)
        project_name: compose project namespace (container names, network, top-level `name`)
        data_root/config_root: host roots for `/var/lib/agmind` / `/etc/agmind` bind mounts

    With ``project_name``/``data_root``/``config_root`` at their defaults the output is
    byte-identical to the historical single-stack render. Non-defaults isolate a second
    compose project (a scenario stack, CI smoke) from a live ``agmind`` stack.
    """
    _validate_render_namespace(project_name, data_root, config_root)
    if network_name is None:
        network_name = project_name
    selected_by_name_pre = {d.name: d for d in descriptors}
    # C2: fail-closed on unresolved non-optional consumes (Wave C renderer hardening).
    # Must run BEFORE descriptors_with_capability_env so the check uses the raw descriptor
    # set (not the env-merged copy) — the provider resolution logic is the same either way.
    _check_unresolved_consumes(selected_by_name_pre)
    # Fail-closed on a depends_on target that is not in THIS selection — render_compose is
    # public, and a direct render_compose([partial]) call would otherwise emit a dangling
    # `depends_on` that Compose hard-errors at up (review LOW render-compose-no-depends-guard).
    # No-op on the render_to_string path (its complete-selection check short-circuits first).
    missing_deps = check_missing_dependencies(selected_by_name_pre, selected_by_name_pre)
    if missing_deps:
        details = "; ".join(
            f"{name} requires {', '.join(deps)}" for name, deps in sorted(missing_deps.items())
        )
        raise ValueError(f"Missing dependencies in render selection: {details}")

    resolved_descriptors = descriptors_with_capability_env(descriptors)

    services_block_local: dict[str, Any] = {}
    selected_by_name = {descriptor.name: descriptor for descriptor in resolved_descriptors}
    for d in sorted(resolved_descriptors, key=lambda x: x.name):
        svc = descriptor_to_compose_service(
            d,
            traefik_enabled,
            selected_by_name,
            project_name=project_name,
            data_root=data_root,
            config_root=config_root,
            network_name=network_name,
        )
        services_block_local[d.name] = svc
    services_block = services_block_local
    networks_block: dict[str, Any] = {
        "default": {
            "name": network_name,
            "driver": "bridge",
        }
    }
    # Add any extra networks referenced by selected services. `internal: true` is
    # the SSRF cage primitive — no host route, only intra-net + a dual-homed proxy.
    extra_networks = sorted({n for d in resolved_descriptors for n in d.networks if n != "default"})
    for net in extra_networks:
        # Fail closed: an unregistered extra network must NOT silently default to a plain
        # egress bridge — a service intending an internal-only SSRF cage would then get a full
        # host/egress route with no warning. Every non-default network must declare its driver +
        # internal flag explicitly in _EXTRA_NETWORK_ATTRS.
        if net not in _EXTRA_NETWORK_ATTRS:
            raise ValueError(
                f"network '{net}' is not registered in renderer._EXTRA_NETWORK_ATTRS — refusing "
                f"to render it (it would become an egress-capable bridge). Add it with an explicit "
                f"{{'driver': ..., 'internal': ...}} so the isolation intent is deliberate."
            )
        attrs = _EXTRA_NETWORK_ATTRS[net]
        networks_block[net] = {"name": f"{network_name}_{net}", **attrs}
    compose: dict[str, Any] = {
        "services": services_block,
        "networks": networks_block,
    }
    # Emit a top-level `name:` ONLY for a namespaced render — a default project keeps the
    # historical output (project name comes from the install dir / `-p`), so golden renders
    # and gates stay byte-identical. A scenario stack gets a self-contained project name.
    if project_name != DEFAULT_PROJECT_NAME:
        compose = {"name": project_name, **compose}
    if COMPOSE_VERSION is not None:
        # Legacy compatibility — современный compose не требует version
        compose = {"version": COMPOSE_VERSION, **compose}
    return compose


def to_yaml(compose: dict[str, Any]) -> str:
    """Serialize compose dict to YAML with stable ordering.

    Возвращает строку с auto-generated header.
    """
    header = (
        "# Auto-generated by `agmind render compose` from templates/services/*.yaml.\n"
        "# DO NOT EDIT BY HAND — изменения переписать в template files и регенерировать.\n"
        "# См. agmind/services/renderer.py + ADR-0006.\n"
        "\n"
    )
    body = yaml.safe_dump(
        compose,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
        indent=2,
    )
    return header + body


def _replace_domain_placeholder(value: Any, domain: str) -> Any:
    if isinstance(value, str):
        return value.replace("agmind.dev", domain)
    if isinstance(value, list):
        return [_replace_domain_placeholder(item, domain) for item in value]
    if isinstance(value, dict):
        return {key: _replace_domain_placeholder(item, domain) for key, item in value.items()}
    return value


def render_to_string(
    profiles: list[str] | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    traefik_enabled: bool | None = None,
    domain: str | None = None,
    services: list[str] | None = None,
    *,
    project_name: str = DEFAULT_PROJECT_NAME,
    data_root: str = DEFAULT_DATA_ROOT,
    config_root: str = DEFAULT_CONFIG_ROOT,
) -> str:
    """End-to-end: load + filter + render + serialize. Возвращает финальный YAML.

    Args:
        profiles: list profile names to include (high-level filter)
        services: explicit service names (per-service selection, takes precedence)
        services_dir: где искать service descriptors
        traefik_enabled: добавлять Traefik routing labels из routing config.
            None (default) = selection-derived: True iff "traefik" is in the selected
            set. Ratified 2026-07-17 (P0.3 / 15-04): the default install is LOCAL —
            install/deploy/wizard paths must not force public routing labels onto a
            selection that deploys no edge; public access = traefik explicitly selected
            (and then the authelia topology gate applies). An explicit bool always wins
            (render_cmd's --traefik/--no-traefik flag).
        domain: если задан — заменить `agmind.dev` placeholder на этот домен
        project_name/data_root/config_root: compose-project namespacing (defaults reproduce
            the historical single-stack output byte-for-byte)
    """
    descriptors = load_descriptors(services_dir)
    if services is not None:
        missing = sorted(set(services).difference(descriptors))
        if missing:
            raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    if services is None:
        missing_profiles = unknown_profiles(descriptors, profiles)
        if missing_profiles:
            raise ValueError(f"Unknown profiles requested: {', '.join(missing_profiles)}")
    selected = select_services(descriptors, profiles=profiles, services=services)
    if not selected:
        raise ValueError(f"No services match: profiles={profiles}, services={services}")
    if traefik_enabled is None:
        traefik_enabled = "traefik" in selected
    missing_dependencies = check_missing_dependencies(selected, descriptors)
    if missing_dependencies:
        details = "; ".join(
            f"{name} requires {', '.join(deps)}"
            for name, deps in sorted(missing_dependencies.items())
        )
        raise ValueError(f"Missing dependencies for selected services: {details}")
    compose = render_compose(
        list(selected.values()),
        traefik_enabled=traefik_enabled,
        project_name=project_name,
        data_root=data_root,
        config_root=config_root,
    )
    if domain:
        safe_domain = validate_domain(domain)
        if safe_domain != "agmind.dev":
            compose = _replace_domain_placeholder(compose, safe_domain)
    return to_yaml(compose)
