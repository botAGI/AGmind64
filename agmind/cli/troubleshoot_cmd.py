"""`agmind troubleshoot <topic>` — print a docs/TROUBLESHOOTING.md section offline.

Surfaces the existing symptom→fix cookbook next to `agmind doctor`, no browser
needed. The doc is located via ``data_root()`` so it works in both an editable
checkout and a wheel install (docs/ is bundled as ``agmind.docs`` package-data).

Contract (mirrors the parent): bare → topic list (exit 0); known/alias/unique
substring → section (exit 0); ambiguous → candidates (exit 1); unknown → stderr
(exit 1).
"""

from __future__ import annotations

import re
import sys

import typer

from agmind.core.paths import data_root

_SECTION_PREFIX = re.compile(r"^Section\s+\d+:\s*", re.IGNORECASE)
_NON_SLUG = re.compile(r"[^a-z0-9]+")

# Stable shortcuts → slugs (heading text has no stable IDs, so substring + these
# aliases insulate callers from heading rewording). Mirrors the parent's topics.
_ALIASES: dict[str, str] = {
    "gpu": "vulkan-gpu-detection",
    "vulkan": "vulkan-gpu-detection",
    "rocm": "rocm-hip",
    "hip": "rocm-hip",
    "gtt": "gtt-memory",
    "memory": "gtt-memory",
    "oom": "gtt-memory",
    "docker": "docker-compose",
    "compose": "docker-compose",
    "llm": "llm-inference",
    "inference": "llm-inference",
    "model": "llm-inference",
    "mdns": "network-mdns",
    "network": "network-mdns",
    "dns": "network-mdns",
    "logs": "logs-observability",
    "rollback": "emergency-rollback",
    "update": "emergency-rollback",
    "help": "get-help",
}


def _slugify(title: str) -> str:
    """'Section 3: GTT memory' → 'gtt-memory'."""
    stripped = _SECTION_PREFIX.sub("", title).strip().lower()
    return _NON_SLUG.sub("-", stripped).strip("-")


def parse_topics(text: str) -> dict[str, tuple[str, str]]:
    """Split markdown on '## ' headings → {slug: (heading, body)}.

    Content before the first h2 (title + intro) is ignored.
    """
    topics: dict[str, tuple[str, str]] = {}
    slug: str | None = None
    title = ""
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if slug is not None:
                topics[slug] = (title, "\n".join(buf).strip("\n"))
            title = line[3:].strip()
            slug = _slugify(title)
            buf = []
        elif slug is not None:
            buf.append(line)
    if slug is not None:
        topics[slug] = (title, "\n".join(buf).strip("\n"))
    return topics


def resolve_topic(
    query: str, topics: dict[str, tuple[str, str]], aliases: dict[str, str]
) -> list[str]:
    """Return matching slugs: [] unknown, [one] resolved, [many] ambiguous."""
    q = query.strip().lower()
    if q in topics:
        return [q]
    alias = aliases.get(q)
    if alias and alias in topics:
        return [alias]
    return sorted(s for s in topics if q in s)


def register(app: typer.Typer) -> None:
    """Attach the ``troubleshoot`` command to ``app``."""

    @app.command()
    def troubleshoot(
        topic: str = typer.Argument(None, help="Topic slug, alias, or substring (omit to list)"),
    ) -> None:
        """Print a TROUBLESHOOTING.md section (or list topics if none given)."""
        doc = data_root() / "docs" / "TROUBLESHOOTING.md"
        if not doc.is_file():
            print(f"ERROR: troubleshooting guide not found at {doc}", file=sys.stderr)
            raise typer.Exit(code=2)

        topics = parse_topics(doc.read_text(encoding="utf-8"))
        if topic is None:
            typer.echo("Topics (agmind troubleshoot <topic>):")
            for slug in topics:
                typer.echo(f"  {slug}")
            return

        matches = resolve_topic(topic, topics, _ALIASES)
        if len(matches) == 1:
            title, body = topics[matches[0]]
            typer.echo(f"## {title}\n")
            typer.echo(body)
            return
        if len(matches) > 1:
            print(
                f"Ambiguous topic '{topic}'. Candidates: {', '.join(matches)}",
                file=sys.stderr,
            )
            raise typer.Exit(code=1)
        print(
            f"Unknown topic '{topic}'. Run `agmind troubleshoot` to list topics.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
