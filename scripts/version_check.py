#!/usr/bin/env python3
"""Phase P: upstream version check — scan AGmind pins + report markdown.

Inspired by legacy AGmind issue #63 (https://github.com/botAGI/AGmind/issues/63).

Что делает:
  1. Сканирует все pinned versions в:
       - templates/services/*.yaml  (image: foo:tag)
       - pyproject.toml             (ansible-core>=X, etc.)
       - docker/Dockerfile.*         (FROM foo:tag)
  2. Опрашивает upstream registry за latest version:
       - Docker Hub (registry.hub.docker.com или registry-1.docker.io v2 API)
       - GHCR (ghcr.io/v2/<owner>/<image>/tags/list с anonymous token)
       - GitHub Releases (gh api /repos/<owner>/<repo>/releases/latest)
  3. Применяет hold annotations из templates/version_holds.yaml
  4. Рендерит markdown table с legend (✅ / 📦 / 🔄 / ⚠️ / ⏸ HOLD)

Usage:
  python3 scripts/version_check.py                # stdout markdown
  python3 scripts/version_check.py --output report.md
  python3 scripts/version_check.py --json out.json # machine-readable

Used by .github/workflows/version-check.yml (weekly cron) — creates/updates
issue с тагом 'upstream-update' в репо.

Out-of-scope: автоматический bump. User видит отчёт и решает.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "templates" / "services"
HOLDS_FILE = REPO_ROOT / "templates" / "version_holds.yaml"
DOCKERFILES_DIR = REPO_ROOT / "docker"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# ---- semver compare ----
_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]([a-zA-Z0-9.-]+))?")


def _parse_semver(s: str) -> tuple[int, int, int, str]:
    m = _VERSION_RE.match(s.strip().lstrip("v"))
    if not m:
        return (-1, 0, 0, s)
    return (
        int(m.group(1)),
        int(m.group(2) or 0),
        int(m.group(3) or 0),
        m.group(4) or "",
    )


def _compare(current: str, latest: str) -> str:
    """Returns one of: 'up_to_date' / 'patch' / 'minor' / 'major' / 'unknown'."""
    c = _parse_semver(current)
    l = _parse_semver(latest)
    if c[:4] == l[:4]:
        return "up_to_date"
    if c[0] != l[0]:
        return "major"
    if c[1] != l[1]:
        return "minor"
    if c[2] != l[2]:
        return "patch"
    return "unknown"


# ---- data model ----


@dataclass
class PinReport:
    image: str  # e.g. "infiniflow/ragflow"
    current: str  # e.g. "v0.25.5"
    latest: str | None
    source: str  # "compose" / "dockerfile" / "pyproject"
    file: str  # relative path
    status: str  # "up_to_date" / "patch" / "minor" / "major" / "hold" / "error"
    hold_reason: str | None = None
    error: str | None = None

    @property
    def glyph(self) -> str:
        return {
            "up_to_date": "✅",
            "patch": "📦",
            "minor": "🔄",
            "major": "⚠️",
            "hold": "⏸",
            "error": "❌",
        }.get(self.status, "?")


# ---- registry probes ----


def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _docker_hub_latest(image: str) -> str | None:
    """e.g. image='infiniflow/ragflow' or 'library/postgres'."""
    if "/" not in image:
        image = f"library/{image}"
    try:
        data = _http_get_json(
            f"https://registry.hub.docker.com/v2/repositories/{image}/tags?page_size=50",
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    tags = [t["name"] for t in data.get("results", [])]
    # Filter only semver-looking tags
    semver = [t for t in tags if _VERSION_RE.match(t.lstrip("v"))]
    if not semver:
        return None
    # Pick highest by parsed semver
    semver.sort(key=_parse_semver, reverse=True)
    return semver[0]


def _ghcr_latest(owner: str, image: str) -> str | None:
    try:
        token_data = _http_get_json(
            f"https://ghcr.io/token?scope=repository:{owner}/{image}:pull",
        )
        token = token_data.get("token", "")
        if not token:
            return None
        data = _http_get_json(
            f"https://ghcr.io/v2/{owner}/{image}/tags/list?n=200",
            headers={"Authorization": f"Bearer {token}"},
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    tags = data.get("tags", []) or []
    # Filter semver-looking
    semver = [t for t in tags if _VERSION_RE.match(t.lstrip("v"))]
    if not semver:
        return None
    semver.sort(key=_parse_semver, reverse=True)
    return semver[0]


def _github_release_latest(owner: str, repo: str) -> str | None:
    try:
        data = _http_get_json(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    return data.get("tag_name") or data.get("name")


def probe_latest(image_with_path: str) -> str | None:
    """Pick registry probe basedon image source."""
    if image_with_path.startswith("ghcr.io/"):
        # ghcr.io/owner/image
        parts = image_with_path[len("ghcr.io/"):].split("/", 1)
        if len(parts) == 2:
            return _ghcr_latest(parts[0], parts[1])
        return None
    if "/" in image_with_path and not image_with_path.count("/") > 1:
        return _docker_hub_latest(image_with_path)
    if "/" not in image_with_path:
        return _docker_hub_latest(image_with_path)
    return None


# ---- holds parser ----


def load_holds() -> dict[str, dict[str, str]]:
    if not HOLDS_FILE.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(HOLDS_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ---- pin scanners ----


_COMPOSE_IMAGE_RE = re.compile(r"^image:\s*([^\s]+):([^\s@]+)(?:@sha256:[a-f0-9]+)?", re.MULTILINE)


def scan_compose_pins(services_dir: Path) -> list[tuple[str, str, str]]:
    """Returns [(image, tag, file), ...]."""
    if not services_dir.exists():
        return []
    out: list[tuple[str, str, str]] = []
    for p in sorted(services_dir.glob("*.yaml")):
        text = p.read_text(encoding="utf-8")
        m = _COMPOSE_IMAGE_RE.search(text)
        if m:
            out.append((m.group(1), m.group(2), str(p.relative_to(REPO_ROOT))))
    return out


_DOCKERFILE_FROM_RE = re.compile(r"^FROM\s+([^\s:]+):([^\s@]+)(?:@sha256:[a-f0-9]+)?", re.MULTILINE)


def scan_dockerfile_pins(docker_dir: Path) -> list[tuple[str, str, str]]:
    if not docker_dir.exists():
        return []
    out: list[tuple[str, str, str]] = []
    for p in sorted(docker_dir.glob("Dockerfile*")):
        text = p.read_text(encoding="utf-8")
        for m in _DOCKERFILE_FROM_RE.finditer(text):
            image, tag = m.group(1), m.group(2)
            out.append((image, tag, str(p.relative_to(REPO_ROOT))))
    return out


# ---- main check loop ----


def build_reports(probe_fn=probe_latest) -> list[PinReport]:
    """Compose + dockerfile pins → status reports."""
    holds = load_holds()
    reports: list[PinReport] = []
    seen: set[tuple[str, str]] = set()

    sources: list[tuple[str, list[tuple[str, str, str]]]] = [
        ("compose", scan_compose_pins(SERVICES_DIR)),
        ("dockerfile", scan_dockerfile_pins(DOCKERFILES_DIR)),
    ]

    for source, pins in sources:
        for image, current_tag, file in pins:
            key = (image, current_tag)
            if key in seen:
                continue
            seen.add(key)

            hold = holds.get(image)
            if hold:
                reports.append(PinReport(
                    image=image, current=current_tag, latest=None,
                    source=source, file=file, status="hold",
                    hold_reason=hold.get("reason", "(no reason given)"),
                ))
                continue

            try:
                latest = probe_fn(image)
            except Exception as exc:  # noqa: BLE001
                reports.append(PinReport(
                    image=image, current=current_tag, latest=None,
                    source=source, file=file, status="error", error=str(exc),
                ))
                continue

            if latest is None:
                reports.append(PinReport(
                    image=image, current=current_tag, latest=None,
                    source=source, file=file, status="error",
                    error="probe returned no version",
                ))
                continue

            status = _compare(current_tag, latest)
            reports.append(PinReport(
                image=image, current=current_tag, latest=latest,
                source=source, file=file, status=status,
            ))

    reports.sort(key=lambda r: (r.status, r.image))
    return reports


# ---- markdown rendering ----


def render_markdown(reports: list[PinReport]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"## Upstream Version Check — {now}",
        "",
        "| Component | Current | Latest | Status | Note |",
        "|-----------|---------|--------|--------|------|",
    ]
    for r in reports:
        latest = r.latest or "?"
        if r.status == "hold":
            status_cell = "⏸ HOLD"
            note = r.hold_reason or ""
        elif r.status == "error":
            status_cell = "❌ error"
            note = r.error or ""
        else:
            status_cell = f"{r.glyph} {r.status}"
            note = f"`{r.file}`"
        lines.append(f"| `{r.image}` | {r.current} | {latest} | {status_cell} | {note} |")

    lines.extend([
        "",
        "### Legend",
        "",
        "- **✅ up_to_date** — pin совпадает с latest registry tag.",
        "- **📦 patch** — patch-bump доступен (semver Z).",
        "- **🔄 minor** — minor-bump доступен (semver Y).",
        "- **⚠️ major** — major-bump доступен (semver X). Breaking changes — review.",
        "- **⏸ HOLD** — pin намеренно остановлен, см. `templates/version_holds.yaml` → reason.",
        "- **❌ error** — probe failed (network / registry rate-limit / unknown image).",
        "",
        "### How to bump",
        "",
        "1. Update `image:` или `FROM` tag в `templates/services/*.yaml` / `docker/Dockerfile.*`",
        "2. Update digest: `docker buildx imagetools inspect <image>:<tag>` → copy `sha256:...`",
        "3. Local verify: `agmind doctor` + `pytest -q` + `scripts/audit_forbidden.py`",
        "4. Commit + push; weekly `version-check.yml` подтвердит ✅ в next report.",
        "",
        f"_Generated by `scripts/version_check.py` at {now}._",
    ])
    return "\n".join(lines) + "\n"


# ---- entry point ----


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=None, help="Write markdown into this file")
    ap.add_argument("--json", dest="json_out", type=Path, default=None, help="Write JSON dump")
    ap.add_argument("--offline", action="store_true",
                    help="Skip registry probes (для unit tests / CI dry-run)")
    args = ap.parse_args()

    if args.offline:
        # Stub probe — все unknown → error.
        reports = build_reports(probe_fn=lambda _img: None)
    else:
        reports = build_reports()

    md = render_markdown(reports)
    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"wrote {args.output} ({len(reports)} entries)", file=sys.stderr)
    else:
        print(md)

    if args.json_out:
        args.json_out.write_text(
            json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"wrote {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
