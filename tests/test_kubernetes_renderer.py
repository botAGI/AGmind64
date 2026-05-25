"""Tests for the Kubernetes/k3s renderer MVP."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.schemas import ServiceDescriptor
from agmind.services.kubernetes_renderer import (
    render_kubernetes,
    render_to_string,
    to_yaml,
)

pytestmark = pytest.mark.backend_any


def _descriptor(**overrides: object) -> ServiceDescriptor:
    base: dict[str, object] = {
        "name": "qdrant",
        "image": "qdrant/qdrant:v1.18.0",
        "digest": "abc123",
        "tier": "storage",
        "purpose": "Vector store",
        "profiles": ["core"],
        "ports": ["127.0.0.1:6333:6333"],
        "env": {"QDRANT__SERVICE__GRPC_PORT": "6334"},
        "resources": {"cpus": 2.0, "mem_limit": "8g"},
    }
    base.update(overrides)
    return ServiceDescriptor.model_validate(base)


def _objects_by_kind(objects: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["kind"]): item for item in objects}


def _object_names(objects: list[dict[str, object]], *, kind: str) -> set[str]:
    return {
        str(item["metadata"]["name"])
        for item in objects
        if item.get("kind") == kind and isinstance(item.get("metadata"), dict)
    }


def test_render_kubernetes_includes_namespace_deployment_and_service() -> None:
    result = render_kubernetes([_descriptor()], namespace="agmind")

    by_kind = _objects_by_kind(result.objects)
    assert set(by_kind) == {"Namespace", "Deployment", "Service"}
    assert by_kind["Namespace"]["metadata"] == {"name": "agmind"}

    deployment = by_kind["Deployment"]
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["metadata"]["name"] == "qdrant"
    assert deployment["metadata"]["namespace"] == "agmind"
    assert deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "qdrant"

    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert container["name"] == "qdrant"
    assert container["image"] == "qdrant/qdrant:v1.18.0@sha256:abc123"
    assert container["ports"] == [{"containerPort": 6333, "name": "tcp-6333"}]
    assert container["env"] == [
        {"name": "QDRANT__SERVICE__GRPC_PORT", "value": "6334"},
    ]

    service = by_kind["Service"]
    assert service["metadata"]["name"] == "qdrant"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [
        {"name": "tcp-6333", "port": 6333, "protocol": "TCP", "targetPort": 6333}
    ]


def test_render_kubernetes_maps_hostpath_volumes_and_readonly_mounts() -> None:
    descriptor = _descriptor(
        volumes=[
            "/var/lib/agmind/qdrant:/qdrant/storage",
            "/etc/agmind/qdrant/config.yml:/etc/qdrant/config.yml:ro",
        ]
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert pod_spec["volumes"] == [
        {
            "name": "qdrant-volume-1",
            "hostPath": {"path": "/var/lib/agmind/qdrant", "type": "DirectoryOrCreate"},
        },
        {
            "name": "qdrant-volume-2",
            "hostPath": {"path": "/etc/agmind/qdrant/config.yml", "type": "FileOrCreate"},
        },
    ]
    assert container["volumeMounts"] == [
        {"name": "qdrant-volume-1", "mountPath": "/qdrant/storage"},
        {"name": "qdrant-volume-2", "mountPath": "/etc/qdrant/config.yml", "readOnly": True},
    ]


def test_render_kubernetes_converts_resource_limits() -> None:
    result = render_kubernetes(
        [_descriptor(resources={"cpus": 0.5, "mem_limit": "512m"})],
        namespace="agmind",
    )

    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert container["resources"] == {
        "limits": {"cpu": "500m", "memory": "512Mi"},
        "requests": {"cpu": "500m", "memory": "512Mi"},
    }


def test_render_kubernetes_warns_about_non_portable_fields() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        devices=["/dev/custom0"],
        group_add=["video", "render"],
        security_opt=["label:disable"],
        env={"UNRESOLVED_VALUE": "${UNRESOLVED_VALUE}"},
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    messages = [warning.message for warning in result.warnings]

    assert any("devices" in message for message in messages)
    assert any("group_add" in message for message in messages)
    assert any("security_opt" in message for message in messages)
    assert any("environment interpolation" in message for message in messages)


def test_render_kubernetes_warning_metadata_is_actionable() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        devices=["/dev/custom0"],
        group_add=["video", "render"],
        security_opt=["label:disable"],
        env={"UNRESOLVED_VALUE": "${UNRESOLVED_VALUE}"},
        volumes=["/var/run/docker.sock:/var/run/docker.sock:ro"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    by_code = {warning.code: warning for warning in result.warnings}

    assert set(by_code) == {
        "docker-device",
        "docker-group-add",
        "docker-security-opt",
        "env-interpolation",
        "docker-socket",
    }
    assert by_code["docker-device"].severity == "blocker"
    assert by_code["env-interpolation"].severity == "warning"
    assert "device plugin" in by_code["docker-device"].remediation
    assert "ConfigMap" in by_code["env-interpolation"].remediation


def test_render_kubernetes_maps_supported_security_context_fields() -> None:
    descriptor = _descriptor(
        name="debug-sidecar",
        security_opt=["seccomp=unconfined"],
        cap_add=["SYS_PTRACE"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    by_code = {warning.code: warning for warning in result.warnings}

    assert container["securityContext"] == {
        "capabilities": {"add": ["SYS_PTRACE"]},
        "seccompProfile": {"type": "Unconfined"},
    }
    assert "docker-security-opt" not in by_code
    assert "linux-capability" not in by_code


def test_render_kubernetes_maps_numeric_group_add_to_pod_security_context() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        group_add=["44", "107", "render"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    pod_spec = deployment["spec"]["template"]["spec"]
    by_code = {warning.code: warning for warning in result.warnings}

    assert pod_spec["securityContext"] == {"supplementalGroups": [44, 107]}
    assert by_code["docker-group-add"].severity == "warning"
    assert "render" in by_code["docker-group-add"].message


def test_render_kubernetes_maps_dri_device_to_amd_gpu_resource() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        devices=["/dev/dri"],
        group_add=["video", "render"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    by_code = {warning.code: warning for warning in result.warnings}

    assert container["resources"]["limits"]["amd.com/gpu"] == "1"
    assert container["resources"]["requests"]["amd.com/gpu"] == "1"
    assert "amd-gpu-device-plugin" in by_code
    assert by_code["amd-gpu-device-plugin"].severity == "warning"
    assert "amd.com/gpu" in by_code["amd-gpu-device-plugin"].remediation
    assert "docker-device" not in by_code
    assert "docker-group-add" not in by_code


def test_render_kubernetes_warns_about_named_group_add_without_gpu_resource() -> None:
    descriptor = _descriptor(name="sidecar", group_add=["render"])

    result = render_kubernetes([descriptor], namespace="agmind")
    by_code = {warning.code: warning for warning in result.warnings}

    assert by_code["docker-group-add"].severity == "warning"
    assert "render" in by_code["docker-group-add"].message


def test_render_kubernetes_resolves_default_env_interpolation() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        env={
            "MODEL": "${MODEL:-model.gguf}",
            "CTX": "${CTX:-${BASE_CTX:-8192}}",
            "UNRESOLVED_VALUE": "${UNRESOLVED_VALUE}",
        },
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}
    warnings = [warning for warning in result.warnings if warning.code == "env-interpolation"]

    assert env["MODEL"] == "model.gguf"
    assert env["CTX"] == "8192"
    assert env["UNRESOLVED_VALUE"] == "${UNRESOLVED_VALUE}"
    assert [(warning.service, warning.message) for warning in warnings] == [
        (
            "llama-llm",
            "environment interpolation in UNRESOLVED_VALUE must be resolved before Kubernetes apply",
        )
    ]


def test_render_kubernetes_maps_secret_env_interpolation_to_secret_key_refs() -> None:
    descriptor = _descriptor(
        name="postgres",
        env={
            "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}",
            "DATA_SOURCE_NAME": "postgresql://dify:${POSTGRES_PASSWORD}@postgres:5432/dify?sslmode=disable",
            "AGMIND_RERANK_FILE": "${AGMIND_RERANK_FILE:-}",
        },
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    warnings = [warning for warning in result.warnings if warning.code == "env-interpolation"]

    assert env["POSTGRES_PASSWORD"] == {
        "name": "POSTGRES_PASSWORD",
        "valueFrom": {
            "secretKeyRef": {
                "name": "agmind-postgres-env",
                "key": "POSTGRES_PASSWORD",
            }
        },
    }
    assert env["DATA_SOURCE_NAME"] == {
        "name": "DATA_SOURCE_NAME",
        "valueFrom": {
            "secretKeyRef": {
                "name": "agmind-postgres-env",
                "key": "DATA_SOURCE_NAME",
            }
        },
    }
    assert env["AGMIND_RERANK_FILE"] == {
        "name": "AGMIND_RERANK_FILE",
        "value": "",
    }
    assert warnings == []


def test_render_kubernetes_injects_capability_env_for_consumers() -> None:
    milvus = _descriptor(
        name="milvus",
        image="milvusdb/milvus:v2.6.6",
        digest=None,
        tier="storage",
        env={},
        ports=[],
        provides=["vector_db"],
    )
    dify_api = _descriptor(
        name="dify-api",
        image="langgenius/dify-api:1.14.2",
        digest=None,
        tier="app",
        env={},
        ports=[],
        consumes=["vector_db"],
    )

    result = render_kubernetes([milvus, dify_api], namespace="agmind")
    deployments = {
        item["metadata"]["name"]: item
        for item in result.objects
        if item.get("kind") == "Deployment"
    }
    env = {
        item["name"]: item["value"]
        for item in deployments["dify-api"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }

    assert env["VECTOR_STORE"] == "milvus"
    assert env["MILVUS_URI"] == "http://milvus:19530"


def test_render_kubernetes_keeps_command_interpolation_unresolved_for_empty_env_default() -> None:
    descriptor = _descriptor(
        name="llama-rerank",
        env={"MODEL_FILE": "${MODEL_FILE:-}"},
        command=["--model", "/models/${MODEL_FILE}"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}
    by_code = {warning.code: warning for warning in result.warnings}

    assert env["MODEL_FILE"] == ""
    assert container["args"] == ["--model", "/models/${MODEL_FILE}"]
    assert "env-interpolation" not in by_code
    assert by_code["command-interpolation"].severity == "warning"
    assert "/models/${MODEL_FILE}" in by_code["command-interpolation"].message


def test_render_kubernetes_omits_rerank_when_model_file_is_empty_default() -> None:
    descriptor = _descriptor(
        name="llama-rerank",
        env={"AGMIND_RERANK_FILE": "${AGMIND_RERANK_FILE:-}"},
        command=["--model", "/models/${AGMIND_RERANK_FILE}"],
        devices=["/dev/dri"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    by_code = {warning.code: warning for warning in result.warnings}

    assert "llama-rerank" not in _object_names(result.objects, kind="Deployment")
    assert "llama-rerank" not in _object_names(result.objects, kind="Service")
    assert set(by_code) == {"kubernetes-omitted"}
    assert by_code["kubernetes-omitted"].service == "llama-rerank"
    assert "rerank model file is not configured" in by_code["kubernetes-omitted"].message
    assert "command-interpolation" not in by_code
    assert "amd-gpu-device-plugin" not in by_code


def test_render_kubernetes_resolves_command_interpolation_from_env_defaults() -> None:
    descriptor = _descriptor(
        name="llama-llm",
        env={
            "MODEL": "${MODEL:-model.gguf}",
            "CTX": "${CTX:-${BASE_CTX:-8192}}",
        },
        command=["--model", "/models/${MODEL}", "--ctx-size", "${CTX}", "${MISSING}"],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    by_code = {warning.code: warning for warning in result.warnings}

    assert container["args"] == [
        "--model",
        "/models/model.gguf",
        "--ctx-size",
        "8192",
        "${MISSING}",
    ]
    assert "env-interpolation" not in by_code
    assert by_code["command-interpolation"].severity == "warning"
    assert "${MISSING}" in by_code["command-interpolation"].message


def test_render_kubernetes_keeps_unknown_devices_as_blockers() -> None:
    descriptor = _descriptor(name="llama-llm", devices=["/dev/custom0"])

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    by_code = {warning.code: warning for warning in result.warnings}

    assert "amd.com/gpu" not in container["resources"]["limits"]
    assert by_code["docker-device"].severity == "blocker"
    assert "custom0" in by_code["docker-device"].message


def test_render_kubernetes_replaces_traefik_docker_provider() -> None:
    descriptor = _descriptor(
        name="traefik",
        image="traefik:v3.7.1",
        digest=None,
        tier="edge",
        volumes=[
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "/var/lib/agmind/traefik/dynamic:/etc/traefik/dynamic:ro",
        ],
        command=[
            "--providers.docker=true",
            "--providers.docker.exposedbydefault=false",
            "--providers.file.directory=/etc/traefik/dynamic",
        ],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    deployment = _objects_by_kind(result.objects)["Deployment"]
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert all(
        volume["hostPath"]["path"] != "/var/run/docker.sock" for volume in pod_spec["volumes"]
    )
    assert "--providers.kubernetesingress=true" in container["args"]
    assert "--providers.docker=true" not in container["args"]
    assert "--providers.docker.exposedbydefault=false" not in container["args"]
    assert not any(
        warning.service == "traefik" and warning.code == "docker-socket"
        for warning in result.warnings
    )


def test_render_kubernetes_omits_portainer_docker_socket_service() -> None:
    descriptor = _descriptor(
        name="portainer",
        image="portainer/portainer-ce:2.41.1",
        digest=None,
        tier="ops",
        ports=["127.0.0.1:9443:9443"],
        volumes=[
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "/var/lib/agmind/portainer:/data",
        ],
    )

    result = render_kubernetes([descriptor], namespace="agmind")
    by_code = {warning.code: warning for warning in result.warnings}

    assert "portainer" not in _object_names(result.objects, kind="Deployment")
    assert "portainer" not in _object_names(result.objects, kind="Service")
    assert set(by_code) == {"kubernetes-omitted"}
    assert by_code["kubernetes-omitted"].severity == "warning"
    assert "Compose-only" in by_code["kubernetes-omitted"].message
    assert not any(
        warning.service == "portainer" and warning.code == "docker-socket"
        for warning in result.warnings
    )


def test_render_kubernetes_strict_rejects_non_portable_fields() -> None:
    descriptor = _descriptor(name="llama-llm", devices=["/dev/dri"])

    with pytest.raises(ValueError, match="non-portable Kubernetes render warnings"):
        render_kubernetes([descriptor], namespace="agmind", strict=True)


def test_kubernetes_yaml_is_multi_document_and_contains_warning_comments() -> None:
    descriptor = _descriptor(name="llama-llm", devices=["/dev/custom0"])

    text = to_yaml(render_kubernetes([descriptor], namespace="agmind"))
    docs = list(yaml.safe_load_all(text))

    assert "# WARNING llama-llm:" in text
    assert "[docker-device/blocker]" in text
    assert [doc["kind"] for doc in docs] == ["Namespace", "Deployment", "Service"]


def test_render_to_string_filters_real_profile() -> None:
    rendered = render_to_string(profiles=["proxmox"], namespace="agmind")
    docs = list(yaml.safe_load_all(rendered))
    deployment_names = {
        doc["metadata"]["name"] for doc in docs if doc and doc.get("kind") == "Deployment"
    }

    assert deployment_names == {"proxmox-exporter"}


def test_render_to_string_writes_selected_services_from_temp_catalog(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    descriptor = _descriptor(name="demo-api", image="example/demo:1.0.0", digest=None)
    (services_dir / "demo-api.yaml").write_text(
        yaml.safe_dump(descriptor.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    rendered = render_to_string(
        services=["demo-api"],
        services_dir=services_dir,
        namespace="demo",
        include_namespace=False,
    )
    docs = list(yaml.safe_load_all(rendered))

    assert [doc["kind"] for doc in docs] == ["Deployment", "Service"]
    assert docs[0]["metadata"]["namespace"] == "demo"
