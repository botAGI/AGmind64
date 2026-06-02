"""Resolve the AGmind *data root* — the directory that holds the bundled
``templates/`` / ``ansible/`` / ``scripts/`` trees.

Two install layouts must both work:

- **editable / dev checkout** — ``agmind/`` lives at ``<repo>/agmind`` and the
  data dirs sit at the repo root (``<repo>/templates`` …). ``data_root()``
  returns the repo root, so development always runs against the live tree.
- **wheel** — ``templates/`` / ``ansible/`` / ``scripts/`` are bundled *inside*
  the package (``site-packages/agmind/templates`` …) via the ``pyproject``
  ``package-data`` wiring. ``data_root()`` returns the package dir.

Every ``<root>/templates/…`` and ``<root>/ansible/…`` path in the package
resolves through here so there is a single source of truth. Historically each
module computed ``Path(__file__).resolve().parents[2]`` independently, which
lands in ``site-packages`` (no data) for a wheel install — that is why an
installed ``agmind`` could not run ``agmind install``.

This module imports only the standard library so it is safe to import from
anywhere during package import without risking a cycle.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

# Marker dir present in both layouts; its location distinguishes them.
_MARKER = "templates"


def _resolve_data_root(package_dir: Path) -> Path:
    """Return the data root given the ``agmind`` package directory.

    Prefers the repo-root layout (editable/dev) so a checkout always wins, then
    the in-package layout (wheel). Falls back to the repo-root candidate so a
    genuinely missing ``templates/`` surfaces as a clear downstream error rather
    than silently pointing at the package dir.
    """
    repo_root = package_dir.parent
    if (repo_root / _MARKER).is_dir():
        return repo_root
    if (package_dir / _MARKER).is_dir():
        return package_dir
    return repo_root


@cache
def data_root() -> Path:
    """Directory containing the bundled ``templates/`` / ``ansible/`` / ``scripts/``."""
    # paths.py is agmind/core/paths.py → parents[1] is the agmind/ package dir.
    return _resolve_data_root(Path(__file__).resolve().parents[1])
