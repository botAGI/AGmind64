"""`agmind models` subcommand — manage GGUF inventory.

См. templates/models.yaml + agmind/models.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.request import urlretrieve

from agmind.log import logger

log = logger(__name__)


def _models_dir() -> Path:
    return Path(os.environ.get("AGMIND_MODELS_DIR", "/var/lib/agmind/models"))


def cmd_list(as_json: bool = False) -> int:
    """List all models per tier."""
    from agmind.models import load_models_registry, model_path

    reg = load_models_registry()
    if reg is None:
        print("ERROR: templates/models.yaml not found", file=sys.stderr)
        return 1

    if as_json:
        import json
        out = {
            "schema_version": reg.schema_version,
            "last_updated": reg.last_updated,
            "llama_cpp": {
                "min_build": reg.llama_cpp_min_build,
                "recommended_build": reg.llama_cpp_recommended_build,
            },
            "tiers": {},
        }
        for tier_name, tier in reg.llm_tiers.items():
            local = model_path(tier.primary, _models_dir())
            out["tiers"][tier_name] = {
                "description": tier.description,
                "primary": {
                    "name": tier.primary.name,
                    "hf_repo": tier.primary.hf_repo,
                    "filename": tier.primary.filename,
                    "size_gb": tier.primary.size_gb,
                    "verification": tier.primary.verification,
                    "url": tier.primary.hf_url,
                    "local_path": str(local),
                    "downloaded": local.exists(),
                },
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(f"AGmind models inventory  ({reg.last_updated})")
    print(f"llama.cpp pin: min={reg.llama_cpp_min_build}, recommended={reg.llama_cpp_recommended_build}")
    print()
    for tier_name in ("S", "M", "L", "XL", "XXL"):
        tier = reg.llm_tiers.get(tier_name)
        if tier is None:
            continue
        m = tier.primary
        local = model_path(m, _models_dir())
        present = "✓" if local.exists() else "·"
        print(f"  [{present}] {tier_name:3s}  {m.name:35s}  {m.size_gb:6.1f} GB  ({m.verification})")
    print()
    print(f"Embed:   {reg.embedding_primary.name} ({reg.embedding_primary.size_gb:.2f} GB)")
    print(f"Rerank:  {reg.reranker_primary.name} ({reg.reranker_primary.size_gb:.2f} GB)")
    if reg.vlm_quality:
        print(f"VLM:     {reg.vlm_quality.name} ({reg.vlm_quality.size_gb:.2f} GB) + mmproj")
    print()
    print(f"Models dir: {_models_dir()}")
    return 0


def cmd_download(
    tier: str | None = None,
    *,
    embed: bool = False,
    rerank: bool = False,
    vlm: bool = False,
    quality: bool = True,
    force: bool = False,
) -> int:
    """Download GGUF model для указанного tier (или embed/rerank/vlm).

    Если ни одного флага — download primary LLM для auto-detected tier.
    """
    from agmind.models import (
        detect_tier,
        load_models_registry,
        model_path,
        resolve_embedding,
        resolve_llm,
        resolve_reranker,
        resolve_vlm,
    )

    reg = load_models_registry()
    if reg is None:
        print("ERROR: templates/models.yaml not found", file=sys.stderr)
        return 1

    targets = []
    if embed:
        targets.append(("embed", resolve_embedding(registry=reg)))
    if rerank:
        targets.append(("rerank", resolve_reranker(registry=reg)))
    if vlm:
        targets.append(("vlm", resolve_vlm(prefer_quality=quality, registry=reg)))
    if not targets:
        # Default: LLM для tier
        if tier is None:
            tier = detect_tier()
        llm = resolve_llm(tier, registry=reg)  # type: ignore[arg-type]
        if llm is None:
            print(f"ERROR: no LLM defined for tier {tier!r}", file=sys.stderr)
            return 1
        targets.append((f"llm-{tier}", llm))

    models_dir = _models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)

    for label, spec in targets:
        if spec is None:
            print(f"  [{label}] no spec — skipping", file=sys.stderr)
            continue
        local = model_path(spec, models_dir)
        if local.exists() and not force:
            print(f"  [{label}] {local.name} already present ({local.stat().st_size / 1024**3:.2f} GB)")
            continue
        print(f"  [{label}] downloading {spec.hf_url}  ({spec.size_gb} GB) → {local}")
        try:
            urlretrieve(spec.hf_url, str(local))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"  [{label}] OK")

    return 0


def cmd_verify(tier: str | None = None) -> int:
    """Verify locally downloaded models (size match, file exists)."""
    from agmind.models import (
        detect_tier,
        load_models_registry,
        model_path,
        resolve_embedding,
        resolve_llm,
        resolve_reranker,
    )

    reg = load_models_registry()
    if reg is None:
        return 1

    if tier is None:
        tier = detect_tier()
    llm = resolve_llm(tier, registry=reg)  # type: ignore[arg-type]
    embed = resolve_embedding(registry=reg)
    rerank = resolve_reranker(registry=reg)

    issues = 0
    for label, spec in (("LLM", llm), ("Embed", embed), ("Rerank", rerank)):
        if spec is None:
            continue
        local = model_path(spec, _models_dir())
        if not local.exists():
            print(f"  [{label}] MISSING: {local}")
            issues += 1
            continue
        size_gb = local.stat().st_size / 1024**3
        expected = spec.size_gb
        # Tolerance ±10%
        if expected > 0 and abs(size_gb - expected) / expected > 0.1:
            print(f"  [{label}] SIZE_MISMATCH: {local} = {size_gb:.2f} GB (expected ~{expected} GB)")
            issues += 1
        else:
            print(f"  [{label}] OK: {local.name} ({size_gb:.2f} GB)")
    return 0 if issues == 0 else 1


def cmd_path(name: str, tier: str | None = None) -> int:
    """Print local path для named model (e.g. 'embed', 'rerank', 'llm')."""
    from agmind.models import (
        detect_tier,
        load_models_registry,
        model_path,
        resolve_embedding,
        resolve_llm,
        resolve_reranker,
        resolve_vlm,
    )

    reg = load_models_registry()
    if reg is None:
        return 1

    name = name.lower()
    if name == "llm":
        if tier is None:
            tier = detect_tier()
        spec = resolve_llm(tier, registry=reg)  # type: ignore[arg-type]
    elif name == "embed":
        spec = resolve_embedding(registry=reg)
    elif name == "rerank":
        spec = resolve_reranker(registry=reg)
    elif name == "vlm":
        spec = resolve_vlm(registry=reg)
    else:
        print(f"ERROR: unknown model name {name!r}. Use llm/embed/rerank/vlm.", file=sys.stderr)
        return 1
    if spec is None:
        print(f"ERROR: no {name} spec", file=sys.stderr)
        return 1
    print(model_path(spec, _models_dir()))
    return 0
