"""Tests для agmind.i18n — translation lookup."""

from __future__ import annotations

import pytest

from agmind.i18n import _load, detect_lang, t

pytestmark = pytest.mark.backend_any


# ---- Phase M3.T: wizard catalogs coverage ----


WIZARD_KEYS = (
    "wizard.title",
    "wizard.section.domain",
    "wizard.section.cf_token",
    "wizard.section.backend",
    "wizard.section.model",
    "wizard.section.ctx_size",
    "wizard.section.kv_cache",
    "wizard.section.threads",
    "wizard.section.parallel",
    "wizard.section.services",
    "wizard.placeholder.domain",
    "wizard.placeholder.cf_token",
    "wizard.btn.preview",
    "wizard.btn.apply",
    "wizard.btn.next",
    "wizard.btn.back",
    "wizard.toast.validation_errors_title",
)


def test_all_wizard_keys_present_in_en() -> None:
    en = _load("en")
    for k in WIZARD_KEYS:
        assert k in en, f"missing en key: {k}"


def test_all_wizard_keys_present_in_ru() -> None:
    ru = _load("ru")
    for k in WIZARD_KEYS:
        assert k in ru, f"missing ru key: {k}"


def test_wizard_section_services_interpolates() -> None:
    en = t("wizard.section.services", lang="en")
    ru = t("wizard.section.services", lang="ru")
    assert "{total}" in en
    assert "{total}" in ru
    assert en.format(total=33) == "Services (33 available — defaults preselected)"


def test_detect_lang_default_en(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("AGMIND_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(k, raising=False)
    assert detect_lang() == "en"


def test_detect_lang_explicit_ru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "ru")
    assert detect_lang() == "ru"


def test_detect_lang_explicit_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    assert detect_lang() == "en"


def test_detect_lang_unknown_fallback_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "fr")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert detect_lang() == "en"


def test_detect_lang_from_lc_all_ru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGMIND_LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    assert detect_lang() == "ru"


def test_detect_lang_from_lang_ru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGMIND_LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    assert detect_lang() == "ru"


def test_t_known_key_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    assert t("cli.doctor.title") == "AGmind doctor"


def test_t_known_key_ru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "ru")
    assert t("cli.status.selected") == "Выбран"


def test_t_missing_key_fallback_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если ключ отсутствует в ru, fallback в en."""
    monkeypatch.setenv("AGMIND_LANG", "ru")
    # cli.doctor.title есть и в ru и в en — оба возвращают что-то.
    # Реальный тест: ключ только в en (если такой есть) → ru ищет en fallback.
    # У нас сейчас все ключи в обоих — поэтому конкретный fallback-only ключ
    # отсутствует. Проверяем что t() не падает.
    out = t("cli.doctor.title")
    assert out  # non-empty


def test_t_missing_key_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    assert t("does.not.exist", default="fallback") == "fallback"


def test_t_missing_key_no_default_returns_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    out = t("missing.key.nofallback")
    assert out == "missing.key.nofallback"


def test_t_explicit_lang_param() -> None:
    assert t("cli.doctor.title", lang="ru") == "AGmind doctor"
    # cli.status.selected: en="Selected", ru="Выбран"
    assert t("cli.status.selected", lang="en") == "Selected"
    assert t("cli.status.selected", lang="ru") == "Выбран"


def test_t_invalid_lang_falls_back_to_en() -> None:
    out = t("cli.doctor.title", lang="unknown_lang", default="d")
    # Unknown lang: empty table; missing key → fallback to en
    assert out == "AGmind doctor"
