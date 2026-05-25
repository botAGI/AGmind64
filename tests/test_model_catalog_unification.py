"""Tests that templates/models.yaml is the canonical wizard model catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_YAML = REPO_ROOT / "templates" / "models.yaml"


def test_models_yaml_declares_wizard_catalog_defaults() -> None:
    data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))

    wizard = data["wizard_catalog"]

    assert wizard["defaults"] == {
        "llm": "qwen36-a3b-q4km",
        "embed": "bge-m3-q8",
        "rerank": "bge-reranker-v2-m3-q8",
    }
    entry_ids = {entry["id"] for entry in wizard["entries"]}
    assert {"qwen36-a3b-q4km", "qwen36-a3b-q4_0", "qwen36-a3b-dyn"} <= entry_ids
    assert "bge-m3-q8" in entry_ids
    assert "bge-reranker-v2-m3-q8" in entry_ids


def test_install_catalog_is_loaded_from_models_yaml() -> None:
    from agmind.install import models as install_models
    from agmind.models import load_curated_model_entries

    assert install_models.CURATED_MODELS == load_curated_model_entries(MODELS_YAML)


def test_yaml_backed_defaults_resolve_to_curated_entries() -> None:
    from agmind.install.models import default_model_id, find_by_id
    from agmind.models import load_model_catalog_defaults

    defaults = load_model_catalog_defaults(MODELS_YAML)

    assert defaults["llm"] == default_model_id("llm")
    assert defaults["embed"] == default_model_id("embed")
    assert defaults["rerank"] == default_model_id("rerank")
    assert find_by_id(default_model_id("llm")) is not None
    assert find_by_id(default_model_id("embed")) is not None
    assert find_by_id(default_model_id("rerank")) is not None
