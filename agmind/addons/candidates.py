"""Candidate catalog for optional AGmind tools and service profiles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agmind.core.paths import data_root

CandidateStatus = Literal["candidate", "accepted", "deferred", "rejected"]
CandidateCategory = Literal[
    "creative-ai",
    "automation",
    "identity",
    "secrets",
    "registry",
    "backup",
    "observability",
    "storage",
    "homelab",
]
CandidateScope = Literal["service-profile", "deploy-target-addon", "external-integration"]
CandidateRuntime = Literal["compose", "kubernetes", "external", "none"]
AdmissionContract = Literal["provision", "configure", "render", "operate", "recover", "secure"]

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_PORT_RE = re.compile(r"^[0-9]{2,5}(/[a-z]+)?$")

REPO_ROOT = data_root()
DEFAULT_TOOL_CANDIDATES_DIR = REPO_ROOT / "templates" / "tool-candidates"


class CandidateAdmission(BaseModel):
    """Contracts that must exist before a candidate becomes a real feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: CandidateScope
    runtime: CandidateRuntime
    contracts: tuple[AdmissionContract, ...]
    component_contract_required: bool = True
    service_descriptor_required: bool = True
    image_pin_required: bool = True


class CandidateDependencies(BaseModel):
    """Target, profile, storage, secrets, and port assumptions for a candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deploy_targets: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    storage_profiles: tuple[str, ...] = ()
    secrets_profiles: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    requires_gpu: bool = False

    @field_validator("deploy_targets", "profiles", "storage_profiles", "secrets_profiles")
    @classmethod
    def _check_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _TOKEN_RE.match(item):
                raise ValueError(f"token '{item}' invalid")
        return value

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for port in value:
            if not _PORT_RE.match(port):
                raise ValueError(f"port '{port}' invalid")
        return value


class CandidateVerification(BaseModel):
    """Verification commands and research references for a candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: tuple[str, ...] = ()
    research_refs: tuple[str, ...] = ()


class ToolCandidate(BaseModel):
    """A vetted optional tool before it becomes a component/service descriptor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = Field(min_length=1)
    status: CandidateStatus
    category: CandidateCategory
    summary: str = Field(min_length=1)
    recommended_version: str = ""
    version_source: str = ""
    admission: CandidateAdmission
    dependencies: CandidateDependencies = Field(default_factory=CandidateDependencies)
    risks: tuple[str, ...] = ()
    next_step: str = Field(min_length=1)
    verification: CandidateVerification = Field(default_factory=CandidateVerification)

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
        """Load and validate a tool candidate from YAML."""
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


def load_tool_candidates(
    root: Path = DEFAULT_TOOL_CANDIDATES_DIR,
) -> dict[str, ToolCandidate]:
    """Load all tool candidates under ``root`` and return them sorted by id."""
    if not root.exists():
        return {}

    candidates = [
        ToolCandidate.from_yaml(path)
        for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")])
    ]
    loaded: dict[str, ToolCandidate] = {}
    for candidate in sorted(candidates, key=lambda item: item.id):
        if candidate.id in loaded:
            raise ValueError(f"duplicate tool candidate id '{candidate.id}'")
        loaded[candidate.id] = candidate
    return loaded
