"""D-05a: compute_diff sees top-level compose keys (networks/volumes/secrets/configs/name).

Structural comparison on the already-parsed dicts — comment/whitespace/key-order churn in the
rendered YAML must never trigger `has_changes`. `raw_unified` stays display-only.
"""

from __future__ import annotations

import pytest

from agmind.deploy.diff import ComposeDiff, compute_diff, format_diff

pytestmark = pytest.mark.backend_any


# ---------- compute_diff: top-level structural comparison ----------


def test_top_level_network_added_flips_has_changes() -> None:
    current = "services:\n  foo:\n    image: foo:1\n"
    new = "services:\n  foo:\n    image: foo:1\nnetworks:\n  agmind:\n    external: true\n"
    diff = compute_diff(current, new)
    assert diff.has_changes
    assert diff.top_level_changed == ["networks"]


def test_top_level_volumes_and_name_changed() -> None:
    current = (
        "name: agmind\nservices:\n  foo:\n    image: foo:1\nvolumes:\n  data:\n    driver: local\n"
    )
    new = (
        "name: agmind-v2\n"
        "services:\n  foo:\n    image: foo:1\n"
        "volumes:\n  data:\n    driver: local\n    driver_opts:\n      type: nfs\n"
    )
    diff = compute_diff(current, new)
    assert diff.has_changes
    assert set(diff.top_level_changed) == {"name", "volumes"}


def test_top_level_secrets_and_configs_detected() -> None:
    current = "services: {}\n"
    new = (
        "services: {}\n"
        "secrets:\n  db_password:\n    external: true\n"
        "configs:\n  nginx_conf:\n    external: true\n"
    )
    diff = compute_diff(current, new)
    assert set(diff.top_level_changed) == {"secrets", "configs"}


def test_cosmetic_only_change_does_not_flip_has_changes() -> None:
    current = "# a comment\nservices:\n  foo:\n    image: foo:1\nnetworks:\n  agmind: {}\n"
    new = (
        "# a DIFFERENT comment, plus trailing whitespace   \n"
        "\n"
        "services:\n"
        "  foo:\n"
        "    image: foo:1\n"
        "\n"
        "networks:\n"
        "  agmind: {}\n"
    )
    diff = compute_diff(current, new)
    assert not diff.has_changes
    assert diff.top_level_changed == []
    # raw_unified is still computed for --verbose display, just not consulted for the decision.
    assert diff.raw_unified != ""


def test_total_changes_counts_top_level_entries() -> None:
    current = "services: {}\n"
    new = "services: {}\nsecrets:\n  db_password:\n    external: true\n"
    diff = compute_diff(current, new)
    assert diff.total_changes == 1
    assert diff.top_level_changed == ["secrets"]


def test_no_top_level_keys_no_change() -> None:
    diff = compute_diff("services: {}\n", "services: {}\n")
    assert diff.top_level_changed == []
    assert not diff.has_changes


# ---------- format_diff: top-level-keys rendering ----------


def test_format_diff_renders_top_level_section() -> None:
    diff = ComposeDiff(top_level_changed=["networks", "volumes"])
    out = format_diff(diff)
    assert "networks" in out
    assert "volumes" in out


def test_format_diff_omits_top_level_section_when_empty() -> None:
    current = "services: {}\n"
    new = "services:\n  foo:\n    image: foo:1\n"
    diff = compute_diff(current, new)
    assert diff.top_level_changed == []
    out = format_diff(diff)
    assert "Top-level" not in out
