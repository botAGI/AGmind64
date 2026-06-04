"""Review HIGH docs-quickstart-install-nonexistent-cmds: operator docs must only show
`agmind` commands that actually exist. QUICKSTART once told operators to run
`agmind models download` / `agmind chat` / `agmind embed` / `agmind rerank` — none of which
are real commands. This parses fenced code blocks in the operator-facing docs and asserts every
`agmind <command> [<subcommand>]` resolves against the live CLI, so the docs can't drift again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]
_DOCS = [
    _REPO / "docs" / "QUICKSTART.md",
    _REPO / "README.md",
    _REPO / "README.ru.md",
]

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
# An actual invocation: a line that (after a `$ ` prompt or a `| ` pipe) STARTS with the
# binary — `agmind <top> [<sub>]` or `python -m agmind <top> [<sub>]`. This avoids matching
# the word "agmind" inside paths / URLs / `cd agmind` that share the fence.
_CMD_RE = re.compile(r"^(?:python\s+-m\s+)?agmind\s+([a-z][\w-]*)(?:\s+([a-z][\w-]*))?")


def _cli_surface() -> tuple[set[str], dict[str, set[str]]]:
    """(top-level command names, {group: subcommand names}) from the live typer app."""
    from agmind.cli import _make_app

    app = _make_app()

    def _name(cmd: object) -> str | None:
        n = getattr(cmd, "name", None)
        if n:
            return str(n)
        cb = getattr(cmd, "callback", None)
        return cb.__name__.replace("_", "-") if cb is not None else None

    tops = {n for c in app.registered_commands if (n := _name(c))}
    groups: dict[str, set[str]] = {}
    for g in app.registered_groups:
        gname = g.name or (g.typer_instance.info.name if g.typer_instance else None)
        if not gname:
            continue
        subs: set[str] = set()
        if g.typer_instance is not None:
            subs = {n for c in g.typer_instance.registered_commands if (n := _name(c))}
            # Include nested sub-GROUP names too (e.g. `ops smoke ...`) so a real nested
            # invocation validates at the first sub level.
            for sg in g.typer_instance.registered_groups:
                sgname = sg.name or (sg.typer_instance.info.name if sg.typer_instance else None)
                if sgname:
                    subs.add(str(sgname))
        groups[str(gname)] = subs
    return tops, groups


def _doc_commands(path: Path) -> list[tuple[str, str | None]]:
    text = path.read_text(encoding="utf-8")
    out: list[tuple[str, str | None]] = []
    for block in _FENCE_RE.findall(text):
        for raw in block.splitlines():
            line = raw.strip().lstrip("$").strip()
            if "|" in line:  # piped: the binary is in the segment after the last pipe
                line = line.split("|")[-1].strip()
            match = _CMD_RE.match(line)
            if match:
                out.append((match.group(1), match.group(2) or None))
    return out


def test_docs_only_reference_real_agmind_commands() -> None:
    tops, groups = _cli_surface()
    invalid: list[str] = []
    for doc in _DOCS:
        if not doc.exists():
            continue
        for top, sub in _doc_commands(doc):
            if top not in tops and top not in groups:
                invalid.append(f"{doc.name}: `agmind {top}` is not a command")
                continue
            # For a GROUP, the next token is the subcommand (skip flags / bare `agmind <group>`).
            if top in groups and groups[top] and sub and not sub.startswith("-"):
                if sub not in groups[top]:
                    invalid.append(
                        f"{doc.name}: `agmind {top} {sub}` — '{sub}' is not a {top} subcommand "
                        f"(have: {sorted(groups[top])})"
                    )
    assert not invalid, "docs reference non-existent agmind commands:\n" + "\n".join(invalid)


def test_quickstart_does_not_resurrect_removed_commands() -> None:
    """Belt-and-suspenders against the exact phantom commands the HIGH finding flagged — checked
    against parsed fenced commands, not prose (a sentence may legitimately name them)."""
    cmds = {(top, sub) for top, sub in _doc_commands(_REPO / "docs" / "QUICKSTART.md")}
    phantoms = {("models", "download"), ("chat", None), ("embed", None), ("rerank", None)}
    assert not (cmds & phantoms), f"QUICKSTART resurrected phantom commands: {cmds & phantoms}"
