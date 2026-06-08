"""Task H.6 (3): missing-digest governance gate — fail-closed, mutation-verified.

The digest-pins check returns FAIL (non-zero + error_count > 0) when any
deploy-facing descriptor lacks a `digest:` field.  All 44 current descriptors
are deploy-facing.  This gate was previously absent; it now surfaces as a
governance ERROR (not WARN) — preventing mutable-tag images from entering deploy.

Mutation-verified RED proof:
- Strip traefik's digest in-test → digest_check reports 1 unpinned → FAIL.
- Restore → digest_check reports 0 unpinned → PASS.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_DIR = _REPO_ROOT / "templates" / "services"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_descriptor_digests(services_dir: Path = _SERVICES_DIR) -> dict[str, str | None]:
    """Load {service_name: digest_or_None} from all descriptor YAMLs.

    Build-services (compose ``build:``) are built on-host from shipped source, carry no
    registry digest, and are exempt from the pin gate — mirror ``digest_check.py`` and
    skip them so they do not register as ``unpinned`` (agent-pydanticai / agent-agno).
    """
    import yaml

    result: dict[str, str | None] = {}
    for path in sorted(services_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("build") is not None:
            continue
        name = data.get("name", path.stem)
        result[name] = data.get("digest")
    return result


# ---------------------------------------------------------------------------
# Static assertions on the committed descriptor catalog
# ---------------------------------------------------------------------------


def test_all_deploy_facing_descriptors_have_digest() -> None:
    """Every descriptor in templates/services/ must have a non-empty digest field."""
    digests = _load_descriptor_digests()
    unpinned = [name for name, d in digests.items() if not d]
    assert not unpinned, (
        f"Deploy-facing descriptor(s) missing digest: {sorted(unpinned)}\n"
        "Run: docker buildx imagetools inspect <image>:<tag> | grep '^Digest:'\n"
        "Then add 'digest: <bare-64hex>' to the descriptor YAML."
    )


def test_previously_unpinned_services_now_pinned() -> None:
    """Specific services that were unpinned before H.6 are now pinned."""
    digests = _load_descriptor_digests()
    # These were the 5 unpinned services identified before Task 3.
    for service in ("authelia", "llama-embed", "llama-llm", "llama-rerank", "traefik"):
        assert service in digests, f"descriptor {service} not found"
        assert digests[service], f"{service} still missing digest pin"


# ---------------------------------------------------------------------------
# Gate function: digest_check.check_digest_pins
# ---------------------------------------------------------------------------


def test_digest_check_passes_on_pinned_catalog() -> None:
    """digest_check.check_digest_pins() returns no issues on the current catalog."""
    from scripts.checks.digest_check import check_digest_pins

    issues, service_count = check_digest_pins()
    assert service_count == 46, f"expected 46 descriptors, got {service_count}"
    assert issues == [], (
        f"digest check found {len(issues)} unpinned descriptor(s): {[i['service'] for i in issues]}"
    )


# ---------------------------------------------------------------------------
# Mutation-verified RED proof (using a temp services dir copy)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_services_dir(tmp_path: Path) -> Iterator[Path]:
    """Copy templates/services/ to a temp dir for mutation tests."""
    dest = tmp_path / "services"
    shutil.copytree(_SERVICES_DIR, dest)
    yield dest


def _strip_digest_from_yaml(path: Path) -> str:
    """Remove the 'digest:' line from a descriptor YAML; return original text."""
    original = path.read_text(encoding="utf-8")
    lines = [line for line in original.splitlines(keepends=True) if not line.startswith("digest:")]
    path.write_text("".join(lines), encoding="utf-8")
    return original


def test_mutation_verified_red_when_digest_stripped(tmp_services_dir: Path) -> None:
    """RED proof: strip traefik's digest → gate reports FAIL.

    This mutation-verifies that the gate is actually fail-closed and is not a
    vacuous assertion.
    """
    from scripts.checks.digest_check import check_digest_pins

    traefik_path = tmp_services_dir / "traefik.yaml"
    assert traefik_path.exists(), "traefik.yaml not found in tmp copy"

    original_text = _strip_digest_from_yaml(traefik_path)

    # Gate must FAIL when traefik has no digest.
    issues, service_count = check_digest_pins(services_dir=tmp_services_dir)
    assert len(issues) == 1, (
        f"Expected exactly 1 issue after stripping traefik's digest, got {len(issues)}: "
        f"{[i['service'] for i in issues]}"
    )
    assert issues[0]["service"] == "traefik", (
        f"Expected issue on 'traefik', got: {issues[0]['service']}"
    )

    # Restore.
    traefik_path.write_text(original_text, encoding="utf-8")

    # Gate must PASS after restore.
    issues_after, _ = check_digest_pins(services_dir=tmp_services_dir)
    assert issues_after == [], (
        f"Gate still reports failures after restoring traefik digest: "
        f"{[i['service'] for i in issues_after]}"
    )


def test_mutation_digest_check_main_returns_nonzero_on_unpinned(
    tmp_services_dir: Path,
) -> None:
    """digest_check.main() returns non-zero when a descriptor is unpinned."""
    from scripts.checks.digest_check import main as digest_main

    traefik_path = tmp_services_dir / "traefik.yaml"
    original_text = _strip_digest_from_yaml(traefik_path)

    # Pass tmp_services_dir directly via the keyword argument exposed for testing.
    rc = digest_main((), services_dir=tmp_services_dir)
    assert rc != 0, "digest_check.main() must return non-zero when a descriptor is unpinned"

    # Restore.
    traefik_path.write_text(original_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Governance integration: digest-pins check is registered
# ---------------------------------------------------------------------------


def test_governance_includes_digest_pins_check() -> None:
    """The aggregate governance must include the digest-pins check."""
    from agmind.governance import DEFAULT_CHECKS

    assert "digest-pins" in DEFAULT_CHECKS, (
        "digest-pins must be in agmind.governance.DEFAULT_CHECKS so it runs in "
        "aggregate governance and CI governance-validate job."
    )


def test_governance_digest_check_passes() -> None:
    """Aggregate governance digest-pins check must return ok=True on current catalog."""
    from agmind.governance import run_governance_checks

    report = run_governance_checks(checks=("digest-pins",), structured=True)
    assert report.ok, (
        "aggregate governance digest-pins check FAILED — some descriptors are unpinned."
    )


# ---------------------------------------------------------------------------
# WR-03: render_cmd must NOT emit a fake sha256:000…0 for un-pinned services
# ---------------------------------------------------------------------------


def test_render_catalog_refuses_fake_zero_digest_for_unpinned_service(
    tmp_services_dir: Path,
) -> None:
    """WR-03 RED proof: render catalog must fail/omit rather than emit sha256:000…0.

    Strip traefik's digest from the descriptor → cmd_render_catalog must either:
      (a) return non-zero (fail-closed error path), OR
      (b) omit the digest key entirely from the service entry (schema then
          rejects it as missing required field).

    It must NOT emit `digest: sha256:<64 zeros>` which masquerades as a real pin
    and passes catalog JSON schema validation while `ref` points at a mutable tag.

    Mutation-verify contract:
      - Reverting the fix (restoring the `f"sha256:{'0' * 64}"` line) makes
        this test fail: rc==0 and the entry carries all-zero digest.
    """
    from agmind.cli.render_cmd import cmd_render_catalog

    traefik_path = tmp_services_dir / "traefik.yaml"
    assert traefik_path.exists(), "traefik.yaml not found in tmp copy"

    # Strip traefik's digest.
    original_text = _strip_digest_from_yaml(traefik_path)

    # Attempt to render.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_render_catalog(version="0.0.0-test", services_dir=tmp_services_dir)

    # Either: non-zero exit (hard fail), or zero exit but no all-zeros digest.
    if rc == 0:
        import json

        output = buf.getvalue()
        catalog = json.loads(output)
        traefik_entry = catalog.get("services", {}).get("traefik", {})
        fake_digest = "sha256:" + "0" * 64
        assert traefik_entry.get("digest") != fake_digest, (
            f"WR-03: render catalog emitted fake zero-digest for unpinned 'traefik': "
            f"{traefik_entry.get('digest')!r}.\n"
            "render catalog must either fail with rc!=0 or omit the digest key entirely "
            "for un-pinned services — never synthesize a zero-hash that passes schema "
            "validation while ref points at a mutable tag."
        )
        # If digest key is present and not zero, it should be absent (no key) for
        # an un-pinned service.  rc==0 + no fake digest is acceptable only if the
        # key is omitted (caller can detect missing digest as a gap).
        assert "digest" not in traefik_entry, (
            f"WR-03: render catalog should omit 'digest' for un-pinned 'traefik', "
            f"but emitted: {traefik_entry.get('digest')!r}."
        )
    else:
        # rc != 0: fail-closed — acceptable.
        pass

    # Restore.
    traefik_path.write_text(original_text, encoding="utf-8")


def test_render_catalog_zero_digest_is_not_emitted_on_unpinned(
    tmp_services_dir: Path,
) -> None:
    """WR-03: the zero-sentinel digest sha256:000…0 must never appear in service entries.

    Independently verifies that the fake-pin constant is not emitted for services,
    regardless of the rc path.  All-real-catalog path must pass (no service is
    actually unpinned today).
    """
    import io
    from contextlib import redirect_stdout

    from agmind.cli.render_cmd import cmd_render_catalog

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_render_catalog(version="0.0.0-test", services_dir=tmp_services_dir)

    assert rc == 0, f"render catalog failed on fully-pinned catalog: rc={rc}"

    import json

    catalog = json.loads(buf.getvalue())
    fake_digest = "sha256:" + "0" * 64
    fakes = [
        name
        for name, entry in catalog.get("services", {}).items()
        if entry.get("digest") == fake_digest
    ]
    assert not fakes, (
        f"WR-03: render catalog emitted fake zero-digest for services: {fakes}.\n"
        "No service should carry sha256:000…0 — that is a sentinel, not a real pin."
    )
