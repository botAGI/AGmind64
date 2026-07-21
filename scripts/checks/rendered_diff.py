#!/usr/bin/env python3
"""SPEC-16.1: rendered-compose diff gate for all AGmind isolation profiles.

Two subcommands support a PR-time "did the rendered compose change?" gate:

  render --out DIR [--domain D]
      Render every profile in ``all_profile_names()`` (sorted, Traefik disabled
      so the P0.3 authelia topology gate does not fail-close a single profile —
      same posture as the ci.yml compose-validate lane) to ``DIR/<profile>.yml``.
      Deterministic: identical inputs produce byte-identical files.

  diff --base DIR --head DIR
      Emit a MARKDOWN semantic-diff report to stdout comparing two render dirs
      (typically the PR base ref vs the PR head). Per profile it classifies
      added / removed / changed and, for changed profiles, embeds a bounded
      unified diff. When every profile is byte-identical it prints a single
      line. Always exits 0 — this is an informational gate, never a blocker.

The workflow renders the base tree and the head tree with their OWN renderer
(each checkout gets its own editable venv), so a descriptor/renderer change on
the PR shows up here as a concrete YAML delta before merge.

Exit codes:
  render — 0 success, non-zero on render error (surfaced, but the workflow
           tolerates it so the PR is never blocked).
  diff   — always 0 (informational).
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from agmind.services.profile_sets import all_profile_names  # noqa: E402
from agmind.services.renderer import render_to_string  # noqa: E402

# ci.yml's compose-validate lane renders every profile against this placeholder
# domain; mirror it so the base/head renders match that lane byte-for-byte.
DEFAULT_DOMAIN = "ci.example.com"

# Hidden HTML marker the PR-comment step keys off to find & update its single
# sticky comment (kept identical to the workflow's marker).
COMMENT_MARKER = "<!-- rendered-diff -->"

# Per-profile unified-diff cap. A changed profile rarely moves more than a few
# dozen lines; the cap keeps a pathological whole-file rewrite from blowing past
# GitHub's comment size limit. Bounded, not omitted — the count of dropped lines
# is reported so the truncation is visible.
_MAX_DIFF_LINES = 400


def render_all_profiles(out_dir: Path, domain: str = DEFAULT_DOMAIN) -> list[Path]:
    """Render every ``all_profile_names()`` profile to ``out_dir/<profile>.yml``.

    Traefik disabled (label-free, no authelia gate) and profiles rendered in
    sorted order so the run is deterministic. Returns the written paths.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for profile in sorted(all_profile_names()):
        rendered = render_to_string(
            profiles=[profile],
            traefik_enabled=False,
            domain=domain,
        )
        target = out_dir / f"{profile}.yml"
        target.write_text(rendered, encoding="utf-8")
        written.append(target)
    return written


def _bounded_unified_diff(base_text: str, head_text: str, name: str) -> str:
    """Bounded ``difflib.unified_diff`` between two YAMLs, as a string."""
    diff_lines = list(
        difflib.unified_diff(
            base_text.splitlines(),
            head_text.splitlines(),
            fromfile=f"base/{name}.yml",
            tofile=f"head/{name}.yml",
            lineterm="",
        )
    )
    dropped = 0
    if len(diff_lines) > _MAX_DIFF_LINES:
        dropped = len(diff_lines) - _MAX_DIFF_LINES
        diff_lines = diff_lines[:_MAX_DIFF_LINES]
    body = "\n".join(diff_lines)
    if dropped:
        body += f"\n... ({dropped} more diff lines truncated)"
    return body


def build_diff_report(base_dir: Path, head_dir: Path) -> str:
    """Compare two render dirs and return a stable Markdown report.

    Classifies each profile (by the union of ``*.yml`` stems across both dirs)
    as added / removed / changed / unchanged. Changed profiles embed a bounded
    unified diff. Returns a single-line "no changes" message when every shared
    profile is byte-identical and nothing was added or removed.
    """
    base = {p.stem: p for p in sorted(base_dir.glob("*.yml"))}
    head = {p.stem: p for p in sorted(head_dir.glob("*.yml"))}
    profiles = sorted(set(base) | set(head))

    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    for name in profiles:
        in_base = name in base
        in_head = name in head
        if in_base and not in_head:
            removed.append(name)
        elif in_head and not in_base:
            added.append(name)
        else:
            if base[name].read_text(encoding="utf-8") == head[name].read_text(encoding="utf-8"):
                continue
            changed.append(name)

    if not (added or removed or changed):
        return f"No rendered changes across {len(profiles)} profiles."

    lines: list[str] = ["## Rendered compose diff", ""]
    lines.append(
        f"{len(added)} added · {len(removed)} removed · {len(changed)} changed "
        f"(of {len(profiles)} profiles)."
    )

    if added:
        lines.extend(["", f"### Added ({len(added)})"])
        lines.extend(f"- `{name}`" for name in added)
    if removed:
        lines.extend(["", f"### Removed ({len(removed)})"])
        lines.extend(f"- `{name}`" for name in removed)
    if changed:
        lines.extend(["", f"### Changed ({len(changed)})"])
        for name in changed:
            diff_body = _bounded_unified_diff(
                base[name].read_text(encoding="utf-8"),
                head[name].read_text(encoding="utf-8"),
                name,
            )
            lines.extend(["", f"#### `{name}`", "", "```diff", diff_body, "```"])

    return "\n".join(lines)


def _cmd_render(args: argparse.Namespace) -> int:
    written = render_all_profiles(Path(args.out), domain=args.domain)
    print(f"rendered {len(written)} profiles to {args.out}")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    report = build_diff_report(Path(args.base), Path(args.head))
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render every compose profile, or diff two render dirs (SPEC-16.1)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="Render all profiles to a directory.")
    p_render.add_argument("--out", required=True, help="Output directory for <profile>.yml files.")
    p_render.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        help=f"Domain placeholder override (default: {DEFAULT_DOMAIN}).",
    )
    p_render.set_defaults(func=_cmd_render)

    p_diff = sub.add_parser("diff", help="Markdown-diff two render dirs (stdout).")
    p_diff.add_argument("--base", required=True, help="Base render directory.")
    p_diff.add_argument("--head", required=True, help="Head render directory.")
    p_diff.set_defaults(func=_cmd_diff)

    args = parser.parse_args(argv)
    return int(args.func(args))


__all__ = [
    "COMMENT_MARKER",
    "DEFAULT_DOMAIN",
    "build_diff_report",
    "main",
    "render_all_profiles",
]


if __name__ == "__main__":
    raise SystemExit(main())
