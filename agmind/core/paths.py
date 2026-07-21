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


def _is_checkout_layout(package_dir: Path) -> bool:
    """True when a repo-root ``templates/`` sits beside the package.

    This is the single detection primitive: a checkout/editable install keeps the
    data dirs at the repo root (``<repo>/templates`` beside ``<repo>/agmind``); a
    wheel in site-packages has no such sibling. Both ``_resolve_data_root`` and the
    wheel guard consult it so the layout is decided in exactly one place.
    """
    return (package_dir.parent / _MARKER).is_dir()


def _resolve_data_root(package_dir: Path) -> Path:
    """Return the data root given the ``agmind`` package directory.

    Prefers the repo-root layout (editable/dev) so a checkout always wins, then
    the in-package layout (wheel). Falls back to the repo-root candidate so a
    genuinely missing ``templates/`` surfaces as a clear downstream error rather
    than silently pointing at the package dir.
    """
    repo_root = package_dir.parent
    if _is_checkout_layout(package_dir):
        return repo_root
    if (package_dir / _MARKER).is_dir():
        return package_dir
    return repo_root


@cache
def data_root() -> Path:
    """Directory containing the bundled ``templates/`` / ``ansible/`` / ``scripts/``."""
    # paths.py is agmind/core/paths.py → parents[1] is the agmind/ package dir.
    return _resolve_data_root(Path(__file__).resolve().parents[1])


def _package_dir() -> Path:
    """The ``agmind/`` package directory (``agmind/core/paths.py`` → parents[1])."""
    return Path(__file__).resolve().parents[1]


def is_wheel_layout(package_dir: Path | None = None) -> bool:
    """True when agmind is running from an installed wheel rather than a checkout.

    A wheel in site-packages has NO repo-root ``templates/`` beside the package —
    the inverse of the checkout/editable layout (which does). Reuses the single
    ``_is_checkout_layout`` detection primitive; ``package_dir`` defaults to the
    live ``agmind/`` package dir (override in tests to synthesize either layout).
    """
    return not _is_checkout_layout(package_dir if package_dir is not None else _package_dir())


class WheelInstallRefused(RuntimeError):
    """Raised when a checkout-only command (install/setup) is invoked from a wheel."""


# Actionable, dated rationale: the wheel bundles templates/ansible/scripts as
# package-data so the *operator* CLI (doctor/verify/creds) works from
# ``/opt/agmind/venv``, but `agmind install` drives ansible against the repo
# checkout (the ``agmind @ file://{{ playbook_dir }}/..`` self-install expects the
# source tree, not a re-packaged wheel). Refuse fast with the fix instead of a
# later obscure ansible/data-root failure.
_WHEEL_REFUSAL_MESSAGE = (
    "agmind must run from a git checkout + .venv, not a site-packages wheel "
    "(wheel = operator CLI only). Re-run install from the checkout: make setup"
)


def refuse_if_wheel_install(package_dir: Path | None = None) -> None:
    """Raise ``WheelInstallRefused`` when running from a wheel; no-op in a checkout."""
    if is_wheel_layout(package_dir):
        raise WheelInstallRefused(_WHEEL_REFUSAL_MESSAGE)
