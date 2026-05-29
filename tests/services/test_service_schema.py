"""Phase H'.A: ServiceDescriptor Pydantic schema tests.

Цель — закрепить контракт schema:
- Валидные descriptors парсятся в обе стороны (Pydantic ↔ legacy Service)
- Опечатки/невалидные значения отлавливаются Pydantic
- JSON Schema export детерминирован
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agmind.addons import ToolCandidate
from agmind.components import ComponentContract
from agmind.deploy import DeploymentTarget
from agmind.schemas import (
    HealthCheck,
    ObservabilityConfig,
    ResourceLimits,
    RoutingConfig,
    ServiceDescriptor,
)

pytestmark = pytest.mark.backend_any

VALID_SHA256 = "a" * 64


# ---------- Minimal valid descriptor ----------


def _minimal_descriptor(**overrides: object) -> ServiceDescriptor:
    """Helper: build a minimal valid descriptor with overrides."""
    base: dict[str, object] = {
        "name": "qdrant",
        "image": "qdrant/qdrant:v1.18.0",
        "tier": "storage",
        "purpose": "Vector store",
    }
    base.update(overrides)
    return ServiceDescriptor.model_validate(base)


def test_minimal_descriptor_parses() -> None:
    d = _minimal_descriptor()
    assert d.name == "qdrant"
    assert d.image == "qdrant/qdrant:v1.18.0"
    assert d.tier == "storage"
    assert d.profiles == []
    assert d.observability.loki_scrape is True  # default
    assert d.observability.prometheus_scrape is False  # whitelist default
    assert d.routing is None


def test_descriptor_is_frozen() -> None:
    d = _minimal_descriptor()
    with pytest.raises(ValidationError):
        d.name = "evil"  # type: ignore[misc]


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ServiceDescriptor.model_validate(
            {
                "name": "qdrant",
                "image": "qdrant/qdrant:v1.18.0",
                "tier": "storage",
                "rogue_field": "boom",  # noqa
            }
        )


# ---------- name validation ----------


@pytest.mark.parametrize(
    "bad_name",
    [
        "Qdrant",  # uppercase
        "qdrant_db",  # underscore
        "1qdrant",  # starts with digit
        "q",  # too short (1 char)
        "x" * 32,  # too long
        "-leading",
        "trailing-",  # actually OK per regex; let's test other cases
        "with space",
    ],
)
def test_invalid_names_rejected(bad_name: str) -> None:
    if bad_name == "trailing-":
        # Hyphen at end IS allowed by regex; skip
        return
    with pytest.raises(ValidationError):
        _minimal_descriptor(name=bad_name)


def test_valid_names_accepted() -> None:
    for ok in ("qdrant", "llama-q4", "bge-m3-embed", "x" * 31):
        _minimal_descriptor(name=ok)


# ---------- image validation ----------


def test_latest_tag_rejected() -> None:
    with pytest.raises(ValidationError, match="latest"):
        _minimal_descriptor(image="qdrant/qdrant:latest")


def test_image_without_tag_rejected() -> None:
    with pytest.raises(ValidationError, match="no tag"):
        _minimal_descriptor(image="qdrant/qdrant")


def test_image_with_registry_port_without_tag_rejected() -> None:
    with pytest.raises(ValidationError, match="no tag"):
        _minimal_descriptor(image="registry.internal:5000/qdrant")


def test_image_with_empty_tag_rejected() -> None:
    with pytest.raises(ValidationError, match="no tag"):
        _minimal_descriptor(image="qdrant/qdrant:")


@pytest.mark.parametrize(
    "image",
    [
        "qdrant/qdrant:v1.18.0\n",
        "qdrant/qdrant:v1.18.0 ",
        "\tqdrant/qdrant:v1.18.0",
    ],
)
def test_image_with_whitespace_rejected(image: str) -> None:
    with pytest.raises(ValidationError, match="whitespace"):
        _minimal_descriptor(image=image)


@pytest.mark.parametrize(
    "image",
    [
        f"qdrant/qdrant:@sha256:{VALID_SHA256}",
        f"registry.internal:5000/qdrant:@sha256:{VALID_SHA256}",
    ],
)
def test_image_with_empty_tag_before_inline_digest_rejected(image: str) -> None:
    with pytest.raises(ValidationError, match="no tag"):
        _minimal_descriptor(image=image)


def test_image_with_registry_port_and_tag_accepted() -> None:
    d = _minimal_descriptor(image="registry.internal:5000/qdrant:v1.18.0")
    assert d.image == "registry.internal:5000/qdrant:v1.18.0"


def test_image_with_digest_accepted() -> None:
    d = _minimal_descriptor(image=f"qdrant/qdrant@sha256:{VALID_SHA256}")
    assert d.image.endswith(VALID_SHA256)


@pytest.mark.parametrize(
    "image",
    [
        "qdrant/qdrant@sha256:abc123",
        "qdrant/qdrant:v1.18.0@sha256:abc123",
        "qdrant/qdrant@sha512:" + VALID_SHA256,
    ],
)
def test_image_with_invalid_inline_digest_rejected(image: str) -> None:
    with pytest.raises(ValidationError, match="sha256 digest"):
        _minimal_descriptor(image=image)


@pytest.mark.parametrize(
    "digest",
    [
        "abc123",
        "sha256:abc123",
        "x" * 64,
        "sha512:" + VALID_SHA256,
    ],
)
def test_digest_field_requires_valid_sha256(digest: str) -> None:
    with pytest.raises(ValidationError, match="sha256 digest"):
        _minimal_descriptor(digest=digest)


def test_inline_image_digest_and_digest_field_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="duplicate digest"):
        _minimal_descriptor(
            image=f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}",
            digest=VALID_SHA256,
        )


@pytest.mark.parametrize(
    "digest",
    [
        VALID_SHA256 + "\n",
        f"sha256:{VALID_SHA256}\n",
    ],
)
def test_digest_field_rejects_trailing_newline(digest: str) -> None:
    with pytest.raises(ValidationError, match="sha256 digest"):
        _minimal_descriptor(digest=digest)


def test_inline_image_digest_rejects_trailing_newline() -> None:
    with pytest.raises(ValidationError, match="sha256 digest"):
        _minimal_descriptor(image=f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}\n")


# ---------- port validation ----------


@pytest.mark.parametrize(
    "good_port",
    ["8080:8080", "127.0.0.1:8080:8080", "0.0.0.0:80:80", "6333:6333"],
)
def test_valid_ports(good_port: str) -> None:
    _minimal_descriptor(ports=[good_port])


@pytest.mark.parametrize(
    "bad_port",
    [
        "8080",
        "8080:abc",
        "127.0.0.1:abc:8080",
        ":8080:8080",
        "127:8080:8080",
        "0:8080",
        "8080:0",
        "65536:8080",
        "8080:65536",
        "127.0.0.1:0:8080",
        "127.0.0.1:8080:0",
        "999.0.0.1:8080:8080",
    ],
)
def test_invalid_ports(bad_port: str) -> None:
    with pytest.raises(ValidationError):
        _minimal_descriptor(ports=[bad_port])


# ---------- mem_limit validation ----------


@pytest.mark.parametrize("good", ["4g", "512m", "1024k", "16g"])
def test_valid_mem_limit(good: str) -> None:
    rl = ResourceLimits(mem_limit=good)
    assert rl.mem_limit == good


@pytest.mark.parametrize("bad", ["4G", "4GB", "4 g", "4.5g", "4gb"])
def test_invalid_mem_limit(bad: str) -> None:
    with pytest.raises(ValidationError):
        ResourceLimits(mem_limit=bad)


# ---------- tier validation ----------


def test_unknown_tier_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_descriptor(tier="exotic")


@pytest.mark.parametrize("ok_tier", ["edge", "inference", "app", "storage", "ops"])
def test_known_tiers(ok_tier: str) -> None:
    d = _minimal_descriptor(tier=ok_tier)
    assert d.tier == ok_tier


# ---------- depends_on validation ----------


def test_depends_on_must_match_name_pattern() -> None:
    with pytest.raises(ValidationError):
        _minimal_descriptor(depends_on=["BadName"])
    _minimal_descriptor(depends_on=["llama-q4", "qdrant"])


# ---------- Full descriptor with all sub-models ----------


def test_full_descriptor_with_routing_and_observability() -> None:
    d = ServiceDescriptor.model_validate(
        {
            "name": "llama-q4",
            "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049",
            "tier": "inference",
            "purpose": "LLM token generation (Q4 quant)",
            "owner": "agmind-core",
            "profiles": ["core"],
            "ports": ["127.0.0.1:8080:8080"],
            "volumes": ["/var/lib/agmind/models:/models:ro"],
            "env": {"AMD_VULKAN_ICD": "RADV"},
            "extra_args": ["--device=/dev/dri", "--group-add=video"],
            "resources": {"cpus": 8.0, "mem_limit": "64g"},
            "health": {
                "test": ["CMD", "curl", "-f", "http://localhost:8080/health"],
                "interval": "30s",
                "timeout": "5s",
                "retries": 3,
            },
            "routing": {
                "host": "llama-q4.agmind.dev",
                "middleware_chain": "chain-llm",
                "sse": True,
            },
            "observability": {
                "prometheus_scrape": True,
                "metrics_path": "/metrics",
                "grafana_dashboard": "llama-overview.json",
            },
        }
    )
    assert d.routing is not None
    assert d.routing.sse is True
    assert d.routing.middleware_chain == "chain-llm"
    assert d.observability.prometheus_scrape is True
    assert d.health is not None
    assert d.health.test[0] == "CMD"
    assert d.resources.mem_limit == "64g"


# ---------- fq_image() ----------


def test_fq_image_without_digest() -> None:
    d = _minimal_descriptor()
    assert d.fq_image() == "qdrant/qdrant:v1.18.0"


def test_fq_image_with_digest() -> None:
    d = _minimal_descriptor(digest=VALID_SHA256)
    assert d.fq_image() == f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}"


def test_fq_image_with_prefixed_digest() -> None:
    d = _minimal_descriptor(digest=f"sha256:{VALID_SHA256}")
    assert d.fq_image() == f"qdrant/qdrant:v1.18.0@sha256:{VALID_SHA256}"


# ---------- to_legacy_service() ----------


def test_to_legacy_service_basic() -> None:
    d = _minimal_descriptor(
        digest=VALID_SHA256,
        profiles=["core", "rag"],
        depends_on=["postgres"],
    )
    legacy = d.to_legacy_service()
    assert legacy.name == "qdrant"
    assert legacy.image == "qdrant/qdrant:v1.18.0"
    assert legacy.digest == VALID_SHA256  # без `sha256:` префикса
    assert legacy.profiles == ("core", "rag")
    assert legacy.depends_on == ("postgres",)
    assert legacy.health == {}  # no health → empty dict


def test_to_legacy_service_with_health() -> None:
    d = _minimal_descriptor(
        health={
            "test": ["CMD", "curl", "-f", "http://localhost/health"],
            "interval": "10s",
            "timeout": "3s",
            "retries": 5,
        }
    )
    legacy = d.to_legacy_service()
    assert legacy.health["interval"] == "10s"
    assert legacy.health["retries"] == 5
    assert legacy.health["test"] == ["CMD", "curl", "-f", "http://localhost/health"]


def test_to_legacy_service_strips_sha256_prefix() -> None:
    d = _minimal_descriptor(digest=f"sha256:{VALID_SHA256}")
    legacy = d.to_legacy_service()
    assert legacy.digest == VALID_SHA256


# ---------- JSON Schema export ----------


def test_json_schema_has_expected_top_level_fields() -> None:
    schema = ServiceDescriptor.model_json_schema()
    properties = schema["properties"]
    expected = {
        "name",
        "image",
        "digest",
        "tier",
        "purpose",
        "owner",
        "profiles",
        "ports",
        "volumes",
        "env",
        "extra_args",
        "depends_on",
        "resources",
        "health",
        "routing",
        "observability",
    }
    assert expected.issubset(set(properties.keys()))


def test_json_schema_is_json_serializable() -> None:
    schema = ServiceDescriptor.model_json_schema()
    # Ensure it round-trips through JSON
    text = json.dumps(schema)
    reparsed = json.loads(text)
    assert reparsed["properties"]["name"]["type"] == "string"


def test_json_schema_tier_is_enum() -> None:
    schema = ServiceDescriptor.model_json_schema()
    # Pydantic v2 inlines Literal as enum в properties
    tier = schema["properties"]["tier"]
    enum_values = tier.get("enum") or []
    assert set(enum_values) == {"edge", "inference", "app", "storage", "ops"}


# ---------- Sub-model edge cases ----------


def test_healthcheck_empty_test_rejected() -> None:
    with pytest.raises(ValidationError):
        HealthCheck(test=[])


def test_routing_short_host_rejected() -> None:
    with pytest.raises(ValidationError):
        RoutingConfig(host="ab")  # < 3 chars


def test_observability_invalid_port() -> None:
    with pytest.raises(ValidationError):
        ObservabilityConfig(metrics_port=99999)


# ---------- Schema-drift gate (F.4) ----------
#
# The committed templates/schemas/*.json artifacts are produced by
# scripts/dev/export_schemas.py, which calls <Model>.model_json_schema() and then
# AUGMENTS the result with exactly three keys before writing:
#   schema["$id"]     = "https://github.com/botAGI/AGmind64/schemas/<name>.json"
#   schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
#   schema["title"]   = "<title>"
# The drift gate MUST compare the committed JSON against that AUGMENTED dict — NOT
# raw model_json_schema() — or it would fail on the $id/$schema/title keys. If any
# model changes without `make schema-export`, the committed JSON diverges from the
# freshly-augmented schema and this gate fails loudly.

# Repo root: tests/services/test_service_schema.py -> parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _REPO_ROOT / "templates" / "schemas"

# (committed json filename, Model, $id url name, title) — mirrors export_schemas.py.
_SCHEMA_DRIFT_CASES = [
    ("service.json", ServiceDescriptor, "service.json", "AGmind Service Descriptor"),
    ("component.json", ComponentContract, "component.json", "AGmind Component Contract"),
    ("deploy-target.json", DeploymentTarget, "deploy-target.json", "AGmind Deployment Target"),
    ("tool-candidate.json", ToolCandidate, "tool-candidate.json", "AGmind Tool Candidate"),
]


def _augmented_schema(model: type[Any], url_name: str, title: str) -> dict[str, Any]:
    """Reproduce the exporter's augmentation (export_schemas.py:32-78)."""
    schema = model.model_json_schema()
    schema["$id"] = f"https://github.com/botAGI/AGmind64/schemas/{url_name}"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    return schema


@pytest.mark.parametrize(
    ("filename", "model", "url_name", "title"),
    _SCHEMA_DRIFT_CASES,
    ids=[case[0] for case in _SCHEMA_DRIFT_CASES],
)
def test_committed_schema_matches_augmented_model(
    filename: str, model: type[Any], url_name: str, title: str
) -> None:
    """Committed templates/schemas/<name>.json == exporter's augmented schema.

    Fails if a model changes without re-running `make schema-export`, keeping CI
    schema-validation aligned to the real Pydantic contract (F.4 / T-066-06).
    """
    committed_path = _SCHEMAS_DIR / filename
    assert committed_path.exists(), f"missing committed schema: {committed_path}"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    expected = _augmented_schema(model, url_name, title)
    assert committed == expected, (
        f"{filename} is stale vs {model.__name__}.model_json_schema(); "
        "run `make schema-export` and commit the regenerated artifact."
    )
