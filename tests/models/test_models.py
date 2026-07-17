"""Tests для agmind.models — GGUF tier resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.models import (
    CuratedModelEntry,
    ModelSpec,
    ModelsRegistry,
    _curated_model_from_dict,
    detect_tier,
    hf_resolve_url,
    load_curated_model_entries,
    load_models_registry,
    model_path,
    resolve_embedding,
    resolve_llm,
    resolve_reranker,
    resolve_vlm,
    safe_model_target,
)

pytestmark = pytest.mark.backend_any


# ---- ModelSpec dataclass ----


def test_modelspec_minimal() -> None:
    s = ModelSpec(
        name="test",
        hf_repo="org/repo",
        filename="model.gguf",
        quant="Q4_K_M",
        size_gb=4.5,
    )
    assert s.hf_url == "https://huggingface.co/org/repo/resolve/main/model.gguf"
    assert s.local_filename == "model.gguf"


def test_modelspec_url_format() -> None:
    s = ModelSpec(
        name="x",
        hf_repo="unsloth/Qwen3.6-35B-A3B-GGUF",
        filename="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        quant="UD-Q4_K_XL",
        size_gb=22.4,
    )
    assert "huggingface.co" in s.hf_url
    assert s.hf_repo in s.hf_url
    assert s.filename in s.hf_url


# ---- load_models_registry ----


def test_load_models_registry_real_file() -> None:
    reg = load_models_registry()
    assert reg is not None, "templates/models.yaml must load"
    assert isinstance(reg, ModelsRegistry)
    assert reg.schema_version == 1
    assert reg.llama_cpp_min_build  # non-empty


def test_load_models_registry_all_tiers_present() -> None:
    reg = load_models_registry()
    assert reg is not None
    assert set(reg.llm_tiers.keys()) == {"S", "M", "L", "XL", "XXL"}


def test_load_models_registry_each_tier_has_primary() -> None:
    reg = load_models_registry()
    assert reg is not None
    for tier_name, tier in reg.llm_tiers.items():
        assert tier.primary is not None
        assert tier.primary.hf_repo, f"{tier_name} primary missing hf_repo"
        assert tier.primary.filename, f"{tier_name} primary missing filename"
        assert tier.primary.size_gb > 0


def test_load_models_registry_llama_cpp_version_pins() -> None:
    """min/recommended builds должны быть pinned (per R-llm-models)."""
    reg = load_models_registry()
    assert reg is not None
    assert reg.llama_cpp_min_build.startswith("b")
    assert reg.llama_cpp_recommended_build.startswith("b")


def test_load_models_registry_embed_present() -> None:
    reg = load_models_registry()
    assert reg is not None
    assert reg.embedding_primary.hf_repo
    assert "bge-m3" in reg.embedding_primary.name.lower()


def test_load_models_registry_rerank_present() -> None:
    reg = load_models_registry()
    assert reg is not None
    assert reg.reranker_primary.hf_repo
    assert "reranker" in reg.reranker_primary.name.lower()


def test_load_models_registry_antipatterns_loaded() -> None:
    reg = load_models_registry()
    assert reg is not None
    assert len(reg.antipatterns) > 0
    # Известные antipatterns должны быть зафиксированы
    ids = [ap.get("id") for ap in reg.antipatterns]
    assert "AMDVLK_ICD" in ids
    assert "MXFP4_DENSE" in ids


# ---- detect_tier ----


def test_detect_tier_small() -> None:
    assert detect_tier(ram_gib=12) == "S"
    assert detect_tier(ram_gib=16) == "S"
    assert detect_tier(ram_gib=20) == "S"


def test_detect_tier_medium() -> None:
    assert detect_tier(ram_gib=28) == "M"
    assert detect_tier(ram_gib=32) == "M"


def test_detect_tier_large() -> None:
    assert detect_tier(ram_gib=64) == "L"


def test_detect_tier_xl() -> None:
    assert detect_tier(ram_gib=128) == "XL"
    assert detect_tier(ram_gib=125) == "XL"


def test_detect_tier_xxl() -> None:
    assert detect_tier(ram_gib=192) == "XXL"


def test_detect_tier_real_host() -> None:
    """На текущей машине должен быть какой-то tier."""
    tier = detect_tier()
    assert tier in ("S", "M", "L", "XL", "XXL")


# ---- resolve_llm ----


def test_resolve_llm_defaults() -> None:
    """Default — primary без strix_optimized."""
    llm = resolve_llm("L", prefer_strix_optimized=False)
    assert llm is not None
    assert llm.role == "primary"


def test_resolve_llm_strix_optimized_for_l() -> None:
    """L tier имеет 0xSero Strix variant."""
    llm = resolve_llm("L", prefer_strix_optimized=True)
    assert llm is not None
    assert "strix" in llm.name.lower() or "dynamic" in llm.quant.lower()


def test_resolve_llm_coding_override() -> None:
    """coding=True предпочитает coding model если есть."""
    llm = resolve_llm("L", coding=True)
    if llm is not None:
        assert "coder" in llm.name.lower() or llm.role == "fallback_coding"


def test_resolve_llm_auto_tier() -> None:
    """tier=None → auto-detect."""
    llm = resolve_llm()
    assert llm is not None


def test_resolve_llm_invalid_tier() -> None:
    """Invalid tier → None (не raise)."""
    llm = resolve_llm("Z")  # type: ignore[arg-type]
    assert llm is None


# ---- resolve_embedding / rerank / vlm ----


def test_resolve_embedding_primary() -> None:
    emb = resolve_embedding()
    assert emb is not None
    assert "bge-m3" in emb.name.lower()


def test_resolve_embedding_ab() -> None:
    emb = resolve_embedding(use_ab=True)
    assert emb is not None
    assert "qwen3" in emb.name.lower() or emb.role == "ab_candidate"


def test_resolve_reranker_primary() -> None:
    rr = resolve_reranker()
    assert rr is not None
    assert "reranker" in rr.name.lower()


def test_resolve_vlm_quality_default() -> None:
    vlm = resolve_vlm(prefer_quality=True)
    if vlm is not None:
        # Quality variant — 7B
        assert "7b" in vlm.name.lower() or vlm.role == "quality"


def test_resolve_vlm_light() -> None:
    vlm = resolve_vlm(prefer_quality=False)
    if vlm is not None:
        assert "3b" in vlm.name.lower() or vlm.role == "light"


# ---- model_path ----


def test_model_path_with_dir() -> None:
    spec = ModelSpec(name="x", hf_repo="o/r", filename="model.gguf", quant="Q4", size_gb=1.0)
    p = model_path(spec, models_dir="/tmp/test-models")
    assert str(p) == "/tmp/test-models/model.gguf"


def test_model_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_MODELS_DIR", "/custom/dir")
    spec = ModelSpec(name="x", hf_repo="o/r", filename="m.gguf", quant="Q4", size_gb=1.0)
    p = model_path(spec)
    assert str(p) == "/custom/dir/m.gguf"


def test_model_path_default() -> None:
    spec = ModelSpec(name="x", hf_repo="o/r", filename="m.gguf", quant="Q4", size_gb=1.0)
    # No env, no arg — default /var/lib/agmind/models
    import os

    os.environ.pop("AGMIND_MODELS_DIR", None)
    p = model_path(spec)
    assert "/var/lib/agmind/models" in str(p)


@pytest.mark.parametrize(
    "file_name",
    [
        "../escape.gguf",
        "nested/model.gguf",
        "/tmp/model.gguf",
        "",
        "bad\x00model.gguf",
        "bad\nmodel.gguf",
        "bad\x1fmodel.gguf",
    ],
)
def test_safe_model_target_rejects_path_escape(tmp_path: Path, file_name: str) -> None:
    with pytest.raises(ValueError, match="model file"):
        safe_model_target(tmp_path / "models", file_name)


def test_safe_model_target_accepts_basename(tmp_path: Path) -> None:
    target = safe_model_target(tmp_path / "models", "model.gguf")

    assert target == (tmp_path / "models" / "model.gguf").resolve()


def test_hf_resolve_url_rejects_unsafe_repo() -> None:
    with pytest.raises(ValueError, match="HF repo"):
        hf_resolve_url("https://evil.example/repo", "model.gguf")


def test_hf_resolve_url_quotes_model_file() -> None:
    url = hf_resolve_url("org/repo", "model name.gguf")

    assert url == "https://huggingface.co/org/repo/resolve/main/model%20name.gguf"


# ---- G.5: revision-aware resolver (optional pinning mechanism) ----


def test_hf_resolve_url_unset_revision_uses_main() -> None:
    """Back-compat: no revision → /resolve/main/ (existing behavior)."""
    url = hf_resolve_url("org/repo", "model.gguf")
    assert url == "https://huggingface.co/org/repo/resolve/main/model.gguf"


def test_hf_resolve_url_with_revision_pins_commit() -> None:
    """When revision is set → /resolve/<revision>/<file> (pinned download)."""
    url = hf_resolve_url("org/repo", "model.gguf", revision="abc123")
    assert "/resolve/abc123/" in url
    assert url == "https://huggingface.co/org/repo/resolve/abc123/model.gguf"


def test_hf_resolve_url_with_empty_revision_uses_main() -> None:
    """Empty-string revision is treated as unset (back-compat default)."""
    url = hf_resolve_url("org/repo", "model.gguf", revision="")
    assert url == "https://huggingface.co/org/repo/resolve/main/model.gguf"


@pytest.mark.parametrize(
    "bad_revision",
    [
        "bad\x00rev",
        "..",
        "../escape",
        "rev\nwith-newline",
        "rev\x1fctrl",
    ],
)
def test_hf_resolve_url_rejects_unsafe_revision(bad_revision: str) -> None:
    """Revision is validated the same defensive way repo parts are (NUL/../control)."""
    with pytest.raises(ValueError, match="revision"):
        hf_resolve_url("org/repo", "model.gguf", revision=bad_revision)


# ---- G.5: optional revision/sha256 on ModelSpec schema ----


def test_modelspec_revision_sha256_default_empty() -> None:
    """Fields are optional; entries without them load with empty-string defaults."""
    s = ModelSpec(
        name="x",
        hf_repo="org/repo",
        filename="model.gguf",
        quant="Q4",
        size_gb=1.0,
    )
    assert s.revision == ""
    assert s.sha256 == ""
    # Unpinned spec falls back to /resolve/main/ (back-compat).
    assert s.hf_url == "https://huggingface.co/org/repo/resolve/main/model.gguf"


def test_modelspec_hf_url_threads_revision() -> None:
    """When revision is set on the spec, hf_url pins the resolve path."""
    s = ModelSpec(
        name="x",
        hf_repo="org/repo",
        filename="model.gguf",
        quant="Q4",
        size_gb=1.0,
        revision="deadbeef",
        sha256="00ff",
    )
    assert s.hf_url == "https://huggingface.co/org/repo/resolve/deadbeef/model.gguf"


def test_model_from_dict_without_pinning_fields_defaults_empty() -> None:
    """A dict lacking revision/sha256 builds a spec with empty-string defaults."""
    from agmind.models import _model_from_dict

    spec = _model_from_dict(
        "",
        {"name": "m", "hf_repo": "org/repo", "filename": "m.gguf", "quant": "Q4", "size_gb": 2.0},
    )
    assert spec.revision == ""
    assert spec.sha256 == ""


def test_model_from_dict_with_pinning_fields_populated_and_not_in_extras() -> None:
    """revision/sha256 are read into the spec and NOT swept into extras."""
    from agmind.models import _model_from_dict

    spec = _model_from_dict(
        "",
        {
            "name": "m",
            "hf_repo": "org/repo",
            "filename": "m.gguf",
            "quant": "Q4",
            "size_gb": 2.0,
            "revision": "abc123",
            "sha256": "a" * 64,
        },
    )
    assert spec.revision == "abc123"
    assert spec.sha256 == "a" * 64
    assert "revision" not in spec.extras
    assert "sha256" not in spec.extras


# ---- verification field consistency ----


def test_all_tier_primaries_have_verification() -> None:
    reg = load_models_registry()
    assert reg is not None
    valid = {"verified-strix", "verified-llamacpp", "inferred", "unverified"}
    for tier_name, tier in reg.llm_tiers.items():
        assert tier.primary.verification in valid, (
            f"{tier_name} primary verification='{tier.primary.verification}'"
        )


def test_no_latest_in_filenames() -> None:
    """Filenames не должны содержать ':latest' или 'latest'."""
    reg = load_models_registry()
    assert reg is not None
    for tier_name, tier in reg.llm_tiers.items():
        assert ":latest" not in tier.primary.filename
        # 'latest' слово допустимо в model names (Latent etc.), но не в pin
        assert "-latest" not in tier.primary.filename.lower()


def test_xl_primary_is_verified_strix() -> None:
    """XL primary должен быть verified на real Strix Halo (per R-llm-models)."""
    reg = load_models_registry()
    assert reg is not None
    assert reg.llm_tiers["XL"].primary.verification == "verified-strix"


# ---- D-02: CuratedModelEntry.revision + .sha256 (wizard supply-chain provenance) ----


def test_curated_model_entry_default_revision_sha256_empty() -> None:
    """Back-compat: entries without revision/sha256 still construct (frozen defaults)."""
    entry = CuratedModelEntry(
        id="x",
        name="X",
        repo="org/repo",
        file="model.gguf",
        size_gib=1.0,
        params_b=1.0,
        active_params_b=None,
        quant="Q4",
        suggested_ctx=4096,
        description="test",
    )
    assert entry.revision == ""
    assert entry.sha256 == ""


def test_curated_model_from_dict_with_revision_sha256_parses() -> None:
    """A YAML dict WITH revision/sha256 parses them onto CuratedModelEntry."""
    entry = _curated_model_from_dict(
        {
            "id": "x",
            "name": "X",
            "repo": "org/repo",
            "file": "model.gguf",
            "size_gib": 1.0,
            "params_b": 1.0,
            "quant": "Q4",
            "suggested_ctx": 4096,
            "description": "test",
            "revision": "abc123def",
            "sha256": "a" * 64,
        }
    )
    assert entry.revision == "abc123def"
    assert entry.sha256 == "a" * 64


def test_curated_model_from_dict_without_revision_sha256_defaults_empty() -> None:
    """A YAML dict WITHOUT revision/sha256 still loads (no crash, empty defaults)."""
    entry = _curated_model_from_dict(
        {
            "id": "x",
            "name": "X",
            "repo": "org/repo",
            "file": "model.gguf",
            "size_gib": 1.0,
            "params_b": 1.0,
            "quant": "Q4",
            "suggested_ctx": 4096,
            "description": "test",
        }
    )
    assert entry.revision == ""
    assert entry.sha256 == ""


def test_wizard_catalog_no_stale_qwen_repo() -> None:
    """No wizard entry may reference the stale 307-redirecting repo id."""
    entries = load_curated_model_entries()
    stale = {e.id: e.repo for e in entries if e.repo == "0xSero/Qwen3.6-35B-A3B-GGUF-Strix"}
    assert stale == {}, f"stale repo still referenced by: {stale}"
    qwen_entries = [e for e in entries if e.id.startswith("qwen36-a3b-")]
    assert len(qwen_entries) == 3
    for e in qwen_entries:
        assert e.repo == "0xSero/Qwen3.6-35B-GGUF"


def test_wizard_catalog_sha256_is_64_hex_when_present() -> None:
    """Every filled sha256 must be exactly 64 lowercase-hex chars (no truncation/typo)."""
    entries = load_curated_model_entries()
    hex_chars = set("0123456789abcdef")
    for e in entries:
        if e.sha256:
            assert len(e.sha256) == 64, f"{e.id}: sha256 length {len(e.sha256)} != 64"
            assert set(e.sha256) <= hex_chars, f"{e.id}: sha256 has non-hex chars"


def test_wizard_catalog_entries_carry_real_revision() -> None:
    """The 7 load-bearing wizard entries carry a fetched HF commit revision (D-02)."""
    entries = load_curated_model_entries()
    assert len(entries) == 7
    for e in entries:
        assert e.revision, f"{e.id}: revision must be filled (fetched HF commit sha)"
        assert len(e.revision) == 40, f"{e.id}: revision should be a 40-hex commit sha"
