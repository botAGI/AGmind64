"""Tier-based GGUF model resolution.

Reads `templates/models.yaml`, auto-detects tier по системной RAM, выбирает
LLM/embed/rerank/VLM models с правильным quant для Strix Halo gfx1151.

См. R-llm-models.md для полного inventory + bench data.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from agmind.core.logging import logger

log = logger(__name__)

Tier = Literal["S", "M", "L", "XL", "XXL"]
ModelKind = Literal["llm", "embed", "rerank"]
_VALID_TIERS: tuple[Tier, ...] = ("S", "M", "L", "XL", "XXL")
_VALID_MODEL_KINDS: tuple[ModelKind, ...] = ("llm", "embed", "rerank")
_HF_REPO_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")

# Tier thresholds — основаны на R10 (effective GTT pool ≈ 94% системной RAM).
# Выбираем tier по RAM, потому что GTT может не быть настроен (warning у doctor).
_TIER_RAM_THRESHOLDS_GB: dict[Tier, int] = {
    "S": 16,
    "M": 32,
    "L": 64,
    "XL": 128,
    "XXL": 160,  # 128 GB+ с большим UMA budget
}

_DEFAULT_MODELS_YAML = Path(__file__).resolve().parents[2] / "templates" / "models.yaml"


@dataclass(frozen=True)
class ModelSpec:
    """Один model entry из models.yaml."""

    name: str
    hf_repo: str
    filename: str
    quant: str
    size_gb: float
    role: str = "primary"  # primary | fallback | strix_optimized | ab_candidate
    verification: str = "inferred"  # verified-strix | verified-llamacpp | inferred | unverified
    license: str = ""
    languages: str = ""
    ctx_native: int = 0
    backend_preferred: str = ""
    server_flags: tuple[str, ...] = ()
    revision: str = ""  # optional HF commit/branch/tag pin; "" → mutable `main`
    sha256: str = ""  # optional content checksum; "" → no post-download verify
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def hf_url(self) -> str:
        """HuggingFace direct download URL (pinned to ``revision`` when set)."""
        return hf_resolve_url(self.hf_repo, self.filename, revision=self.revision)

    @property
    def local_filename(self) -> str:
        """Filename как стоит на диске (не учитывая mmproj sidecars)."""
        return self.filename


@dataclass(frozen=True)
class CuratedModelEntry:
    """One short-list model recommendation used by the setup wizard."""

    id: str
    name: str
    repo: str
    file: str
    size_gib: float
    params_b: float
    active_params_b: float | None
    quant: str
    suggested_ctx: int
    description: str
    kind: ModelKind = "llm"
    strix_tested: bool = False
    measured_tg_t_s: float | None = None

    @property
    def display(self) -> str:
        suffix = ""
        if self.measured_tg_t_s is not None:
            suffix = f"  ·  {self.measured_tg_t_s:.0f} t/s"
        elif self.strix_tested:
            suffix = "  ·  tested"
        return f"{self.name}  [{self.size_gib:.1f} GB]{suffix}"


@dataclass(frozen=True)
class ModelTier:
    """Tier (S/M/L/XL/XXL) с primary + optional fallback/strix_optimized."""

    tier: Tier
    description: str
    primary: ModelSpec
    fallback: ModelSpec | None = None
    strix_optimized: ModelSpec | None = None
    fallback_coding: ModelSpec | None = None


@dataclass(frozen=True)
class ModelsRegistry:
    """Full inventory из models.yaml."""

    schema_version: int
    last_updated: str
    llama_cpp_min_build: str
    llama_cpp_recommended_build: str
    llm_tiers: dict[Tier, ModelTier]
    embedding_primary: ModelSpec
    embedding_ab: ModelSpec | None
    reranker_primary: ModelSpec
    reranker_ab: ModelSpec | None
    vlm_light: ModelSpec | None
    vlm_quality: ModelSpec | None
    antipatterns: tuple[dict[str, str], ...]


def _read_yaml(path: Path) -> dict[str, Any]:
    """Re-use service registry's YAML reader (PyYAML if available, else fallback)."""
    from agmind.services.registry import _parse_yaml

    if not path.exists():
        log.warning("models.yaml not found at %s", path)
        return {}
    return _parse_yaml(path.read_text(encoding="utf-8"))


def _model_from_dict(name: str, data: dict[str, Any], role: str = "primary") -> ModelSpec:
    """Build ModelSpec from yaml dict."""
    extras: dict[str, Any] = {}
    for k, v in data.items():
        if k not in {
            "name",
            "hf_repo",
            "filename",
            "quant",
            "size_gb",
            "verification",
            "license",
            "languages_supported",
            "ctx_native",
            "backend_preferred",
            "server_flags",
            "revision",
            "sha256",
        }:
            extras[k] = v
    return ModelSpec(
        name=name or str(data.get("name", "unknown")),
        hf_repo=str(data.get("hf_repo", "")),
        filename=str(data.get("filename", "")),
        quant=str(data.get("quant", "")),
        size_gb=float(data.get("size_gb") or 0.0),
        role=role,
        verification=str(data.get("verification", "inferred")),
        license=str(data.get("license", "")),
        languages=str(data.get("languages_supported") or ""),
        ctx_native=int(data.get("ctx_native") or 0),
        backend_preferred=str(data.get("backend_preferred", "")),
        server_flags=tuple(str(x) for x in (data.get("server_flags") or ())),
        revision=str(data.get("revision") or ""),
        sha256=str(data.get("sha256") or ""),
        extras=extras,
    )


def _curated_model_from_dict(data: dict[str, Any]) -> CuratedModelEntry:
    """Build a wizard curated model entry from templates/models.yaml."""
    raw_kind = str(data.get("kind") or "llm")
    kind: ModelKind = "llm"
    if raw_kind in _VALID_MODEL_KINDS:
        kind = raw_kind
    active = data.get("active_params_b")
    measured = data.get("measured_tg_t_s")
    return CuratedModelEntry(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        repo=str(data.get("repo", "")),
        file=str(data.get("file", "")),
        size_gib=float(data.get("size_gib") or 0.0),
        params_b=float(data.get("params_b") or 0.0),
        active_params_b=float(active) if active is not None else None,
        quant=str(data.get("quant", "")),
        suggested_ctx=int(data.get("suggested_ctx") or 0),
        description=str(data.get("description", "")),
        kind=kind,
        strix_tested=bool(data.get("strix_tested", False)),
        measured_tg_t_s=float(measured) if measured is not None else None,
    )


def load_curated_model_entries(
    path: Path | str | None = None,
) -> tuple[CuratedModelEntry, ...]:
    """Load setup-wizard curated model entries from templates/models.yaml."""
    p = Path(path) if path else _DEFAULT_MODELS_YAML
    raw = _read_yaml(p)
    wizard = raw.get("wizard_catalog") or {}
    entries = wizard.get("entries") if isinstance(wizard, dict) else None
    if not isinstance(entries, list):
        return ()
    return tuple(_curated_model_from_dict(entry) for entry in entries if isinstance(entry, dict))


def load_model_catalog_defaults(path: Path | str | None = None) -> dict[str, str]:
    """Load per-kind wizard defaults from templates/models.yaml."""
    p = Path(path) if path else _DEFAULT_MODELS_YAML
    raw = _read_yaml(p)
    wizard = raw.get("wizard_catalog") or {}
    defaults = wizard.get("defaults") if isinstance(wizard, dict) else None
    if not isinstance(defaults, dict):
        return {
            "llm": "qwen36-a3b-q4km",
            "embed": "bge-m3-q8",
            "rerank": "bge-reranker-v2-m3-q8",
        }
    return {
        "llm": str(defaults.get("llm") or "qwen36-a3b-q4km"),
        "embed": str(defaults.get("embed") or "bge-m3-q8"),
        "rerank": str(defaults.get("rerank") or "bge-reranker-v2-m3-q8"),
    }


def load_models_registry(path: Path | str | None = None) -> ModelsRegistry | None:
    """Load full registry from yaml. Returns None если файл missing."""
    p = Path(path) if path else _DEFAULT_MODELS_YAML
    raw = _read_yaml(p)
    if not raw:
        return None

    llamacpp_req = raw.get("llama_cpp_requirements") or {}

    tiers: dict[Tier, ModelTier] = {}
    for tier_name, tier_data in (raw.get("llm_tiers") or {}).items():
        if tier_name not in _VALID_TIERS:
            continue
        tier_typed: Tier = tier_name
        primary_data = tier_data.get("primary") or {}
        primary = _model_from_dict("", primary_data, role="primary")

        fallback: ModelSpec | None = None
        if isinstance(tier_data.get("fallback"), dict):
            fallback = _model_from_dict("", tier_data["fallback"], role="fallback")

        strix_opt: ModelSpec | None = None
        if isinstance(tier_data.get("strix_optimized"), dict):
            strix_opt = _model_from_dict("", tier_data["strix_optimized"], role="strix_optimized")

        coding: ModelSpec | None = None
        if isinstance(tier_data.get("fallback_coding"), dict):
            coding = _model_from_dict("", tier_data["fallback_coding"], role="fallback_coding")

        tiers[tier_typed] = ModelTier(
            tier=tier_typed,
            description=str(tier_data.get("description", "")),
            primary=primary,
            fallback=fallback,
            strix_optimized=strix_opt,
            fallback_coding=coding,
        )

    embed_root = raw.get("embedding") or {}
    rerank_root = raw.get("reranker") or {}
    vlm_root = raw.get("vlm") or {}

    return ModelsRegistry(
        schema_version=int(raw.get("schema_version") or 1),
        last_updated=str(raw.get("last_updated", "")),
        llama_cpp_min_build=str(llamacpp_req.get("min_build", "")),
        llama_cpp_recommended_build=str(llamacpp_req.get("recommended_build", "")),
        llm_tiers=tiers,
        embedding_primary=_model_from_dict("", embed_root.get("primary") or {}, role="primary"),
        embedding_ab=(
            _model_from_dict("", embed_root["ab_candidate"], role="ab_candidate")
            if isinstance(embed_root.get("ab_candidate"), dict)
            else None
        ),
        reranker_primary=_model_from_dict("", rerank_root.get("primary") or {}, role="primary"),
        reranker_ab=(
            _model_from_dict("", rerank_root["ab_candidate"], role="ab_candidate")
            if isinstance(rerank_root.get("ab_candidate"), dict)
            else None
        ),
        vlm_light=(
            _model_from_dict("", vlm_root["light"], role="light")
            if isinstance(vlm_root.get("light"), dict)
            else None
        ),
        vlm_quality=(
            _model_from_dict("", vlm_root["quality"], role="quality")
            if isinstance(vlm_root.get("quality"), dict)
            else None
        ),
        antipatterns=tuple(
            dict(item) for item in (raw.get("antipatterns") or ()) if isinstance(item, dict)
        ),
    )


def detect_tier(ram_gib: float | None = None) -> Tier:
    """Auto-select tier по системной RAM.

    Args:
        ram_gib: override (для тестов). None → /proc/meminfo MemTotal.

    Returns:
        Tier "S" | "M" | "L" | "XL" | "XXL".
    """
    if ram_gib is None:
        from agmind.compute.detect import detect_host

        ram_gib = detect_host().system_ram_bytes / 1024**3

    # Highest tier <= ram_gib
    if ram_gib >= _TIER_RAM_THRESHOLDS_GB["XXL"] * 0.9:  # 90% of 160 = 144
        return "XXL"
    if ram_gib >= _TIER_RAM_THRESHOLDS_GB["XL"] * 0.75:  # 75% of 128 = 96
        return "XL"
    if ram_gib >= _TIER_RAM_THRESHOLDS_GB["L"] * 0.85:  # 85% of 64 = 54.4
        return "L"
    if ram_gib >= _TIER_RAM_THRESHOLDS_GB["M"] * 0.85:  # 85% of 32 = 27.2
        return "M"
    return "S"


def resolve_llm(
    tier: Tier | None = None,
    *,
    prefer_strix_optimized: bool = True,
    coding: bool = False,
    registry: ModelsRegistry | None = None,
) -> ModelSpec | None:
    """Resolve LLM model для tier.

    Args:
        tier: явный tier. None → auto-detect.
        prefer_strix_optimized: использовать 0xSero variant если он есть.
        coding: предпочитать coding-focused (Qwen3-Coder family).
        registry: pre-loaded ModelsRegistry (для тестов).

    Returns:
        ModelSpec или None если registry missing / tier не найден.
    """
    if registry is None:
        registry = load_models_registry()
    if registry is None:
        return None

    if tier is None:
        tier = detect_tier()

    tier_data = registry.llm_tiers.get(tier)
    if tier_data is None:
        return None

    if coding and tier_data.fallback_coding is not None:
        return tier_data.fallback_coding
    if prefer_strix_optimized and tier_data.strix_optimized is not None:
        return tier_data.strix_optimized
    return tier_data.primary


def resolve_embedding(
    *,
    use_ab: bool = False,
    registry: ModelsRegistry | None = None,
) -> ModelSpec | None:
    """Resolve embedding model. use_ab=True → Qwen3-Embedding-0.6B."""
    if registry is None:
        registry = load_models_registry()
    if registry is None:
        return None
    if use_ab and registry.embedding_ab is not None:
        return registry.embedding_ab
    return registry.embedding_primary


def resolve_reranker(
    *,
    use_ab: bool = False,
    registry: ModelsRegistry | None = None,
) -> ModelSpec | None:
    """Resolve reranker model."""
    if registry is None:
        registry = load_models_registry()
    if registry is None:
        return None
    if use_ab and registry.reranker_ab is not None:
        return registry.reranker_ab
    return registry.reranker_primary


def resolve_vlm(
    *,
    prefer_quality: bool = True,
    registry: ModelsRegistry | None = None,
) -> ModelSpec | None:
    """Resolve VLM model (quality=7B, иначе 3B light)."""
    if registry is None:
        registry = load_models_registry()
    if registry is None:
        return None
    if prefer_quality and registry.vlm_quality is not None:
        return registry.vlm_quality
    return registry.vlm_light


def model_path(spec: ModelSpec, models_dir: str | Path | None = None) -> Path:
    """Return локальный путь файла модели."""
    if models_dir is None:
        models_dir = os.environ.get("AGMIND_MODELS_DIR", "/var/lib/agmind/models")
    return safe_model_target(Path(models_dir), spec.local_filename)


def safe_model_target(models_dir: Path, file_name: str) -> Path:
    """Return a model path constrained to one basename inside models_dir."""
    if not file_name or "\x00" in file_name:
        raise ValueError("model file name is empty or contains NUL")
    if any(ord(char) < 32 or ord(char) == 127 for char in file_name):
        raise ValueError("model file name contains control characters")

    posix = PurePosixPath(file_name)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError("model file must be relative and must not contain '..'")
    if len(posix.parts) != 1:
        raise ValueError("model file must be a basename, not a path")

    base = Path(models_dir).resolve()
    target = (base / posix.name).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("model file escapes models dir") from exc
    return target


def _safe_hf_revision(revision: str | None) -> str:
    """Return a validated, URL-safe HF revision segment (default ``main``).

    A falsy ``revision`` (None or "") means "unpinned" → mutable ``main`` (back-compat).
    A set revision (commit sha / branch / tag) is validated the same defensive way
    repo parts are — reject NUL, ``..`` traversal, and control characters — then
    percent-quoted into a single path segment.
    """
    if not revision:
        return "main"
    if (
        "\x00" in revision
        or "://" in revision
        or ".." in PurePosixPath(revision).parts
        or any(ord(char) < 32 or ord(char) == 127 for char in revision)
        or "/" in revision
    ):
        raise ValueError("HF revision must be a safe commit sha / branch / tag")
    return quote(revision, safe="")


def hf_resolve_url(repo: str, file_name: str, revision: str | None = None) -> str:
    """Return a safe Hugging Face resolve URL for a validated repo and model file.

    When ``revision`` is set, the URL pins ``/resolve/<revision>/`` (immutable
    download); otherwise it falls back to the mutable ``/resolve/main/`` (back-compat).
    """
    safe_model_target(Path("/tmp/agmind-model-name-check"), file_name)
    safe_revision = _safe_hf_revision(revision)
    repo_parts = PurePosixPath(repo).parts
    if (
        not repo
        or "\x00" in repo
        or "://" in repo
        or PurePosixPath(repo).is_absolute()
        or ".." in repo_parts
        or len(repo_parts) not in {1, 2}
        or any(not _HF_REPO_PART_RE.match(part) for part in repo_parts)
    ):
        raise ValueError("HF repo must be a safe Hugging Face repo id")
    safe_repo = "/".join(quote(part, safe="") for part in repo_parts)
    safe_file = quote(PurePosixPath(file_name).name, safe="")
    return f"https://huggingface.co/{safe_repo}/resolve/{safe_revision}/{safe_file}"
