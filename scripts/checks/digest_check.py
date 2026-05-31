#!/usr/bin/env python3
"""Fail-closed governance gate: every deploy-facing descriptor must have a digest pin.

A descriptor is "deploy-facing" when it appears in a real deploy profile (any
profile listed in its descriptor's `profiles` field, since all 42 AGmind
descriptors are deploy-facing — there are no build-locally-only descriptors).

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
) -> tuple[list[dict[str, str]], int]:
    """Return (issues, service_count).

    issues is empty when all deploy-facing descriptors carry a digest.
    """
    if services_dir is None:
        services_dir = REPO_ROOT / "templates" / "services"

    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors(services_dir)
    issues: list[dict[str, str]] = []
    for name, desc in descriptors.items():
        if not desc.digest:
            issues.append(_issue(name, desc.image))
    return issues, len(descriptors)


def _payload(
    *,
    ok: bool,
    service_count: int,
    pinned_count: int,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "service_count": service_count,
        "pinned_count": pinned_count,
        "unpinned_count": len(issues),
        "error_count": len(issues),
        "issues": issues,
    }


def main(argv: Sequence[str] | None = None, *, services_dir: Path | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    issues, service_count = check_digest_pins(services_dir)
    pinned_count = service_count - len(issues)
    ok = len(issues) == 0

    if as_json:
        print(
            json.dumps(
                _payload(
                    ok=ok,
                    service_count=service_count,
                    pinned_count=pinned_count,
                    issues=issues,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if ok else 1

    if ok:
        print(f"digest-pins OK: all {pinned_count} deploy-facing descriptors pinned")
    else:
        for issue in issues:
            print(f"FAIL: {issue['message']}", file=sys.stderr)
        print(
            f"digest-pins FAILED: {len(issues)} unpinned descriptor(s) "
            f"({pinned_count}/{service_count} pinned)",
            file=sys.stderr,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
