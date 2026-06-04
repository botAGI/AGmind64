#!/usr/bin/env python3
"""Emit the pinned-image bundle manifest for an offline / air-gap install.

The image list is generated from the live descriptor registry so it never rots
when a digest changes (a hardcoded list in the doc would). Feed it to
``docker save`` on an internet-connected host, transfer the tar, then
``docker load`` on the air-gap host. See docs/installation/offline-install.md.

Usage (module path differs by install mode — a pip/wheel install exposes this under the
``agmind`` namespace via package-dir, a source checkout under the top-level ``scripts``):
    python -m agmind.scripts.bundle_manifest            # wheel / pip install
    python -m scripts.bundle_manifest                   # source checkout (pip install -e .)
    python -m scripts.bundle_manifest --profile core,rag
    python -m scripts.bundle_manifest --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence


def build_manifest(profiles: Sequence[str] | None = None) -> dict[str, list[str]]:
    """Return ``{"images": [<image@sha256:...>, ...]}`` for the selection.

    ``profiles`` (e.g. ``["core", "rag"]``) scopes the image set; omitted = the
    full catalog. Unknown profile names raise ``ValueError`` (via the enum).
    """
    from agmind.services.registry import Service, list_services, services_for_profile

    services: list[Service]
    if profiles:
        chosen: dict[str, Service] = {}
        for profile in profiles:
            for svc in services_for_profile(profile):
                chosen[svc.name] = svc
        services = list(chosen.values())
    else:
        services = list_services()

    images = sorted({svc.fq_image() for svc in services})
    return {"images": images}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="",
        help="Comma-separated profile names to scope the image set (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    profiles = [p.strip() for p in args.profile.split(",") if p.strip()] or None
    try:
        manifest = build_manifest(profiles)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        for ref in manifest["images"]:
            print(ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
