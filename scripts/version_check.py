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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "templates" / "services"
COMPONENTS_DIR = REPO_ROOT / "templates" / "components"
HOLDS_FILE = REPO_ROOT / "templates" / "version_holds.yaml"
DOCKERFILES_DIR = REPO_ROOT / "docker"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CONSTRAINTS_DIR = REPO_ROOT / "constraints"

# ---- semver compare ----
_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]([a-zA-Z0-9.-]+))?")

# Platform / arch / OS variants which сделают tag не сопоставимым с "plain" pin.
# E.g. our pin caddy:2.11.3-alpine vs upstream "latest" 2.11.3-windowsservercore-ltsc2025
# — это разные variants одной semver version, comparison не имеет смысла.
_VARIANT_TOKENS = (
    "windowsservercore",
    "windows-ltsc",
    "windows",
    "nanoserver",
    "amd64",
    "arm64",  # audit: allow arch-tokens-for-filter
    "arm",  # audit: allow arch-tokens-for-filter
    "armv7",  # audit: allow arch-tokens-for-filter
    "ppc64le",
    "s390x",  # audit: allow arch-tokens-for-filter
    "distroless",
    "ubuntu",
    "alpine",
    "trixie",
    "debian",
    "perl",
    "fpm",
    "slim",
    "buster",
    "bookworm",
    "bullseye",
    "scratch",
    "ubi",
    "ubi9",
    "ubi8",
    "centos",
    "fedora",
    "oraclelinux9",
    "oraclelinux8",
    "oracle",
    "unprivileged",
    "boringcrypto",
    "busybox",
    "otel",
    "builder",
    "gpu-nvidia",
    "gpu-amd",
    "cuda",
    "rocm",
    "vulkan",
    "node",
    "hadoop",
    "kafka",
    "spark",
)

# Pre-release / build / dev markers — pin не должен апгрейдиться на это.
_PRERELEASE_TOKENS = (
    "rc",
    "alpha",
    "beta",
    "pre",
    "nightly",
    "dev",
    "snapshot",
    "preview",
    "edge",
    "canary",
    "test",
)

# Tag stripping: SHA-only (40 hex chars), date-only (8 digits), build IDs.
_SHA_TAG_RE = re.compile(r"^[a-f0-9]{40}$")
_DATE_TAG_RE = re.compile(r"^\d{8}(-[a-f0-9]+)?$")
_BUILD_ID_RE = re.compile(r"^b\d+$")


# Numeric build suffix: e.g. "13.1.0-25893932881" — long number after version.
_NUMERIC_BUILD_SUFFIX_RE = re.compile(r"-\d{6,}(?:[-+]|$)")

# Embedded date in tag body: "3.0-20260518-ddd76bcc" — YYYYMMDD anywhere.
_EMBEDDED_DATE_RE = re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")

# Branch-style tag (no semver): e.g. "latest", "master", "main",
# или '4-27-app-deploy' — tag с alpha слова и dash-separated parts.
_BRANCH_TAG_RE = re.compile(r"^[a-z]+(-[a-z]+)*$", re.IGNORECASE)
_BRANCH_NUMERIC_PREFIX_RE = re.compile(r"^\d+-\d+-[a-z]", re.IGNORECASE)


def _is_variant_or_prerelease(tag: str) -> bool:
    """True если tag это OS/arch variant, RC, dev build, SHA, or branch."""
    low = tag.lower()
    for tok in _VARIANT_TOKENS:
        if f"-{tok}" in low or low.endswith(f".{tok}") or low.startswith(f"{tok}-"):
            return True
    for tok in _PRERELEASE_TOKENS:
        if (
            f"-{tok}" in low
            or f".{tok}" in low
            or f"+{tok}" in low
            or low.endswith(f"-{tok}")
            or low.endswith(f".{tok}")
        ):
            return True
    if _SHA_TAG_RE.match(low):
        return True
    if _DATE_TAG_RE.match(low):
        return True
    if _BUILD_ID_RE.match(low):
        return True
    if _NUMERIC_BUILD_SUFFIX_RE.search(low):
        return True
    if _EMBEDDED_DATE_RE.search(low):
        return True
    if _BRANCH_TAG_RE.match(tag) and not tag.startswith("v") and "." not in tag:
        return True
    if _BRANCH_NUMERIC_PREFIX_RE.match(tag):
        return True
    return False


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
    """Returns semver delta status.

    Compares только numeric semver tuple (major, minor, patch). Variant
    suffix (e.g. `-alpine` в pin vs plain `2.11.3` upstream) ignored —
    user intentionally pinned variant, не нужно flag это как outdated.
    """
    c = _parse_semver(current)[:3]
    l = _parse_semver(latest)[:3]
    if c[0] < 0 or l[0] < 0:
        return "unknown"
    if c == l:
        return "up_to_date"
    if c > l:
        return "newer_than_probe"
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
            "newer_than_probe": "↥",
            "hold": "⏸",
            "error": "❌",
        }.get(self.status, "?")


@dataclass(frozen=True)
class DependencyPin:
    """One non-container dependency discovered by the version scanner."""

    name: str
    specifier: str
    source: str
    file: str


@dataclass(frozen=True)
class ComponentPolicy:
    """One component-level version/update policy."""

    name: str
    kind: str
    current: str
    recommended: str
    update_policy: str
    source: str
    file: str
    hold_reason: str = ""


# ---- registry probes ----


def _http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return cast(dict[str, Any], json.loads(resp.read().decode("utf-8")))


def _docker_hub_latest(image: str) -> str | None:
    """e.g. image='infiniflow/ragflow' or 'library/postgres'."""
    if "/" not in image:
        image = f"library/{image}"
    try:
        data = _http_get_json(
            f"https://registry.hub.docker.com/v2/repositories/{image}/tags?page_size=100",
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    results = data.get("results", [])
    tags = [
        str(t.get("name", ""))
        for t in results
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]
    # Filter only semver-looking tags
    semver = [
        t for t in tags if _VERSION_RE.match(t.lstrip("v")) and not _is_variant_or_prerelease(t)
    ]
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
    raw_tags = data.get("tags", []) or []
    tags = [tag for tag in raw_tags if isinstance(tag, str)]
    # Filter semver-looking + drop variants / RC / SHA-only
    semver = [
        t for t in tags if _VERSION_RE.match(t.lstrip("v")) and not _is_variant_or_prerelease(t)
    ]
    if not semver:
        return None
    semver.sort(key=_parse_semver, reverse=True)
    return semver[0]


def _quay_latest(owner: str, image: str) -> str | None:
    """Probe quay.io API.

    Endpoint: https://quay.io/api/v1/repository/<owner>/<image>/tag/?limit=100
    Anonymous read для public repos OK.
    """
    try:
        data = _http_get_json(
            f"https://quay.io/api/v1/repository/{owner}/{image}/tag/?limit=100&onlyActiveTags=true",
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    tags = [
        str(t.get("name", ""))
        for t in data.get("tags", [])
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]
    semver = [
        t for t in tags if _VERSION_RE.match(t.lstrip("v")) and not _is_variant_or_prerelease(t)
    ]
    if not semver:
        return None
    semver.sort(key=_parse_semver, reverse=True)
    return semver[0]


def _gcr_latest(project: str, image: str) -> str | None:
    """Probe gcr.io (Google Container Registry). Anonymous через v2 API.

    Endpoint: https://gcr.io/v2/<project>/<image>/tags/list
    Без bearer token (some public repos требуют — fail silently).
    """
    try:
        data = _http_get_json(
            f"https://gcr.io/v2/{project}/{image}/tags/list",
        )
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    raw_tags = data.get("tags", []) or []
    tags = [tag for tag in raw_tags if isinstance(tag, str)]
    semver = [
        t for t in tags if _VERSION_RE.match(t.lstrip("v")) and not _is_variant_or_prerelease(t)
    ]
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
    tag = data.get("tag_name") or data.get("name")
    return tag if isinstance(tag, str) else None


def probe_latest(image_with_path: str) -> str | None:
    """Dispatch на правильный registry probe basedon image prefix."""
    if image_with_path.startswith("ghcr.io/"):
        parts = image_with_path[len("ghcr.io/") :].split("/", 1)
        if len(parts) == 2:
            return _ghcr_latest(parts[0], parts[1])
        return None
    if image_with_path.startswith("quay.io/"):
        parts = image_with_path[len("quay.io/") :].split("/", 1)
        if len(parts) == 2:
            return _quay_latest(parts[0], parts[1])
        return None
    if image_with_path.startswith("gcr.io/"):
        parts = image_with_path[len("gcr.io/") :].split("/", 1)
        if len(parts) == 2:
            return _gcr_latest(parts[0], parts[1])
        return None
    # Docker Hub fallback: 'foo/bar' или 'bar' (library/bar)
    return _docker_hub_latest(image_with_path)


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


def scan_pyproject_deps(pyproject: Path = PYPROJECT) -> list[DependencyPin]:
    """Scan [project].dependencies and [project.optional-dependencies]."""
    if not pyproject.exists():
        return []
    import tomllib

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    out: list[DependencyPin] = []

    def add_req(req: str) -> None:
        name = re.split(r"[<>=!~;\[]", req, maxsplit=1)[0].strip()
        specifier = req[len(name) :].strip() or "*"
        if not name:
            return
        out.append(
            DependencyPin(
                name=name,
                specifier=specifier,
                source="pyproject",
                file=str(pyproject.relative_to(REPO_ROOT)),
            )
        )

    for req in data.get("project", {}).get("dependencies", []) or []:
        add_req(req)
    optional = data.get("project", {}).get("optional-dependencies", {}) or {}
    for reqs in optional.values():
        for req in reqs:
            add_req(req)
    return out


def scan_ansible_collections(
    path: Path = REPO_ROOT / "ansible" / "requirements.yml",
) -> list[DependencyPin]:
    """Scan ansible/requirements.yml collection pins."""
    if not path.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    out: list[DependencyPin] = []
    for item in data.get("collections", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        specifier = str(item.get("version", "*")).strip() or "*"
        if not name:
            continue
        out.append(
            DependencyPin(
                name=name,
                specifier=specifier,
                source="ansible-galaxy",
                file=str(path.relative_to(REPO_ROOT)),
            )
        )
    return out


_PIP_SPEC_RE = re.compile(r'"?([A-Za-z0-9_.-]+)(==|>=|<=|~=|>|<)([^"\s]+)"?')


def scan_dockerfile_pip_specs(docker_dir: Path = DOCKERFILES_DIR) -> list[DependencyPin]:
    """Scan pip specs embedded in docker/Dockerfile.*."""
    out: list[DependencyPin] = []
    if not docker_dir.exists():
        return out
    for path in sorted(docker_dir.glob("Dockerfile*")):
        text = path.read_text(encoding="utf-8")
        for match in _PIP_SPEC_RE.finditer(text):
            name, op, version = match.groups()
            out.append(
                DependencyPin(
                    name=name,
                    specifier=f"{op}{version}",
                    source="dockerfile-pip",
                    file=str(path.relative_to(REPO_ROOT)),
                )
            )
    return out


def scan_constraint_specs(constraints_dir: Path = CONSTRAINTS_DIR) -> list[DependencyPin]:
    """Scan constraints/*.txt compatibility envelopes."""
    out: list[DependencyPin] = []
    if not constraints_dir.exists():
        return out
    for path in sorted(constraints_dir.glob("*.txt")):
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith(("-c ", "--constraint ")):
                continue
            name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
            specifier = line[len(name) :].strip() or "*"
            if not name:
                continue
            out.append(
                DependencyPin(
                    name=name,
                    specifier=specifier,
                    source=f"constraint:{path.stem}",
                    file=str(path.relative_to(REPO_ROOT)),
                )
            )
    return out


def scan_component_policies(components_dir: Path = COMPONENTS_DIR) -> list[ComponentPolicy]:
    """Scan templates/components/*.yaml for component-level update policies."""
    if not components_dir.exists():
        return []
    try:
        import yaml
    except Exception:
        return []

    out: list[ComponentPolicy] = []
    for path in sorted(components_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        core = data.get("core", {}) if isinstance(data, dict) else {}
        if not isinstance(core, dict):
            core = {}
        name = str(data.get("id", path.stem)).strip()
        if not name:
            continue
        out.append(
            ComponentPolicy(
                name=name,
                kind=str(data.get("kind", "")).strip(),
                current=str(core.get("current_pin") or ""),
                recommended=str(core.get("recommended_version") or ""),
                update_policy=str(core.get("update_policy") or ""),
                source=str(core.get("source") or ""),
                file=str(path.relative_to(REPO_ROOT)),
                hold_reason=str(core.get("hold_reason") or ""),
            )
        )
    return out


# ---- main check loop ----


def build_reports(probe_fn: Callable[[str], str | None] = probe_latest) -> list[PinReport]:
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
                reports.append(
                    PinReport(
                        image=image,
                        current=current_tag,
                        latest=None,
                        source=source,
                        file=file,
                        status="hold",
                        hold_reason=hold.get("reason", "(no reason given)"),
                    )
                )
                continue

            try:
                latest = probe_fn(image)
            except Exception as exc:  # noqa: BLE001
                reports.append(
                    PinReport(
                        image=image,
                        current=current_tag,
                        latest=None,
                        source=source,
                        file=file,
                        status="error",
                        error=str(exc),
                    )
                )
                continue

            if latest is None:
                reports.append(
                    PinReport(
                        image=image,
                        current=current_tag,
                        latest=None,
                        source=source,
                        file=file,
                        status="error",
                        error="probe returned no version",
                    )
                )
                continue

            status = _compare(current_tag, latest)
            reports.append(
                PinReport(
                    image=image,
                    current=current_tag,
                    latest=latest,
                    source=source,
                    file=file,
                    status=status,
                )
            )

    reports.sort(key=lambda r: (r.status, r.image))
    return reports


# ---- markdown rendering ----


def render_markdown(reports: list[PinReport]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d")
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

    lines.extend(
        [
            "",
            "### Legend",
            "",
            "- **✅ up_to_date** — pin совпадает с latest registry tag.",
            "- **📦 patch** — patch-bump доступен (semver Z).",
            "- **🔄 minor** — minor-bump доступен (semver Y).",
            "- **⚠️ major** — major-bump доступен (semver X). Breaking changes — review.",
            "- **↥ newer_than_probe** — committed pin is newer than probed latest; registry probe may be incomplete.",
            "- **⏸ HOLD** — pin намеренно остановлен, см. `templates/version_holds.yaml` → reason.",
            "- **❌ error** — probe failed (network / registry rate-limit / unknown image).",
            "- **strict-pin** — component updates must keep the current explicit pin until reviewed.",
            "- **compatible-patch / compatible-minor** — component can move within the stated compatibility window after verification.",
            "- **manual-hold** — component is intentionally held until a documented compatibility review.",
            "- **pinned-by-parent** — version follows a parent stack such as Dify or RAGFlow.",
            "",
        ]
    )

    component_policies = scan_component_policies()
    if component_policies:
        lines.extend(
            [
                "### Update policies",
                "",
                "- **strict-pin** — keep the exact pin until a component owner reviews and verifies the bump.",
                "- **compatible-patch** — patch updates may be planned automatically but still require local verification.",
                "- **compatible-minor** — minor updates are acceptable after changelog review and component verification.",
                "- **manual-hold** — do not bump without a research note or explicit compatibility evidence.",
                "- **upstream-compatible** — follow upstream's supported compatibility window.",
                "- **patch-auto** — legacy issue-report term for patch updates that can be planned automatically.",
                "- **minor-review** — legacy issue-report term for minor updates that need changelog review.",
                "- **major-hold** — legacy issue-report term for major updates blocked until explicit review.",
                "- **grouped** — update the whole stack together, not a single service in isolation.",
                "- **pinned-by-parent** — version follows a parent stack contract.",
                "- **volatile** — upstream tags are noisy; prefer explicit known-good pins.",
                "",
                "### Component update policies",
                "",
                "| Component | Kind | Current | Recommended | Policy | Source | Note |",
                "|-----------|------|---------|-------------|--------|--------|------|",
            ]
        )
        for policy in sorted(component_policies, key=lambda item: (item.kind, item.name)):
            note = policy.hold_reason or f"`{policy.file}`"
            current = policy.current or "?"
            recommended = policy.recommended or "?"
            source = policy.source or "?"
            lines.append(
                f"| `{policy.name}` | {policy.kind} | {current} | {recommended} | "
                f"`{policy.update_policy}` | {source} | {note} |"
            )
        lines.append("")

    dependency_pins = (
        scan_pyproject_deps()
        + scan_ansible_collections()
        + scan_dockerfile_pip_specs()
        + scan_constraint_specs()
    )
    if dependency_pins:
        lines.extend(
            [
                "### Non-container dependency pins",
                "",
                "| Name | Specifier | Source | File |",
                "|------|-----------|--------|------|",
            ]
        )
        for pin in sorted(dependency_pins, key=lambda item: (item.source, item.name, item.file)):
            lines.append(f"| `{pin.name}` | `{pin.specifier}` | {pin.source} | `{pin.file}` |")
        lines.append("")

    lines.extend(
        [
            "### How to update",
            "",
            "1. Run `agmind upgrade --check` and inspect this report.",
            "2. For a component update, run `agmind upgrade --component <id> --version <target> --plan`.",
            "3. If the plan is acceptable, run `agmind upgrade --component <id> --version <target> --apply`.",
            "4. Run `agmind doctor` and the component verification commands listed in `templates/components/<id>.yaml`.",
            "5. Run `docker compose config --quiet` for the affected rendered profiles.",
            "6. Commit descriptor, contract, and digest changes only after verification.",
            "7. If verification fails, run `agmind upgrade --rollback`.",
            "",
            "### How to bump",
            "",
            "1. Update `image:` или `FROM` tag в `templates/services/*.yaml` / `docker/Dockerfile.*`",
            "2. Update digest: `docker buildx imagetools inspect <image>:<tag>` → copy `sha256:...`",
            "3. Local verify: `agmind doctor` + `pytest -q` + `scripts/audit_forbidden.py`",
            "4. Commit + push; weekly `version-check.yml` подтвердит ✅ в next report.",
            "",
            f"_Generated by `scripts/version_check.py` at {now}._",
        ]
    )
    return "\n".join(lines) + "\n"


# ---- entry point ----


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=None, help="Write markdown into this file")
    ap.add_argument("--json", dest="json_out", type=Path, default=None, help="Write JSON dump")
    ap.add_argument(
        "--offline", action="store_true", help="Skip registry probes (для unit tests / CI dry-run)"
    )
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
