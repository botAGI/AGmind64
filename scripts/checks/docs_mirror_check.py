#!/usr/bin/env python3
"""Guard README.md and README.ru.md against structural/command drift."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHELL_LANGS = {"bash", "sh", "shell", "console"}


@dataclass(frozen=True)
class CodeBlock:
    index: int
    language: str
    lines: tuple[str, ...]


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _heading_topology(lines: list[str]) -> list[int]:
    # Only REAL markdown headings — a `# comment` inside a ``` code fence (e.g. a bash
    # comment) is not a heading and must not inflate the topology / EN-RU parity count.
    topology: list[int] = []
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            topology.append(len(line) - len(line.lstrip("#")))
    return topology


def _parse_code_blocks(lines: list[str]) -> tuple[list[CodeBlock], list[str]]:
    blocks: list[CodeBlock] = []
    errors: list[str] = []
    in_block = False
    block_language = ""
    block_lines: list[str] = []
    block_start = 0

    for lineno, line in enumerate(lines, start=1):
        if not line.startswith("```"):
            if in_block:
                block_lines.append(line.rstrip())
            continue

        if in_block:
            blocks.append(
                CodeBlock(
                    index=len(blocks) + 1,
                    language=block_language,
                    lines=tuple(block_lines),
                )
            )
            in_block = False
            block_language = ""
            block_lines = []
            block_start = 0
            continue

        in_block = True
        block_language = line[3:].strip().split(maxsplit=1)[0].lower()
        block_lines = []
        block_start = lineno

    if in_block:
        errors.append(f"unclosed code block starting at line {block_start}")
    return blocks, errors


def _normalize_block(block: CodeBlock) -> list[str]:
    if block.language in _SHELL_LANGS:
        return [
            line.rstrip()
            for line in block.lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
    return [line.rstrip() for line in block.lines if line.strip()]


def _diff(left: list[str], right: list[str], left_name: str, right_name: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            left,
            right,
            fromfile=left_name,
            tofile=right_name,
            lineterm="",
        )
    )


def check_docs(english: Path, russian: Path) -> dict[str, Any]:
    issues: list[str] = []
    english_lines = _read(english)
    russian_lines = _read(russian)

    english_headings = _heading_topology(english_lines)
    russian_headings = _heading_topology(russian_lines)
    if english_headings != russian_headings:
        issues.append(
            "heading topology differs: "
            f"{english} levels={english_headings}; {russian} levels={russian_headings}"
        )

    english_blocks, english_errors = _parse_code_blocks(english_lines)
    russian_blocks, russian_errors = _parse_code_blocks(russian_lines)
    issues.extend(f"{english}: {error}" for error in english_errors)
    issues.extend(f"{russian}: {error}" for error in russian_errors)

    if len(english_blocks) != len(russian_blocks):
        issues.append(
            "code block count differs: "
            f"{english} has {len(english_blocks)}; {russian} has {len(russian_blocks)}"
        )

    for left, right in zip(english_blocks, russian_blocks, strict=False):
        if left.language != right.language:
            issues.append(
                f"code block {left.index} language differs: "
                f"{english}={left.language or '<plain>'}; {russian}={right.language or '<plain>'}"
            )
            continue

        left_normalized = _normalize_block(left)
        right_normalized = _normalize_block(right)
        if left_normalized != right_normalized:
            diff = _diff(
                left_normalized,
                right_normalized,
                f"{english}:block-{left.index}",
                f"{russian}:block-{right.index}",
            )
            issues.append(f"code block {left.index} differs:\n{diff}")

    return {
        "ok": not issues,
        "english": str(english),
        "russian": str(russian),
        "heading_count": len(english_headings),
        "code_block_count": len(english_blocks),
        "error_count": len(issues),
        "warning_count": 0,
        "info_count": 0,
        "issues": [
            {
                "severity": "error",
                "kind": "docs_mirror",
                "message": issue,
            }
            for issue in issues
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check README.md and README.ru.md structural/code-block parity.",
    )
    parser.add_argument("--english", type=Path, default=_REPO_ROOT / "README.md")
    parser.add_argument("--russian", type=Path, default=_REPO_ROOT / "README.ru.md")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    payload = check_docs(args.english, args.russian)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    if not payload["ok"]:
        print("README mirror drift detected:", file=sys.stderr)
        for issue in payload["issues"]:
            print(f"- {issue['message']}", file=sys.stderr)
        return 1

    print(f"README mirror OK: {args.english} <-> {args.russian}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
