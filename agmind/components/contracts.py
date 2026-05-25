"""Pydantic models for AGmind component contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ComponentKind = Literal[
    "core",
    "compute",
    "inference",
    "app",
    "stateful",
    "edge",
    "ops",
    "deploy_target",
    "model",
    "tool",
]
UpdatePolicy = Literal[
    "strict-pin",
    "compatible-patch",
    "compatible-minor",
    "upstream-compatible",
    "manual-hold",
]
VersionSource = Literal["registry", "github_release", "pypi", "manual", "local"]

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class ComponentCore(BaseModel):
    """Upstream identity and AGmind's recommended version baseline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream: str = Field(min_length=1)
    min_version: str | None = None
    recommended_version: str = Field(min_length=1)
    current_pin: str | None = None
    update_policy: UpdatePolicy = "compatible-minor"
    hold_reason: str = ""
    source: VersionSource = "registry"


class ComponentRuntime(BaseModel):
    """Deploy-time artifacts owned by a component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_descriptors: tuple[str, ...] = ()
    compose_profiles: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    dockerfiles: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    python_packages: tuple[str, ...] = ()
    ansible_collections: tuple[str, ...] = ()
    model_catalogs: tuple[str, ...] = ()

    @field_validator("service_descriptors")
    @classmethod
    def _check_service_descriptors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for service in value:
            if not _SERVICE_RE.match(service):
                raise ValueError(f"service descriptor '{service}' invalid")
        return value


class ComponentRequirements(BaseModel):
    """Capabilities or components required before this component can run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: tuple[str, ...] = ()
    components: tuple[str, ...] = ()

    @field_validator("capabilities", "components")
    @classmethod
    def _check_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _TOKEN_RE.match(item):
                raise ValueError(f"token '{item}' invalid")
        return value


class ComponentConflicts(BaseModel):
    """Known mutually-exclusive services, components, ports, or capabilities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    services: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @field_validator("services", "components", "capabilities")
    @classmethod
    def _check_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _TOKEN_RE.match(item):
                raise ValueError(f"token '{item}' invalid")
        return value


class ComponentVerification(BaseModel):
    """Commands and schema references used to validate a component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    smoke: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    schema_refs: tuple[str, ...] = ()


class ComponentContract(BaseModel):
    """Version, runtime, dependency, and verification contract for one component."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: ComponentKind
    core: ComponentCore
    runtime: ComponentRuntime = Field(default_factory=ComponentRuntime)
    provides: tuple[str, ...] = ()
    requires: ComponentRequirements = Field(default_factory=ComponentRequirements)
    conflicts: ComponentConflicts = Field(default_factory=ComponentConflicts)
    verification: ComponentVerification = Field(default_factory=ComponentVerification)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(
                f"id '{value}' invalid: expected lowercase slug matching ^[a-z][a-z0-9-]{{1,62}}$"
            )
        return value

    @field_validator("provides")
    @classmethod
    def _check_provides(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for capability in value:
            if not _TOKEN_RE.match(capability):
                raise ValueError(f"capability '{capability}' invalid")
        return value

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        """Load and validate a component contract from YAML."""
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
