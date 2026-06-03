#!/usr/bin/env python3
"""Landmines gate — scan the RENDERED compose for mechanically-checkable Правила.

The descriptor-level gates (digest_check, the no-unguarded-interp test) check the
inputs; this checks the renderer's actual OUTPUT, where a regression would
actually bite. Pure-Python, renders in-process (no Docker). Canonical landmine
table lives here; tests/lint/LANDMINES.md mirrors it for humans (a test guards
the two against drift).

Severity: critical → exit 1 (supply-chain / data-loss / blank-interpolation);
warning → reported but exit 0 (best-practice).

Usage:
    python3 scripts/checks/landmines_check.py
    python3 scripts/checks/landmines_check.py --json
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Canonical landmine table: id → (severity, Правило, description).
LANDMINES: dict[str, tuple[str, str, str]] = {
    "L01": ("critical", "#8", "no :latest tag in any rendered image"),
    "L02": ("critical", "#8", "every rendered image is digest-pinned (@sha256:)"),
    "L03": ("critical", "#5", "every volume is a host bind mount (no anonymous/named volume)"),
    "L04": ("critical", "#10/#11", "no bare ${VAR} (unguarded) left in the rendered output"),
    "L05": ("warning", "#log", "every service caps its log size (logging.options.max-size)"),
}

_BARE_VAR_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
_LATEST_RE = re.compile(r":latest(?:$|@|\s)")


@dataclass(frozen=True)
class LandmineHit:
    landmine: str
    severity: str
    target: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "landmine": self.landmine,
            "severity": self.severity,
            "target": self.target,
            "detail": self.detail,
        }


def _sev(landmine: str) -> str:
    return LANDMINES[landmine][0]


def scan_rendered(text: str) -> list[LandmineHit]:
    """Return landmine hits for a rendered docker-compose YAML string."""
    hits: list[LandmineHit] = []

    # L04 is a raw-text check (catches ${VAR} anywhere, incl. command args).
    for match in _BARE_VAR_RE.findall(text):
        hits.append(LandmineHit("L04", _sev("L04"), match, "unguarded interpolation"))

    doc = yaml.safe_load(text) or {}
    services = doc.get("services") or {}
    if not isinstance(services, dict):
        return hits

    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        image = str(svc.get("image", ""))
        if image and _LATEST_RE.search(image):
            hits.append(LandmineHit("L01", _sev("L01"), str(name), f"image '{image}' uses :latest"))
        if image and "@sha256:" not in image:
            hits.append(
                LandmineHit("L02", _sev("L02"), str(name), f"image '{image}' not digest-pinned")
            )

        for vol in svc.get("volumes") or []:
            if isinstance(vol, str):
                source = vol.split(":", 1)[0]
                if not source.startswith("/"):
                    hits.append(
                        LandmineHit("L03", _sev("L03"), str(name), f"non-bind volume '{vol}'")
                    )
            elif isinstance(vol, dict) and vol.get("type") == "volume":
                hits.append(LandmineHit("L03", _sev("L03"), str(name), "named/anonymous volume"))

        max_size = ((svc.get("logging") or {}).get("options") or {}).get("max-size")
        if not max_size:
            hits.append(LandmineHit("L05", _sev("L05"), str(name), "no logging max-size cap"))

    return hits


def check_landmines(services_dir: Path | None = None) -> list[LandmineHit]:
    """Render the full profile and scan it. ``services_dir`` overrides the catalog dir."""
    from agmind.services.renderer import DEFAULT_SERVICES_DIR, render_to_string

    rendered = render_to_string(
        profiles=["full"],
        domain="landmines.example.com",
        services_dir=services_dir or DEFAULT_SERVICES_DIR,
    )
    return scan_rendered(rendered)


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    hits = check_landmines()
    critical = [h for h in hits if h.severity == "critical"]
    warnings = [h for h in hits if h.severity != "critical"]
    ok = not critical

    if as_json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "critical_count": len(critical),
                    "warning_count": len(warnings),
                    "hits": [h.to_dict() for h in hits],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if ok else 1

    if not hits:
        print(f"landmines OK: rendered catalog clean ({len(LANDMINES)} landmines checked)")
        return 0
    for h in hits:
        stream = sys.stderr if h.severity == "critical" else sys.stdout
        print(f"{h.severity.upper()} {h.landmine} {h.target}: {h.detail}", file=stream)
    if critical:
        print(f"landmines FAILED: {len(critical)} critical hit(s)", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
