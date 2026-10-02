# -*- coding: utf-8 -*-
"""Unit tests for Photo Healer GUI bilingual localization and I18nManager."""

from __future__ import annotations

import locale
import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from photo_healer.gui.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    I18nManager,
    detect_system_language,
    get_language,
    i18n,
    set_language,
    t,
)

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Ensure a QApplication exists for Qt signal and widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def reset_global_i18n():
    """Reset the global i18n singleton language to default after each test."""
    initial_lang = i18n.get_language()
    yield
    set_language(initial_lang)


class TestLanguageDetection:
    """Tests for detect_system_language priority and fallback logic."""

    def test_detect_env_var_ru(self, monkeypatch):
        monkeypatch.setenv("PHOTO_HEALER_LANG", "ru")
        assert detect_system_language() == "ru"

    def test_detect_env_var_ru_with_locale(self, monkeypatch):
        monkeypatch.setenv("PHOTO_HEALER_LANG", "ru_RU.UTF-8")
        assert detect_system_language() == "ru"

    def test_detect_env_var_en(self, monkeypatch):
        monkeypatch.setenv("PHOTO_HEALER_LANG", "en")
        assert detect_system_language() == "en"

    def test_detect_env_var_en_us(self, monkeypatch):
        monkeypatch.setenv("PHOTO_HEALER_LANG", "en_US")
        assert detect_system_language() == "en"

    def test_detect_qlocale_russian_enum(self, monkeypatch):
        monkeypatch.delenv("PHOTO_HEALER_LANG", raising=False)
        mock_locale = MagicMock()
        mock_locale.language.return_value = QLocale.Language.Russian
        mock_locale.name.return_value = "ru_RU"

        with patch.object(QLocale, "system", return_value=mock_locale):
            assert detect_system_language() == "ru"

    def test_detect_qlocale_russian_name(self, monkeypatch):
        monkeypatch.delenv("PHOTO_HEALER_LANG", raising=False)
        mock_locale = MagicMock()
        mock_locale.language.return_value = QLocale.Language.English
        mock_locale.name.return_value = "ru_UA"

        with patch.object(QLocale, "system", return_value=mock_locale):
            assert detect_system_language() == "ru"

    def test_detect_locale_getlocale_fallback(self, monkeypatch):
        monkeypatch.delenv("PHOTO_HEALER_LANG", raising=False)
        mock_locale = MagicMock()
        mock_locale.language.return_value = QLocale.Language.English
        mock_locale.name.return_value = "en_US"

        with patch.object(QLocale, "system", return_value=mock_locale):
            with patch.object(locale, "getlocale", return_value=("ru_RU", "UTF-8")):
                assert detect_system_language() == "ru"

    def test_detect_locale_getdefaultlocale_fallback(self, monkeypatch):
        monkeypatch.delenv("PHOTO_HEALER_LANG", raising=False)
        mock_locale = MagicMock()
        mock_locale.language.return_value = QLocale.Language.English
        mock_locale.name.return_value = "en_US"

        with patch.object(QLocale, "system", return_value=mock_locale):
            with patch.object(locale, "getlocale", return_value=(None, None)):
                with patch.object(locale, "getdefaultlocale", return_value=("Russian_Russia", "1251")):
                    assert detect_system_language() == "ru"

    def test_detect_fallback_to_english_for_other_languages(self, monkeypatch):
        monkeypatch.delenv("PHOTO_HEALER_LANG", raising=False)
        mock_locale = MagicMock()
        mock_locale.language.return_value = QLocale.Language.German
        mock_locale.name.return_value = "de_DE"

        with patch.object(QLocale, "system", return_value=mock_locale):
            with patch.object(locale, "getlocale", return_value=("de_DE", "UTF-8")):
                with patch.object(locale, "getdefaultlocale", return_value=("de_DE", "UTF-8")):
                    assert detect_system_language() == DEFAULT_LANGUAGE


class TestTranslationFunction:
    """Tests for t() translation, interpolation, and fallbacks in 'en' and 'ru'."""

    def test_translation_en(self):
        set_language("en")
        assert get_language() == "en"
        assert t("app.title") == "Photo Healer - SSD TRIM Photo Forensics"
        assert t("folder.scan") == "Scan"
        assert t("folder.cancel") == "Stop"
        assert t("table.col.name") == "Filename"

    def test_translation_ru(self):
        set_language("ru")
        assert get_language() == "ru"
        assert t("app.title") == "Photo Healer — Восстановление поврежденных фото"
        assert t("folder.scan") == "Сканировать"
        assert t("folder.cancel") == "Остановить"
        assert t("table.col.name") == "Имя файла"

    def test_translation_with_kwargs_en(self):
        set_language("en")
        res = t("status.scanning", current=5, total=100, filename="img01.jpg")
        assert res == "Scanning: 5 / 100 files (img01.jpg)"

        res2 = t("status.completed", count=42, size="10.5 MB")
        assert res2 == "Scan completed: 42 files processed (10.5 MB)"

    def test_translation_with_kwargs_ru(self):
        set_language("ru")
        res = t("status.scanning", current=5, total=100, filename="img01.jpg")
        assert res == "Сканирование: 5 из 100 (img01.jpg)"

        res2 = t("status.completed", count=42, size="10.5 MB")
        assert res2 == "Сканирование завершено: обработано файлов: 42 (10.5 MB)"

    def test_translation_missing_key_fallback(self):
        set_language("en")
        assert t("non.existent.key") == "non.existent.key"
        assert t("non.existent.key", default="Custom Default") == "Custom Default"

    def test_translation_formatting_error_resilience(self):
        set_language("en")
        # Missing required template keys should not crash
        res = t("status.scanning", wrong_key="test")
        assert "Scanning: {current} / {total} files ({filename})" in res


class TestLanguageSwitchingAndSignals:
    """Tests for dynamic language switching and Qt Signal emission."""

    def test_set_language_emits_signal(self):
        set_language("en")
        received_languages: list[str] = []

        def on_lang_changed(lang: str):
            received_languages.append(lang)

        i18n.language_changed.connect(on_lang_changed)
        try:
            set_language("ru")
            assert get_language() == "ru"
            assert received_languages == ["ru"]

            set_language("en")
            assert get_language() == "en"
            assert received_languages == ["ru", "en"]
        finally:
            i18n.language_changed.disconnect(on_lang_changed)

    def test_set_same_language_does_not_emit(self):
        set_language("en")
        signals: list[str] = []

        def on_change(lang: str):
            signals.append(lang)

        i18n.language_changed.connect(on_change)
        try:
            set_language("en")
            assert len(signals) == 0
        finally:
            i18n.language_changed.disconnect(on_change)

    def test_set_unsupported_language_falls_back_to_default(self):
        set_language("ru")
        set_language("es")  # Spanish is not in SUPPORTED_LANGUAGES
        assert get_language() == DEFAULT_LANGUAGE

    def test_isolated_i18n_manager_instance(self):
        manager = I18nManager()
        emitted: list[str] = []
        manager.language_changed.connect(emitted.append)

        manager.set_language("en")
        manager.set_language("ru")
        assert manager.get_language() == "ru"
        assert emitted[-1] == "ru"
        assert manager.t("lang.ru") == "Русский"


class TestTranslationKeysParity:
    """Verifies complete symmetry and critical UI keys across all supported languages."""

    def test_all_supported_languages_defined(self):
        for lang in SUPPORTED_LANGUAGES:
            assert lang in TRANSLATIONS, f"Language {lang} missing from TRANSLATIONS dict"

    def test_symmetric_key_parity_between_en_and_ru(self):
        en_keys = set(TRANSLATIONS["en"].keys())
        ru_keys = set(TRANSLATIONS["ru"].keys())

        missing_in_ru = en_keys - ru_keys
        missing_in_en = ru_keys - en_keys

        assert not missing_in_ru, f"Keys in 'en' missing from 'ru': {sorted(missing_in_ru)}"
        assert not missing_in_en, f"Keys in 'ru' missing from 'en': {sorted(missing_in_en)}"
        assert en_keys == ru_keys

    @pytest.mark.parametrize(
        "critical_key",
        [
            "app.title",
            "app.subtitle",
            "lang.switch",
            "lang.en",
            "lang.ru",
            "folder.label",
            "folder.placeholder",
            "folder.browse",
            "folder.scan",
            "folder.cancel",
            "folder.dialog_title",
            "tab.diagnostics",
            "tab.recovery",
            "tab.gallery",
            "status.ready",
            "status.scanning",
            "status.completed",
            "status.cancelled",
            "status.error",
            "metric.files",
            "metric.total_size",
            "metric.status",
            "table.col.name",
            "table.col.status",
            "table.col.size",
            "table.col.path",
            "status.healed_candidate",
            "status.trim_zero",
            "status.valid",
            "status.error",
            "status.empty",
            "status.other",
            "filter.all",
            "filter.candidates",
            "filter.dummies",
            "filter.intact",
            "action.quarantine",
            "action.export_json",
            "quarantine.title",
            "quarantine.confirm_prompt",
            "quarantine.dest_label",
            "quarantine.dry_run",
            "quarantine.browse",
            "quarantine.btn_ok",
            "quarantine.btn_cancel",
            "quarantine.no_items",
            "quarantine.success",
            "quarantine.dry_run_success",
            "quarantine.dest_dialog_title",
            "export.title",
            "export.filter",
            "export.success",
            "export.error",
            "export.no_data",
            "recovery.title",
            "recovery.desc",
            "gallery.title",
            "gallery.desc",
        ],
    )
    def test_critical_keys_exist_in_all_languages(self, critical_key: str):
        for lang in SUPPORTED_LANGUAGES:
            assert critical_key in TRANSLATIONS[lang], f"Key '{critical_key}' missing in '{lang}'"
            assert isinstance(TRANSLATIONS[lang][critical_key], str)
            assert len(TRANSLATIONS[lang][critical_key]) > 0
