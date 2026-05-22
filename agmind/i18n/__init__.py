"""Minimal i18n — string lookup из JSON dictionaries.

Без gettext / babel — лёгкая зависимость. Языки: en (default) / ru.
Префикс ключа = модуль, e.g. "cli.doctor.kernel_warn".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

_LANG_FILES: Final = {
    "en": Path(__file__).parent / "en.json",
    "ru": Path(__file__).parent / "ru.json",
}

_loaded: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang in _loaded:
        return _loaded[lang]
    path = _LANG_FILES.get(lang)
    if not path or not path.exists():
        _loaded[lang] = {}
        return _loaded[lang]
    try:
        _loaded[lang] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _loaded[lang] = {}
    return _loaded[lang]


def detect_lang() -> str:
    """Return user-preferred language from env, default 'en'."""
    val = os.environ.get("AGMIND_LANG", "").strip().lower()
    if val in _LANG_FILES:
        return val
    # Fall back to LC_ALL / LC_MESSAGES / LANG
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(key, "").strip().lower()
        if v.startswith("ru"):
            return "ru"
    return "en"


def t(key: str, *, lang: str | None = None, default: str | None = None) -> str:
    """Translate key to current language.

    Args:
        key: dotted lookup, e.g. "cli.doctor.no_gpu".
        lang: override AGMIND_LANG. Default — detect_lang().
        default: fallback if key missing in all dictionaries.

    Returns:
        Translated string or `default` (or `key` if no default).
    """
    if lang is None:
        lang = detect_lang()
    table = _load(lang)
    if key in table:
        return table[key]
    if lang != "en":
        en = _load("en")
        if key in en:
            return en[key]
    return default if default is not None else key
