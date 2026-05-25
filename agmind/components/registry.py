"""Registry helpers for component contract files."""

from __future__ import annotations

from pathlib import Path

from agmind.components.contracts import ComponentContract

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPONENTS_DIR = REPO_ROOT / "templates" / "components"


def load_component_contracts(
    root: Path = DEFAULT_COMPONENTS_DIR,
) -> dict[str, ComponentContract]:
    """Load all component contracts under ``root`` and return them sorted by id."""
    if not root.exists():
        return {}

    contracts = [
        ComponentContract.from_yaml(path)
        for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")])
    ]
    loaded: dict[str, ComponentContract] = {}
    for contract in sorted(contracts, key=lambda item: item.id):
        if contract.id in loaded:
            raise ValueError(f"duplicate component contract id '{contract.id}'")
        loaded[contract.id] = contract
    return loaded
