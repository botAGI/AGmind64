from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.cli import service_cmd

pytestmark = pytest.mark.backend_any


def test_service_scaffold_writes_descriptor_that_validates(tmp_path: Path) -> None:
    rc = service_cmd.cmd_scaffold("demo-api", "app", services_dir=tmp_path)

    assert rc == 0
    descriptor_path = tmp_path / "demo-api.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
    assert descriptor["image"] != "REPLACE_WITH_IMAGE:latest"
    assert descriptor["ports"] == ["127.0.0.1:8080:8080"]
    assert "localhost:8080" in descriptor["health"]["test"][-1]
    assert service_cmd.cmd_validate("demo-api", services_dir=tmp_path) == 0


def test_service_scaffold_output_has_no_literal_todo(tmp_path: Path) -> None:
    # 5.1: scaffold placeholders должны быть self-documenting (`<purpose>` etc.),
    # NOT the literal "TODO" — иначе `grep -rn TODO agmind/` over production code
    # ловит scaffold OUTPUT text как code-debt.
    rc = service_cmd.cmd_scaffold("demo-api", "app", services_dir=tmp_path)

    assert rc == 0
    rendered = (tmp_path / "demo-api.yaml").read_text(encoding="utf-8")
    assert "TODO" not in rendered
