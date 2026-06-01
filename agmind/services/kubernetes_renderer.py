"""Kubernetes/k3s renderer MVP from ServiceDescriptor objects.

This renderer is intentionally separate from the Docker Compose renderer. It is
the first research-grade bridge for k3s manifests: useful for inspection,
portable-service prototyping, and identifying Docker-only fields before AGmind
promotes Kubernetes lanes beyond research status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    check_missing_dependencies,
    descriptors_with_capability_env,
    load_descriptors,
    select_services,
    unknown_profiles,
)

DEFAULT_NAMESPACE = "agmind"
AMD_GPU_DOCKER_DEVICE = "/dev/dri"
AMD_GPU_RESOURCE_NAME = "amd.com/gpu"
AMD_GPU_RESOURCE_QUANTITY = "1"
AMD_GPU_DOCKER_GROUPS = frozenset({"video", "render"})
_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")
_SECRET_TOKEN_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
)
COMPOSE_ONLY_DOCKER_SOCKET_SERVICES = frozenset(
    {
        "cadvisor",  # Docker-native metrics; Kubernetes uses metrics-server/node-exporter
        "dozzle",
        "homarr",
        "netdata",
        "portainer",
        "watchtower",
    }
)

WarningSeverity = Literal["info", "warning", "blocker"]


@dataclass(frozen=True)
class KubernetesRenderWarning:
    """A portability warning emitted while rendering Kubernetes manifests."""

    service: str
    code: str
    severity: WarningSeverity
    message: str
    remediation: str

    def comment_text(self) -> str:
        """Human-readable warning line for YAML comments."""
        return f"{self.service}: [{self.code}/{self.severity}] {self.message}"


@dataclass(frozen=True)
class KubernetesRenderResult:
    """Rendered Kubernetes objects plus non-portability warnings."""

    objects: list[dict[str, Any]]
    warnings: tuple[KubernetesRenderWarning, ...] = ()


def render_kubernetes(
    descriptors: list[ServiceDescriptor],
    *,
    namespace: str = DEFAULT_NAMESPACE,
    include_namespace: bool = True,
    strict: bool = False,
) -> KubernetesRenderResult:
    """Render ServiceDescriptor objects into plain Kubernetes manifests."""
    descriptors = descriptors_with_capability_env(descriptors)
    renderable = tuple(
        descriptor for descriptor in descriptors if not _is_kubernetes_omitted_service(descriptor)
    )
    warnings = tuple(
        warning
        for descriptor in descriptors
        for warning in collect_portability_warnings(descriptor)
    )
    if strict and warnings:
        details = "; ".join(f"{warning.service}: {warning.message}" for warning in warnings)
        raise ValueError(f"non-portable Kubernetes render warnings: {details}")

    objects: list[dict[str, Any]] = []
    if include_namespace:
        objects.append(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": namespace},
            }
        )

    warnings_by_service: dict[str, list[str]] = {}
    for warning in warnings:
        warnings_by_service.setdefault(warning.service, []).append(warning.message)

    for descriptor in sorted(renderable, key=lambda item: item.name):
        service_warnings = tuple(warnings_by_service.get(descriptor.name, ()))
        objects.append(_deployment_for_descriptor(descriptor, namespace, service_warnings))
        service = _service_for_descriptor(descriptor, namespace)
        if service is not None:
            objects.append(service)

    return KubernetesRenderResult(objects=objects, warnings=warnings)


def render_to_string(
    *,
    profiles: list[str] | None = None,
    services: list[str] | None = None,
    exclude_services: list[str] | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    namespace: str = DEFAULT_NAMESPACE,
    include_namespace: bool = True,
    strict: bool = False,
) -> str:
    """Load descriptors, select by profile/service, and render Kubernetes YAML."""
    descriptors = load_descriptors(services_dir)
    if services is not None:
        missing = sorted(set(services).difference(descriptors))
        if missing:
            raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    if exclude_services is not None:
        missing = sorted(set(exclude_services).difference(descriptors))
        if missing:
            raise ValueError(f"Unknown excluded services: {', '.join(missing)}")
    if services is None:
        missing_profiles = unknown_profiles(descriptors, profiles)
        if missing_profiles:
            raise ValueError(f"Unknown profiles requested: {', '.join(missing_profiles)}")
    selected = select_services(descriptors, profiles=profiles, services=services)
    if exclude_services:
        excluded = frozenset(exclude_services)
        selected = {
            name: descriptor for name, descriptor in selected.items() if name not in excluded
        }
    if not selected:
        raise ValueError(f"No services match: profiles={profiles}, services={services}")
    missing_dependencies = check_missing_dependencies(selected, descriptors)
    if missing_dependencies:
        details = "; ".join(
            f"{name} requires {', '.join(deps)}"
            for name, deps in sorted(missing_dependencies.items())
        )
        raise ValueError(f"Missing dependencies for selected services: {details}")
    result = render_kubernetes(
        list(selected.values()),
        namespace=namespace,
        include_namespace=include_namespace,
        strict=strict,
    )
    if services is not None and not any(obj.get("kind") != "Namespace" for obj in result.objects):
        raise ValueError(
            "No Kubernetes-renderable services selected after applying Kubernetes omissions: "
            + ", ".join(sorted(selected))
        )
    return to_yaml(result)


def to_yaml(result: KubernetesRenderResult) -> str:
    """Serialize Kubernetes objects as a stable multi-document YAML stream."""
    header_lines = [
        "# Auto-generated by `agmind render kubernetes` from templates/services/*.yaml.",
        "# Research-grade Kubernetes MVP: review warnings before applying to a cluster.",
    ]
    for warning in result.warnings:
        header_lines.append(f"# WARNING {warning.comment_text()}")
    header = "\n".join(header_lines) + "\n"
    body = yaml.safe_dump_all(
        result.objects,
        explicit_start=True,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
        indent=2,
    )
    return header + body


def collect_portability_warnings(
    descriptor: ServiceDescriptor,
) -> tuple[KubernetesRenderWarning, ...]:
    """Return warnings for Docker Compose fields without a safe k8s mapping."""
    if _is_kubernetes_omitted_service(descriptor):
        return (_kubernetes_omission_warning(descriptor),)

    warnings: list[KubernetesRenderWarning] = []
    if _requires_amd_gpu_resource(descriptor):
        warnings.append(
            KubernetesRenderWarning(
                service=descriptor.name,
                code="amd-gpu-device-plugin",
                severity="warning",
                message=(
                    f"{AMD_GPU_DOCKER_DEVICE} is rendered as Kubernetes extended resource "
                    f"{AMD_GPU_RESOURCE_NAME}"
                ),
                remediation=(
                    "Install the AMD GPU device plugin or GPU Operator on GPU nodes and verify "
                    f"allocatable {AMD_GPU_RESOURCE_NAME} before real cluster promotion."
                ),
            )
        )
    unknown_devices = tuple(
        device for device in descriptor.devices if device != AMD_GPU_DOCKER_DEVICE
    )
    if unknown_devices:
        device_list = ", ".join(unknown_devices)
        warnings.append(
            KubernetesRenderWarning(
                service=descriptor.name,
                code="docker-device",
                severity="blocker",
                message=(
                    f"Docker devices ({device_list}) require Kubernetes device-plugin, "
                    "privileged, or node-specific mapping"
                ),
                remediation="Map hardware through a Kubernetes device plugin or a reviewed privileged node policy.",
            )
        )
    unmapped_groups = _unmapped_group_add(descriptor)
    if unmapped_groups:
        group_list = ", ".join(unmapped_groups)
        warnings.append(
            KubernetesRenderWarning(
                service=descriptor.name,
                code="docker-group-add",
                severity="warning",
                message=(
                    f"group_add values ({group_list}) are Docker-specific group names and "
                    "are not rendered into Kubernetes securityContext"
                ),
                remediation="Provide numeric GID policy for supplementalGroups or replace node-level group assumptions.",
            )
        )
    unmapped_security_options = _unmapped_security_options(descriptor)
    if unmapped_security_options:
        option_list = ", ".join(unmapped_security_options)
        warnings.append(
            KubernetesRenderWarning(
                service=descriptor.name,
                code="docker-security-opt",
                severity="warning",
                message=(
                    f"security_opt values ({option_list}) are Docker-specific and need an "
                    "explicit Kubernetes securityContext policy"
                ),
                remediation="Translate security options into an explicit Kubernetes securityContext policy.",
            )
        )
    resolved_env = _resolved_env_for_descriptor(descriptor)
    for name, value in descriptor.env.items():
        if (
            "${" in value
            and name not in resolved_env
            and _secret_key_ref_for_env(descriptor, name, value, resolved_env) is None
        ):
            warnings.append(
                KubernetesRenderWarning(
                    service=descriptor.name,
                    code="env-interpolation",
                    severity="warning",
                    message=f"environment interpolation in {name} must be resolved before Kubernetes apply",
                    remediation="Resolve this value into a ConfigMap, Secret, or deployment-time variable.",
                )
            )
    for arg in _command_for_descriptor(descriptor):
        if _resolve_interpolated_value(arg, resolved_env) is None and "${" in arg:
            warnings.append(
                KubernetesRenderWarning(
                    service=descriptor.name,
                    code="command-interpolation",
                    severity="warning",
                    message=f"command interpolation in {arg} must be resolved before Kubernetes apply",
                    remediation="Resolve this command argument through descriptor defaults, a ConfigMap, or explicit Kubernetes values.",
                )
            )
    for volume in descriptor.volumes:
        host_path = volume.split(":", 1)[0]
        if host_path == "/var/run/docker.sock":
            if _docker_socket_replaced_by_kubernetes_provider(descriptor):
                continue
            warnings.append(
                KubernetesRenderWarning(
                    service=descriptor.name,
                    code="docker-socket",
                    severity="blocker",
                    message="Docker socket hostPath is node-specific and should be replaced by Kubernetes discovery",
                    remediation="Replace Docker socket discovery with Kubernetes API discovery or remove the dependency.",
                )
            )
    return tuple(warnings)


def _labels_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": descriptor.name,
        "app.kubernetes.io/part-of": "agmind",
        "app.kubernetes.io/component": descriptor.tier,
        "agmind.io/owner": descriptor.owner,
    }


def _deployment_for_descriptor(
    descriptor: ServiceDescriptor,
    namespace: str,
    warnings: tuple[str, ...],
) -> dict[str, Any]:
    labels = _labels_for_descriptor(descriptor)
    pod_metadata: dict[str, Any] = {"labels": dict(labels)}
    if warnings:
        pod_metadata["annotations"] = {"agmind.io/render-warnings": " | ".join(warnings)}

    pod_spec: dict[str, Any] = {"containers": [_container_for_descriptor(descriptor)]}
    pod_security_context = _pod_security_context_for_descriptor(descriptor)
    if pod_security_context:
        pod_spec["securityContext"] = pod_security_context
    volumes, mounts = _volumes_for_descriptor(descriptor)
    if volumes:
        pod_spec["volumes"] = volumes
        pod_spec["containers"][0]["volumeMounts"] = mounts

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": descriptor.name,
            "namespace": namespace,
            "labels": dict(labels),
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app.kubernetes.io/name": descriptor.name}},
            "template": {
                "metadata": pod_metadata,
                "spec": pod_spec,
            },
        },
    }


def _container_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": descriptor.name,
        "image": descriptor.fq_image(),
    }
    ports = _container_ports(descriptor)
    if ports:
        container["ports"] = ports
    resolved_env = _resolved_env_for_descriptor(descriptor)
    env_entries: list[dict[str, Any]] = []
    env_names: set[str] = set()
    if descriptor.env:
        for name, value in sorted(descriptor.env.items()):
            env_entries.append(_env_entry_for_descriptor(descriptor, name, value, resolved_env))
            env_names.add(name)
    # Secrets referenced only in command args (e.g. redis --requirepass) are surfaced as
    # $(NAME) by _command_for_descriptor; k8s substitutes those from the container env, so
    # define them here via secretKeyRef even when the descriptor has no matching env key.
    for secret_name in _command_secret_env_names(descriptor, resolved_env):
        if secret_name not in env_names:
            env_entries.append(
                {
                    "name": secret_name,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": f"agmind-{descriptor.name}-env",
                            "key": secret_name,
                        }
                    },
                }
            )
            env_names.add(secret_name)
    if env_entries:
        container["env"] = env_entries
    if descriptor.command:
        container["args"] = _command_for_descriptor(descriptor)
    security_context = _container_security_context_for_descriptor(descriptor)
    if security_context:
        container["securityContext"] = security_context

    resources = _resources_for_descriptor(descriptor)
    if resources:
        container["resources"] = resources

    probe = _http_probe_for_descriptor(descriptor)
    if probe:
        container["livenessProbe"] = probe
        container["readinessProbe"] = probe

    return container


def _service_for_descriptor(
    descriptor: ServiceDescriptor,
    namespace: str,
) -> dict[str, Any] | None:
    ports = _service_ports(descriptor)
    if not ports:
        return None
    labels = _labels_for_descriptor(descriptor)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": descriptor.name,
            "namespace": namespace,
            "labels": dict(labels),
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {"app.kubernetes.io/name": descriptor.name},
            "ports": ports,
        },
    }


def _container_ports(descriptor: ServiceDescriptor) -> list[dict[str, Any]]:
    ports = []
    for port in _unique_container_ports(descriptor):
        ports.append({"containerPort": port, "name": f"tcp-{port}"})
    return ports


def _service_ports(descriptor: ServiceDescriptor) -> list[dict[str, Any]]:
    ports = []
    for port in _unique_container_ports(descriptor):
        ports.append(
            {
                "name": f"tcp-{port}",
                "port": port,
                "protocol": "TCP",
                "targetPort": port,
            }
        )
    return ports


def _unique_container_ports(descriptor: ServiceDescriptor) -> tuple[int, ...]:
    ports: list[int] = []
    seen: set[int] = set()
    for mapping in descriptor.ports:
        port = int(mapping.split(":")[-1])
        if port not in seen:
            ports.append(port)
            seen.add(port)
    return tuple(ports)


def _volumes_for_descriptor(
    descriptor: ServiceDescriptor,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    volumes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    for index, spec in enumerate(descriptor.volumes, start=1):
        parsed = _parse_volume_spec(spec)
        if parsed is None:
            continue
        host_path, mount_path, read_only = parsed
        if host_path == "/var/run/docker.sock" and _docker_socket_replaced_by_kubernetes_provider(
            descriptor
        ):
            continue
        volume_name = f"{descriptor.name}-volume-{index}"
        volumes.append(
            {
                "name": volume_name,
                "hostPath": {
                    "path": host_path,
                    "type": _hostpath_type(host_path),
                },
            }
        )
        mount: dict[str, Any] = {"name": volume_name, "mountPath": mount_path}
        if read_only:
            mount["readOnly"] = True
        mounts.append(mount)
    return volumes, mounts


def _parse_volume_spec(spec: str) -> tuple[str, str, bool] | None:
    parts = spec.split(":")
    if len(parts) < 2:
        return None
    host_path = parts[0]
    mount_path = parts[1]
    if not host_path.startswith("/") or not mount_path.startswith("/"):
        return None
    return host_path, mount_path, "ro" in parts[2:]


def _command_for_descriptor(descriptor: ServiceDescriptor) -> list[str]:
    if descriptor.name != "traefik" or descriptor.command is None:
        resolved_env = _resolved_env_for_descriptor(descriptor)
        return [
            _command_arg_for_kubernetes(arg, resolved_env) for arg in (descriptor.command or [])
        ]

    rewritten: list[str] = []
    added_kubernetes_provider = False
    for arg in descriptor.command:
        if arg.startswith("--providers.docker"):
            if not added_kubernetes_provider:
                rewritten.append("--providers.kubernetesingress=true")
                added_kubernetes_provider = True
            continue
        rewritten.append(arg)
    if not added_kubernetes_provider and _has_docker_socket_volume(descriptor):
        rewritten.insert(0, "--providers.kubernetesingress=true")
    resolved_env = _resolved_env_for_descriptor(descriptor)
    return [_command_arg_for_kubernetes(arg, resolved_env) for arg in rewritten]


def _command_arg_for_kubernetes(arg: str, resolved_env: dict[str, str]) -> str:
    """Resolve a command arg for the Kubernetes render.

    ``${VAR:-default}`` forms resolve to their literal value. A secret-marked
    ``${SECRET}`` with no usable default is rewritten to the k8s downward-substitution
    form ``$(SECRET)`` — k8s substitutes it from the container env, which carries a
    matching ``secretKeyRef`` (see ``_command_secret_env_names``). This mirrors the
    silent secret resolution the env path already does. Non-secret unresolved ``${...}``
    tokens are left literal so ``collect_portability_warnings`` still flags them.
    """
    resolved = _resolve_interpolated_value(arg, resolved_env)
    if resolved is not None:
        return resolved

    def _substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_secret_placeholder_token(token):
            return f"$({_placeholder_name(token)})"
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_substitute, arg)


def _command_secret_env_names(
    descriptor: ServiceDescriptor, resolved_env: dict[str, str]
) -> list[str]:
    """Secret placeholder names referenced (without a usable default) in command args.

    ``_command_arg_for_kubernetes`` surfaces these as ``$(NAME)`` substitutions, so the
    container env must define each via a ``secretKeyRef`` for k8s to substitute at start.
    Returns names in first-seen order, de-duplicated.
    """
    names: list[str] = []
    for arg in descriptor.command or []:
        if _resolve_interpolated_value(arg, resolved_env) is not None:
            continue
        for token in _PLACEHOLDER_RE.findall(arg):
            if _is_secret_placeholder_token(token):
                name = _placeholder_name(token)
                if name not in names:
                    names.append(name)
    return names


def _docker_socket_replaced_by_kubernetes_provider(descriptor: ServiceDescriptor) -> bool:
    return descriptor.name == "traefik" and _has_docker_socket_volume(descriptor)


def _is_kubernetes_omitted_service(descriptor: ServiceDescriptor) -> bool:
    return _is_compose_only_docker_socket_service(descriptor) or _is_unconfigured_rerank_service(
        descriptor
    )


def _is_compose_only_docker_socket_service(descriptor: ServiceDescriptor) -> bool:
    return descriptor.name in COMPOSE_ONLY_DOCKER_SOCKET_SERVICES and _has_docker_socket_volume(
        descriptor
    )


def _is_unconfigured_rerank_service(descriptor: ServiceDescriptor) -> bool:
    if descriptor.name != "llama-rerank":
        return False
    rerank_file = descriptor.env.get("AGMIND_RERANK_FILE")
    if rerank_file is None:
        return False
    resolved = _resolve_interpolated_value(
        rerank_file,
        {},
        allow_empty_default=True,
    )
    if resolved != "":
        return False
    return any("${AGMIND_RERANK_FILE}" in arg for arg in (descriptor.command or ()))


def _kubernetes_omission_warning(descriptor: ServiceDescriptor) -> KubernetesRenderWarning:
    if _is_unconfigured_rerank_service(descriptor):
        return KubernetesRenderWarning(
            service=descriptor.name,
            code="kubernetes-omitted",
            severity="warning",
            message=(
                "rerank model file is not configured; optional rerank service is "
                "omitted from Kubernetes render"
            ),
            remediation=(
                "Set AGMIND_RERANK_FILE to a GGUF reranker model before Kubernetes "
                "promotion, or keep reranking disabled for this target."
            ),
        )
    return KubernetesRenderWarning(
        service=descriptor.name,
        code="kubernetes-omitted",
        severity="warning",
        message="Compose-only Docker management service is omitted from Kubernetes render",
        remediation=(
            "Use a Kubernetes-native management option or remove this service from "
            "Kubernetes target profiles before promotion."
        ),
    )


def _has_docker_socket_volume(descriptor: ServiceDescriptor) -> bool:
    return any(volume.split(":", 1)[0] == "/var/run/docker.sock" for volume in descriptor.volumes)


def _requires_amd_gpu_resource(descriptor: ServiceDescriptor) -> bool:
    return AMD_GPU_DOCKER_DEVICE in descriptor.devices


def _pod_security_context_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, Any]:
    supplemental_groups = _numeric_group_add(descriptor)
    if not supplemental_groups:
        return {}
    return {"supplementalGroups": list(supplemental_groups)}


def _container_security_context_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if descriptor.cap_add:
        context["capabilities"] = {"add": list(descriptor.cap_add)}
    seccomp_profile = _seccomp_profile_for_descriptor(descriptor)
    if seccomp_profile:
        context["seccompProfile"] = seccomp_profile
    return context


def _numeric_group_add(descriptor: ServiceDescriptor) -> tuple[int, ...]:
    return tuple(int(group) for group in descriptor.group_add if group.isdigit())


def _unmapped_group_add(descriptor: ServiceDescriptor) -> tuple[str, ...]:
    covered_groups = (
        AMD_GPU_DOCKER_GROUPS if _requires_amd_gpu_resource(descriptor) else frozenset()
    )
    return tuple(
        group
        for group in descriptor.group_add
        if not group.isdigit() and group not in covered_groups
    )


def _seccomp_profile_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, str]:
    for option in descriptor.security_opt:
        profile = _seccomp_profile_for_security_opt(option)
        if profile:
            return profile
    return {}


def _seccomp_profile_for_security_opt(option: str) -> dict[str, str]:
    if option == "seccomp=unconfined":
        return {"type": "Unconfined"}
    if option == "seccomp=runtime/default":
        return {"type": "RuntimeDefault"}
    return {}


def _unmapped_security_options(descriptor: ServiceDescriptor) -> tuple[str, ...]:
    return tuple(
        option
        for option in descriptor.security_opt
        if not _seccomp_profile_for_security_opt(option)
    )


def _resolved_env_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for name, value in descriptor.env.items():
        resolved_value = _resolve_interpolated_value(value, resolved, allow_empty_default=True)
        if resolved_value is not None:
            resolved[name] = resolved_value
    return resolved


def _env_entry_for_descriptor(
    descriptor: ServiceDescriptor,
    name: str,
    value: str,
    resolved_env: dict[str, str],
) -> dict[str, Any]:
    resolved_value = resolved_env.get(name)
    if resolved_value is not None:
        return {"name": name, "value": resolved_value}
    secret_ref = _secret_key_ref_for_env(descriptor, name, value, resolved_env)
    if secret_ref is not None:
        return {"name": name, "valueFrom": {"secretKeyRef": secret_ref}}
    return {"name": name, "value": value}


def _resolve_interpolated_value(
    value: str,
    variables: dict[str, str],
    *,
    allow_empty_default: bool = False,
) -> str | None:
    current = value
    for _ in range(10):
        match = _PLACEHOLDER_RE.search(current)
        if match is None:
            return current
        token = match.group(1)
        replacement = _resolve_placeholder_token(
            token,
            variables,
            allow_empty_default=allow_empty_default,
        )
        if replacement is None:
            return None
        current = f"{current[: match.start()]}{replacement}{current[match.end() :]}"
    return None


def _resolve_placeholder_token(
    token: str,
    variables: dict[str, str],
    *,
    allow_empty_default: bool = False,
) -> str | None:
    if ":-" in token:
        name, default = token.split(":-", 1)
        value = variables.get(name)
        if value:
            return value
        if not default and not allow_empty_default:
            return None
        return default
    value = variables.get(token)
    return value if value else None


def _secret_key_ref_for_env(
    descriptor: ServiceDescriptor,
    name: str,
    value: str,
    resolved_env: dict[str, str],
) -> dict[str, str] | None:
    if "${" not in value or name in resolved_env:
        return None
    tokens = _PLACEHOLDER_RE.findall(value)
    secret_tokens = tuple(token for token in tokens if _is_secret_placeholder_token(token))
    if not secret_tokens:
        return None
    key = (
        _placeholder_name(secret_tokens[0]) if value.strip() == f"${{{secret_tokens[0]}}}" else name
    )
    return {"name": f"agmind-{descriptor.name}-env", "key": key}


def _is_secret_placeholder_token(token: str) -> bool:
    name = _placeholder_name(token).upper()
    return any(marker in name for marker in _SECRET_TOKEN_MARKERS)


def _placeholder_name(token: str) -> str:
    # Strip any compose guard/default suffix (${VAR:-x}, ${VAR:?err}, ${VAR:+y}). Env
    # var names never contain ':', so the bare name is everything before the first ':'.
    return token.split(":", 1)[0]


def _hostpath_type(host_path: str) -> str:
    name = Path(host_path).name
    return "FileOrCreate" if "." in name else "DirectoryOrCreate"


def _resources_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, dict[str, str]]:
    limits: dict[str, str] = {}
    if descriptor.resources.cpus is not None:
        limits["cpu"] = _cpu_quantity(descriptor.resources.cpus)
    if descriptor.resources.mem_limit is not None:
        limits["memory"] = _memory_quantity(descriptor.resources.mem_limit)
    if _requires_amd_gpu_resource(descriptor):
        limits[AMD_GPU_RESOURCE_NAME] = AMD_GPU_RESOURCE_QUANTITY
    if not limits:
        return {}
    return {"limits": dict(limits), "requests": dict(limits)}


def _cpu_quantity(cpus: float) -> str:
    if cpus < 1:
        return f"{int(cpus * 1000)}m"
    if cpus.is_integer():
        return str(int(cpus))
    return f"{int(cpus * 1000)}m"


def _memory_quantity(value: str) -> str:
    number = value[:-1]
    suffix = value[-1]
    unit = {"k": "Ki", "m": "Mi", "g": "Gi"}[suffix]
    return f"{number}{unit}"


def _http_probe_for_descriptor(descriptor: ServiceDescriptor) -> dict[str, Any]:
    if descriptor.health is None:
        return {}
    test = list(descriptor.health.test)
    url = next((item for item in test if item.startswith("http://localhost:")), "")
    if not url:
        return {}
    port_and_path = url.removeprefix("http://localhost:")
    port_text, _, path = port_and_path.partition("/")
    try:
        port = int(port_text)
    except ValueError:
        return {}
    return {
        "httpGet": {"path": f"/{path}" if path else "/", "port": port},
        "periodSeconds": _duration_seconds(descriptor.health.interval, default=30),
        "timeoutSeconds": _duration_seconds(descriptor.health.timeout, default=5),
        "failureThreshold": descriptor.health.retries,
        "initialDelaySeconds": _duration_seconds(descriptor.health.start_period, default=10),
    }


def _duration_seconds(value: str, *, default: int) -> int:
    if value.endswith("s") and value[:-1].isdigit():
        return int(value[:-1])
    if value.endswith("m") and value[:-1].isdigit():
        return int(value[:-1]) * 60
    return default
