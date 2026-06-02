"""Deployment target contracts for homelab and enterprise lanes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agmind.core.paths import data_root

RuntimeKind = Literal["compose", "kubernetes", "nomad"]
ProvisionerKind = Literal["none", "opentofu-proxmox", "external"]
ConfiguratorKind = Literal["ansible", "helm", "kustomize", "talosctl", "kubectl", "none"]
StorageProfile = Literal["local-paths", "proxmox-zfs", "nfs", "longhorn", "ceph", "external"]
SecretsProfile = Literal["env-files", "sops-age", "external-secrets", "vault", "infisical"]
TargetStatus = Literal["supported", "experimental", "research"]

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")

REPO_ROOT = data_root()
DEFAULT_DEPLOY_TARGETS_DIR = REPO_ROOT / "templates" / "deploy-targets"


class DeploymentRuntime(BaseModel):
    """Runtime renderer and profile boundary for a deployment target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RuntimeKind
    renderer: str = Field(min_length=1)
    profiles: tuple[str, ...] = ()
    excluded_services: tuple[str, ...] = ()

    @field_validator("profiles", "excluded_services")
    @classmethod
    def _check_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for token in value:
            if not _TOKEN_RE.match(token):
                raise ValueError(f"runtime token '{token}' invalid")
        return value


class DeploymentProvisioner(BaseModel):
    """Infrastructure provisioning boundary before host configuration runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ProvisionerKind
    module: str = ""
    state_backend: Literal["local", "remote", "external"] = "local"
    outputs: tuple[str, ...] = ()

    @field_validator("outputs")
    @classmethod
    def _check_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for output in value:
            if not _TOKEN_RE.match(output):
                raise ValueError(f"output '{output}' invalid")
        return value


class DeploymentConfigurator(BaseModel):
    """Configuration layer used after infrastructure exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ConfiguratorKind
    inventory_source: str = ""
    playbooks: tuple[str, ...] = ()
    charts: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()


class DeploymentExpectedWarning(BaseModel):
    """One explicitly accepted Kubernetes render warning for a target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    code: str

    @field_validator("service", "code")
    @classmethod
    def _check_token(cls, value: str) -> str:
        if not _TOKEN_RE.match(value):
            raise ValueError(f"expected warning token '{value}' invalid")
        return value


class DeploymentVerification(BaseModel):
    """Commands and artifacts used to validate the target lane."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    expected_warning_codes: tuple[str, ...] = ()
    expected_warnings: tuple[DeploymentExpectedWarning, ...] = ()

    @field_validator("expected_warning_codes")
    @classmethod
    def _check_expected_warning_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for code in value:
            if not _TOKEN_RE.match(code):
                raise ValueError(f"expected warning code '{code}' invalid")
        return value

    @field_validator("expected_warnings")
    @classmethod
    def _check_expected_warnings(
        cls,
        value: tuple[DeploymentExpectedWarning, ...],
    ) -> tuple[DeploymentExpectedWarning, ...]:
        pairs = [(warning.service, warning.code) for warning in value]
        if len(set(pairs)) != len(pairs):
            raise ValueError("expected warnings must not contain duplicate service/code pairs")
        return value


class DeploymentTarget(BaseModel):
    """Deploy lane contract that keeps provisioning separate from service runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = Field(min_length=1)
    status: TargetStatus
    summary: str = Field(min_length=1)
    runtime: DeploymentRuntime
    provisioner: DeploymentProvisioner
    configurator: DeploymentConfigurator
    storage_profile: StorageProfile
    secrets_profile: SecretsProfile
    verification: DeploymentVerification = Field(default_factory=DeploymentVerification)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(
                f"id '{value}' invalid: expected lowercase slug matching ^[a-z][a-z0-9-]{{1,62}}$"
            )
        return value

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate a deployment target from YAML."""
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


def load_deploy_targets(
    root: Path = DEFAULT_DEPLOY_TARGETS_DIR,
) -> dict[str, DeploymentTarget]:
    """Load deployment targets under ``root`` and return them sorted by id."""
    if not root.exists():
        return {}

    targets = [
        DeploymentTarget.from_yaml(path)
        for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")])
    ]
    loaded: dict[str, DeploymentTarget] = {}
    for target in sorted(targets, key=lambda item: item.id):
        if target.id in loaded:
            raise ValueError(f"duplicate deployment target id '{target.id}'")
        loaded[target.id] = target
    return loaded
