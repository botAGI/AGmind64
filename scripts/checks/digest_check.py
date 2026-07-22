#!/usr/bin/env python3
"""Fail-closed governance gate: every deploy-facing descriptor must have a digest pin.

A descriptor is "deploy-facing" when it appears in a real deploy profile (any
profile listed in its descriptor's `profiles` field). The count follows the
live catalog; descriptors that carry `build:` (on-host builds) are exempt from
the digest requirement, registry-backed ones are not.

Disposition: FAIL (non-zero exit + error_count > 0 in JSON) when any
deploy-facing descriptor is missing a `digest:` field.  This was previously a
WARNING in the version-check report; it is now promoted to FAIL to enforce the
supply-chain invariant (Правила Карпатого item #8: pin by digest).

Verification upgrade path (per version_check.py :795):
    docker buildx imagetools inspect <image>:<tag>
    → copy the top-level Digest sha256:<hex> (bare hex, no 'sha256:' prefix)
    → add `digest: <bare-hex>` to the descriptor YAML

Usage:
    python3 scripts/checks/digest_check.py
    python3 scripts/checks/digest_check.py --json
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _issue(service_name: str, image: str) -> dict[str, str]:
    return {
        "severity": "error",
        "kind": "missing_digest",
        "service": service_name,
        "image": image,
        "message": (
            f"Service '{service_name}' ({image}) has no 'digest:' pin. "
            "Run: docker buildx imagetools inspect {image} → copy top-level Digest → "
            "add 'digest: <bare-64hex>' to the descriptor YAML."
        ),
        "remediation": (
            f"docker buildx imagetools inspect {image} | grep '^Digest:' | "
            "awk '{print $2}' | sed 's/sha256://'"
        ),
    }


def check_digest_pins(
    services_dir: Path | None = None,
) -> tuple[list[dict[str, str]], int, int]:
    """Return (issues, service_count, exempt_build_count).

    issues is empty when all registry-backed deploy-facing descriptors carry a
    digest; exempt_build_count is the number of on-host `build:` descriptors.
    """
    if services_dir is None:
        services_dir = REPO_ROOT / "templates" / "services"

    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors(services_dir)
    issues: list[dict[str, str]] = []
    exempt_build_count = 0
    for name, desc in descriptors.items():
        # build-services (compose `build:`) are built on-host from shipped source, not pulled
        # from a registry — they carry no registry digest and are exempt from the digest pin.
        if desc.build is not None:
            exempt_build_count += 1
            continue
        # A descriptor is digest-pinned two schema-blessed ways (service.py _check_image /
        # _check_single_digest_source): the `digest:` field, OR an inline `image: repo:tag@sha256:
        # <hex>` with no field. The field-only check false-failed `make audit` on the inline form
        # (#23) — a schema-valid, genuinely pinned descriptor. Accept either.
        if not desc.digest and "@sha256:" not in desc.image:
            issues.append(_issue(name, desc.image))
    return issues, len(descriptors), exempt_build_count


def _payload(
    *,
    ok: bool,
    service_count: int,
    pinned_count: int,
    exempt_build_count: int,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "service_count": service_count,
        # pinned_count keeps its historical meaning (service_count - unpinned) for
        # downstream compat; the registry/exempt split is in the two keys below.
        "pinned_count": pinned_count,
        "registry_backed_count": service_count - exempt_build_count,
        "exempt_build_count": exempt_build_count,
        "unpinned_count": len(issues),
        "error_count": len(issues),
        "issues": issues,
    }


def main(argv: Sequence[str] | None = None, *, services_dir: Path | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    issues, service_count, exempt_build_count = check_digest_pins(services_dir)
    pinned_count = service_count - len(issues)
    registry_total = service_count - exempt_build_count
    registry_pinned = registry_total - len(issues)
    ok = len(issues) == 0

    if as_json:
        print(
            json.dumps(
                _payload(
                    ok=ok,
                    service_count=service_count,
                    pinned_count=pinned_count,
                    exempt_build_count=exempt_build_count,
                    issues=issues,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if ok else 1

    if ok:
        print(
            f"digest-pins OK: {registry_pinned} registry-backed pinned, "
            f"{exempt_build_count} local-build exempt ({service_count} total)"
        )
    else:
        for issue in issues:
            print(f"FAIL: {issue['message']}", file=sys.stderr)
        print(
            f"digest-pins FAILED: {len(issues)} unpinned registry-backed descriptor(s) "
            f"({registry_pinned}/{registry_total} pinned, {exempt_build_count} local-build exempt)",
            file=sys.stderr,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
