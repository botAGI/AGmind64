"""Shared subprocess + sudo primitives.

Single home for the "run argv, capture, map timeout→124 / not-found→127" helper
(`run_command`) that several read-only probes (`cluster.inspect`, `ci.monitor`,
`services.kubernetes_dry_run`) previously copy-pasted, and for the low-level sudo
invocation primitives (`sudo_argv`, `sudo_stdin_*`) so that *how* the password is fed
to `sudo -S` lives in exactly one place.

Callers keep their own thin wrappers (preserving their error messages / exception
types / monkeypatch surface) but build the argv and stdin payload from here.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

__all__ = [
    "CommandResult",
    "output_text",
    "run_command",
    "sudo_argv",
    "sudo_stdin_bytes",
    "sudo_stdin_text",
]


@dataclass(frozen=True)
class CommandResult:
    """Small subprocess result used by injectable command probes."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def output_text(value: str | bytes | None) -> str:
    """Normalize ``subprocess`` output (which may be ``bytes`` or ``None``) to ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_command(args: tuple[str, ...], *, timeout: float) -> CommandResult:
    """Run ``args``, capture text output, map missing-binary→127 / timeout→124.

    Never raises for the common failure modes — returns a :class:`CommandResult` so the
    caller can branch on ``returncode``.
    """
    if shutil.which(args[0]) is None:
        return CommandResult(returncode=127, stderr=f"{args[0]} not found")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=124,
            stdout=output_text(exc.output),
            stderr=f"{' '.join(args)} timed out after {exc.timeout} seconds",
        )
    except OSError as exc:
        return CommandResult(returncode=127, stderr=str(exc))
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def sudo_argv(args: list[str]) -> list[str]:
    """Wrap ``args`` for non-interactive sudo: ``sudo -S -p "" -- <args>``.

    ``-S`` reads the password from stdin, ``-p ""`` suppresses the prompt, and ``--``
    stops option parsing so a leading-dash payload can't be mistaken for a sudo flag.
    """
    return ["sudo", "-S", "-p", "", "--", *args]


def sudo_stdin_text(sudo_password: str) -> str:
    """Newline-terminated password for ``sudo -S`` on a text pipe."""
    return f"{sudo_password}\n"


def sudo_stdin_bytes(sudo_password: str) -> bytes:
    """Newline-terminated password for ``sudo -S`` on a bytes pipe."""
    return f"{sudo_password}\n".encode()
