"""Compute compose diff (Phase L.B) — что изменится между current и rendered.

Used by `agmind deploy --diff` чтобы показать пользователю preview ДО apply.
Это закрывает user pain "не знаю что произойдёт при deploy".
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServiceChange:
    """One service change in compose."""

    name: str
    kind: str
    """`added` | `removed` | `image_changed` | `config_changed`"""
    detail: str = ""


@dataclass(frozen=True)
class ComposeDiff:
    """Diff result between two compose configs."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    image_changed: list[ServiceChange] = field(default_factory=list)
    config_changed: list[ServiceChange] = field(default_factory=list)
    top_level_changed: list[str] = field(default_factory=list)
    """Top-level compose keys (networks/volumes/secrets/configs/name) that differ (D-05a)."""
    raw_unified: str = ""
    """Full unified text diff для debugging."""

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.image_changed
            or self.config_changed
            or self.top_level_changed
        )

    @property
    def total_changes(self) -> int:
        return (
            len(self.added)
            + len(self.removed)
            + len(self.image_changed)
            + len(self.config_changed)
            + len(self.top_level_changed)
        )


_TOP_LEVEL_KEYS = ("networks", "volumes", "secrets", "configs", "name")
"""D-05a: non-`services` keys compared structurally by compute_diff."""


def _parse_compose(yaml_text: str) -> dict[str, object]:
    """Parse compose YAML once; non-dict/empty content -> {}."""
    import yaml

    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        return {}
    return data


def _extract_services(data: dict[str, object]) -> dict[str, dict[str, object]]:
    """Pull `services:` out of an already-parsed compose mapping."""
    services = data.get("services", {})
    if not isinstance(services, dict):
        return {}
    return services


def compute_diff(current_text: str, new_text: str) -> ComposeDiff:
    """Compare two rendered compose YAMLs, return structured diff.

    Args:
        current_text: текущий /opt/agmind/docker-compose.yml content (или empty string)
        new_text: только что rendered compose
    """
    current_data = _parse_compose(current_text)
    new_data = _parse_compose(new_text)

    current_services = _extract_services(current_data)
    new_services = _extract_services(new_data)

    current_names = set(current_services.keys())
    new_names = set(new_services.keys())

    added = sorted(new_names - current_names)
    removed = sorted(current_names - new_names)

    image_changed: list[ServiceChange] = []
    config_changed: list[ServiceChange] = []

    for name in sorted(current_names & new_names):
        cur = current_services[name]
        new = new_services[name]
        if not isinstance(cur, dict) or not isinstance(new, dict):
            continue
        cur_img = cur.get("image", "")
        new_img = new.get("image", "")
        if cur_img != new_img:
            image_changed.append(
                ServiceChange(
                    name=name,
                    kind="image_changed",
                    detail=f"{cur_img} → {new_img}",
                )
            )
            continue

        # config change check (любой другой ключ отличается)
        if cur != new:
            config_changed.append(ServiceChange(name=name, kind="config_changed", detail=""))

    # Top-level keys (D-05a): structural (parsed-dict) equality, NOT a text/difflib
    # comparison — comment/whitespace/key-order-only churn in the rendered YAML must
    # never flip has_changes.
    top_level_changed = [
        key for key in _TOP_LEVEL_KEYS if current_data.get(key) != new_data.get(key)
    ]

    # Raw unified для verbose mode — display-only, never consulted for has_changes.
    raw_unified = "".join(
        difflib.unified_diff(
            current_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="current",
            tofile="rendered",
            lineterm="",
        )
    )

    return ComposeDiff(
        added=added,
        removed=removed,
        image_changed=image_changed,
        config_changed=config_changed,
        top_level_changed=top_level_changed,
        raw_unified=raw_unified,
    )


def format_diff(diff: ComposeDiff, verbose: bool = False) -> str:
    """Format diff для human-friendly output на CLI."""
    if not diff.has_changes:
        return "✓ no changes — rendered compose matches current\n"

    lines: list[str] = []
    lines.append(f"📋 {diff.total_changes} change(s):\n")

    if diff.added:
        lines.append(f"\n  ➕ Added ({len(diff.added)}):")
        for name in diff.added:
            lines.append(f"     + {name}")

    if diff.removed:
        lines.append(f"\n  ➖ Removed ({len(diff.removed)}):")
        for name in diff.removed:
            lines.append(f"     - {name}  ⚠️  containers + connected resources будут уничтожены")

    if diff.image_changed:
        lines.append(f"\n  🔄 Image updated ({len(diff.image_changed)}):")
        for change in diff.image_changed:
            lines.append(f"     ~ {change.name}: {change.detail}")

    if diff.config_changed:
        lines.append(f"\n  ⚙️  Config changed ({len(diff.config_changed)}):")
        for change in diff.config_changed:
            lines.append(f"     ~ {change.name}")

    if verbose and diff.raw_unified:
        lines.append("\n--- Full unified diff ---")
        lines.append(diff.raw_unified)

    lines.append("")  # trailing newline
    return "\n".join(lines)


def compute_diff_from_files(
    current_file: Path,
    new_text: str,
) -> ComposeDiff:
    """Convenience: load current from disk + diff."""
    current_text = current_file.read_text(encoding="utf-8") if current_file.exists() else ""
    return compute_diff(current_text, new_text)
