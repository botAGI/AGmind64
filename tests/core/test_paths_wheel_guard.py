"""Tests for the SPEC-17.2 wheel=checkout-only refuse guard in `agmind.core.paths`.

`agmind install` needs the full repo checkout (it drives ansible against the source
tree); a wheel in site-packages is the *operator* CLI only. `is_wheel_layout` /
`refuse_if_wheel_install` decide this by asking whether a repo-root ``templates/``
sits beside the package. These tests synthesize both layouts under ``tmp_path`` and
pass the package dir explicitly, so they never depend on the real install location.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.core.paths import (
    WheelInstallRefused,
    is_wheel_layout,
    refuse_if_wheel_install,
)

pytestmark = pytest.mark.backend_any


def _checkout_pkg(tmp_path: Path) -> Path:
    """Synthesize a checkout/editable layout: <repo>/templates beside <repo>/agmind."""
    pkg = tmp_path / "agmind"
    pkg.mkdir()
    (tmp_path / "templates").mkdir()
    return pkg


def _wheel_pkg(tmp_path: Path) -> Path:
    """Synthesize a wheel layout: site-packages/agmind/templates, NO site-packages/templates."""
    pkg = tmp_path / "site-packages" / "agmind"
    (pkg / "templates").mkdir(parents=True)
    return pkg


def test_is_wheel_layout_false_for_checkout(tmp_path: Path) -> None:
    assert is_wheel_layout(_checkout_pkg(tmp_path)) is False


def test_is_wheel_layout_true_for_wheel(tmp_path: Path) -> None:
    assert is_wheel_layout(_wheel_pkg(tmp_path)) is True


def test_refuse_is_noop_for_checkout(tmp_path: Path) -> None:
    # No exception, returns None.
    assert refuse_if_wheel_install(_checkout_pkg(tmp_path)) is None


def test_refuse_raises_for_wheel_with_make_setup_message(tmp_path: Path) -> None:
    with pytest.raises(WheelInstallRefused) as excinfo:
        refuse_if_wheel_install(_wheel_pkg(tmp_path))
    msg = str(excinfo.value)
    assert "make setup" in msg
    assert "site-packages wheel" in msg


def test_wheel_install_refused_is_runtimeerror() -> None:
    # Callers may catch RuntimeError generically; keep that contract.
    assert issubclass(WheelInstallRefused, RuntimeError)


def test_missing_templates_both_sides_reports_wheel(tmp_path: Path) -> None:
    # A package with neither a repo-root nor an in-package templates/ sibling has no
    # checkout marker → treated as wheel (refuse), matching the stated definition
    # ("True when the package has NO repo-root templates sibling").
    pkg = tmp_path / "somewhere" / "agmind"
    pkg.mkdir(parents=True)
    assert is_wheel_layout(pkg) is True
    with pytest.raises(WheelInstallRefused):
        refuse_if_wheel_install(pkg)
