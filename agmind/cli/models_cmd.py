"""`agmind models` subcommand — manage GGUF inventory.

См. templates/models.yaml + agmind/models.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
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

        out: dict[str, Any] = {
            "schema_version": reg.schema_version,
            "last_updated": reg.last_updated,
            "llama_cpp": {
                "min_build": reg.llama_cpp_min_build,
                "recommended_build": reg.llama_cpp_recommended_build,
            },
            "tiers": {},
        }
        for tier_name, tier_obj in reg.llm_tiers.items():
            local = model_path(tier_obj.primary, _models_dir())
            out["tiers"][tier_name] = {
                "description": tier_obj.description,
                "primary": {
                    "name": tier_obj.primary.name,
                    "hf_repo": tier_obj.primary.hf_repo,
                    "filename": tier_obj.primary.filename,
                    "size_gb": tier_obj.primary.size_gb,
                    "verification": tier_obj.primary.verification,
                    "url": tier_obj.primary.hf_url,
                    "local_path": str(local),
                    "downloaded": local.exists(),
                },
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(f"AGmind models inventory  ({reg.last_updated})")
    print(
        f"llama.cpp pin: min={reg.llama_cpp_min_build}, recommended={reg.llama_cpp_recommended_build}"
    )
    print()
    for tier_name in ("S", "M", "L", "XL", "XXL"):
        tier = reg.llm_tiers.get(tier_name)
        if tier is None:
            continue
        m = tier.primary
        local = model_path(m, _models_dir())
        present = "✓" if local.exists() else "·"
        print(
            f"  [{present}] {tier_name:3s}  {m.name:35s}  {m.size_gb:6.1f} GB  ({m.verification})"
        )
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
            print(
                f"  [{label}] {local.name} already present ({local.stat().st_size / 1024**3:.2f} GB)"
            )
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
            print(
                f"  [{label}] SIZE_MISMATCH: {local} = {size_gb:.2f} GB (expected ~{expected} GB)"
            )
            issues += 1
        else:
            print(f"  [{label}] OK: {local.name} ({size_gb:.2f} GB)")
    return 0 if issues == 0 else 1


def cmd_info(model_id: str | None = None, file: str | None = None) -> int:
    """Print details для curated model (by id) или local file (by --file path).

    Examples:
        agmind models info qwen36-a3b-q4km        # curated catalog lookup
        agmind models info --file Qwen3.6-A3B.gguf  # local file inspect
    """
    if model_id is not None:
        from agmind.install.models import find_by_id

        entry = find_by_id(model_id)
        if entry is None:
            print(
                f"ERROR: unknown model id {model_id!r}. Use `agmind models list --catalog`.",
                file=sys.stderr,
            )
            return 1
        local = _models_dir() / entry.file
        present = "✓ downloaded" if local.exists() else "· not downloaded"
        print(f"{entry.name}")
        print(f"  ID:           {entry.id}")
        print(f"  HF repo:      {entry.repo}")
        print(f"  File:         {entry.file}")
        print(f"  Size:         {entry.size_gib:.1f} GiB")
        print(
            f"  Params:       {entry.params_b:.1f}B"
            + (
                f" total, {entry.active_params_b:.1f}B active (MoE)"
                if entry.active_params_b is not None
                else ""
            )
        )
        print(f"  Quant:        {entry.quant}")
        print(f"  Ctx:          {entry.suggested_ctx}")
        print(f"  Tested:       {'★ Strix Halo' if entry.strix_tested else '— not tested'}")
        if entry.measured_tg_t_s is not None:
            print(f"  Measured tps: {entry.measured_tg_t_s:.1f} t/s tg")
        print(f"  Description:  {entry.description}")
        print(f"  Local path:   {local}  [{present}]")
        return 0

    if file is not None:
        local = _models_dir() / file
        if not local.exists():
            # Try absolute path
            local = Path(file)
        if not local.exists():
            print(f"ERROR: file not found: {file}", file=sys.stderr)
            return 2
        size_gib = local.stat().st_size / 1024**3
        print(f"{local.name}")
        print(f"  Path:    {local}")
        print(f"  Size:    {size_gib:.2f} GiB ({local.stat().st_size:,} bytes)")
        mtime = local.stat().st_mtime
        from datetime import datetime as _dt

        print(f"  Modified: {_dt.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}")
        return 0

    print("ERROR: pass either <model_id> или --file <name>", file=sys.stderr)
    return 2


def cmd_pull(
    model_id: str | None = None,
    repo: str | None = None,
    file: str | None = None,
    force: bool = False,
) -> int:
    """Download model from curated catalog (by id) или custom HF (--repo/--file).

    Examples:
        agmind models pull qwen36-a3b-q4km          # curated
        agmind models pull --repo X --file Y.gguf   # custom HF
    """
    import shutil
    import subprocess

    if model_id is not None:
        from agmind.install.models import find_by_id

        entry = find_by_id(model_id)
        if entry is None:
            print(f"ERROR: unknown model id {model_id!r}", file=sys.stderr)
            return 1
        repo = entry.repo
        file = entry.file

    if not repo or not file:
        print("ERROR: provide <model_id> или --repo/--file pair", file=sys.stderr)
        return 2

    models_dir = _models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / file
    if target.exists() and not force:
        size_gib = target.stat().st_size / 1024**3
        print(f"✓ already present: {target} ({size_gib:.2f} GiB) — use --force to re-download")
        return 0

    if shutil.which("curl") is None:
        print("ERROR: curl not installed", file=sys.stderr)
        return 1

    url = f"https://huggingface.co/{repo}/resolve/main/{file}"
    print(f"Downloading {url}")
    print(f"  → {target}")
    rc = subprocess.run(
        ["curl", "-fL", "-C", "-", "-o", str(target), "--progress-bar", "--retry", "3", url],
        check=False,
    ).returncode
    if rc != 0:
        print(f"ERROR: curl rc={rc}", file=sys.stderr)
        return 1
    size_gib = target.stat().st_size / 1024**3
    print(f"✓ downloaded {size_gib:.2f} GiB → {target}")
    return 0


def cmd_rm(model_id: str | None = None, file: str | None = None, force: bool = False) -> int:
    """Delete model file. Warn если referenced в running compose."""
    if model_id is not None:
        from agmind.install.models import find_by_id

        entry = find_by_id(model_id)
        if entry is None:
            print(f"ERROR: unknown model id {model_id!r}", file=sys.stderr)
            return 1
        target = _models_dir() / entry.file
    elif file is not None:
        target = _models_dir() / file
        if not target.exists():
            target = Path(file)
    else:
        print("ERROR: pass <model_id> или --file", file=sys.stderr)
        return 2

    if not target.exists():
        print(f"ERROR: file not found: {target}", file=sys.stderr)
        return 2

    # Check если model используется running compose (AGMIND_MODEL_FILE in /opt/agmind/.env)
    env_file = Path("/opt/agmind/.env")
    if env_file.exists():
        try:
            text = env_file.read_text()
            if target.name in text:
                if not force:
                    print(
                        f"WARNING: {target.name} referenced in /opt/agmind/.env.",
                        file=sys.stderr,
                    )
                    print(
                        "  Stop the deployment first (`docker compose down`) или use --force.",
                        file=sys.stderr,
                    )
                    return 1
                print("WARNING: forcing rm despite reference in /opt/agmind/.env")
        except OSError:
            pass

    size_gib = target.stat().st_size / 1024**3
    target.unlink()
    print(f"✓ removed {target} ({size_gib:.2f} GiB freed)")
    return 0


def cmd_list_local(as_json: bool = False) -> int:
    """List local .gguf files в models_dir (без registry tier breakdown)."""
    import json
    from datetime import datetime as _dt

    models_dir = _models_dir()
    if not models_dir.exists():
        print(f"models dir not present: {models_dir}")
        return 0

    files = sorted(p for p in models_dir.iterdir() if p.suffix in (".gguf", ".safetensors", ".bin"))
    if not files:
        print(f"models dir empty: {models_dir}")
        return 0

    if as_json:
        out = [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "size_gib": f.stat().st_size / 1024**3,
                "modified": _dt.fromtimestamp(f.stat().st_mtime).isoformat(),
                "path": str(f),
            }
            for f in files
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    total_gib = sum(f.stat().st_size for f in files) / 1024**3
    print(f"Local models in {models_dir}:")
    print(f"{'SIZE':>10}  {'MODIFIED':<20}  NAME")
    print("-" * 70)
    for f in files:
        size_gib = f.stat().st_size / 1024**3
        mtime = _dt.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {size_gib:>6.2f} GB  {mtime:<20}  {f.name}")
    print("-" * 70)
    print(f"  Total: {len(files)} files, {total_gib:.2f} GiB")
    return 0


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
