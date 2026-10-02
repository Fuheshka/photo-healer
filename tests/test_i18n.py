"""Unit and integration tests for CLI internationalization (photo_healer.cli.i18n)."""

from __future__ import annotations

import argparse
import ctypes
import locale
import os
import re
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from photo_healer.cli import i18n
from photo_healer.cli.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    detect_language,
    detect_system_language,
    ensure_windows_utf8,
    get_language,
    normalize_language,
    reset_language,
    set_language,
    t,
)
from photo_healer.cli.main import build_parser, main


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def isolate_language_state(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test runs with isolated language state and clean environment."""
    for var in ("PHOTO_HEALER_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)

    reset_language()
    yield
    reset_language()


# ── Group 1: Translation Dictionary Parity & Integrity ────────────────────────
class TestDictionaryParity:
    """Verifies complete parity and syntactic validity between EN and RU dictionaries."""

    PLACEHOLDER_REGEX = re.compile(r"\{([a-zA-Z0-9_]+)\}")

    def test_both_supported_languages_present(self):
        """EN and RU dictionaries must be defined in TRANSLATIONS."""
        assert "en" in TRANSLATIONS, "English dictionary missing"
        assert "ru" in TRANSLATIONS, "Russian dictionary missing"
        assert set(SUPPORTED_LANGUAGES) == {"en", "ru"}
        assert DEFAULT_LANGUAGE == "en"

    def test_all_keys_match_between_en_and_ru(self):
        """Every key present in English must exist in Russian and vice-versa."""
        en_keys = set(TRANSLATIONS["en"].keys())
        ru_keys = set(TRANSLATIONS["ru"].keys())

        missing_in_ru = en_keys - ru_keys
        missing_in_en = ru_keys - en_keys

        assert not missing_in_ru, f"Keys present in EN but missing in RU: {sorted(missing_in_ru)}"
        assert not missing_in_en, f"Keys present in RU but missing in EN: {sorted(missing_in_en)}"

    def test_no_empty_translation_values(self):
        """All translation entries must be non-empty strings."""
        for lang_code in ("en", "ru"):
            for key, val in TRANSLATIONS[lang_code].items():
                assert isinstance(val, str), f"Value for '{key}' in '{lang_code}' must be str"
                assert val.strip(), f"Value for '{key}' in '{lang_code}' cannot be empty or blank"

    def test_interpolation_placeholders_match(self):
        """Placeholders like {count} or {path} must match exactly between languages."""
        en_dict = TRANSLATIONS["en"]
        ru_dict = TRANSLATIONS["ru"]

        for key in en_dict:
            en_placeholders = set(self.PLACEHOLDER_REGEX.findall(en_dict[key]))
            ru_placeholders = set(self.PLACEHOLDER_REGEX.findall(ru_dict[key]))

            assert en_placeholders == ru_placeholders, (
                f"Placeholder mismatch for key '{key}': "
                f"EN has {en_placeholders}, but RU has {ru_placeholders}"
            )


# ── Group 2: Language Auto-Detection Hierarchy ────────────────────────────────
class TestLanguageDetection:
    """Verifies language auto-detection priority and fallback mechanisms."""

    @pytest.mark.parametrize(
        "raw_code, expected",
        [
            ("ru", "ru"),
            ("RU", "ru"),
            ("ru_RU", "ru"),
            ("ru-RU", "ru"),
            ("ru_RU.UTF-8", "ru"),
            ("russian", "ru"),
            ("en", "en"),
            ("en_US", "en"),
            ("en-GB", "en"),
            ("en_US.UTF-8", "en"),
            ("fr_FR", "en"),
            ("de_DE", "en"),
            ("es_ES", "en"),
            ("zh_CN", "en"),
            ("", "en"),
            (None, "en"),
        ],
    )
    def test_normalize_language(self, raw_code: str | None, expected: str):
        """Normalizes various locale formats to either 'ru' or fallback 'en'."""
        assert normalize_language(raw_code) == expected

    def test_detection_priority_photo_healer_env(self, monkeypatch: pytest.MonkeyPatch):
        """PHOTO_HEALER_LANG environment variable has highest precedence."""
        monkeypatch.setenv("PHOTO_HEALER_LANG", "ru")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

        monkeypatch.setenv("PHOTO_HEALER_LANG", "en")
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert detect_language() == "en"

    def test_detection_standard_env_variables(self, monkeypatch: pytest.MonkeyPatch):
        """LC_ALL, LC_MESSAGES, and LANG take precedence over system locale."""
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.setenv("LC_MESSAGES", "ru_RU.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert detect_language() == "ru"

    def test_detection_locale_getlocale_fallback(self):
        """Uses locale.getlocale() when env vars are unset."""
        with patch("locale.getlocale", return_value=("ru_RU", "UTF-8")):
            assert detect_language() == "ru"

        with patch("locale.getlocale", return_value=("en_US", "UTF-8")):
            assert detect_language() == "en"

    def test_detection_windows_api_fallback(self):
        """Uses Windows GetUserDefaultUILanguage() when locale returns None."""
        with patch("locale.getlocale", return_value=(None, None)), \
             patch("locale.getdefaultlocale", return_value=(None, None)):

            mock_kernel32 = MagicMock()
            mock_kernel32.GetUserDefaultUILanguage.return_value = 0x0419  # Russian

            with patch.object(ctypes, "windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                assert detect_language() == "ru"

            mock_kernel32.GetUserDefaultUILanguage.return_value = 0x0409  # English
            with patch.object(ctypes, "windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                assert detect_language() == "en"

    def test_detection_defaults_to_english_on_failure(self):
        """Defaults cleanly to 'en' when all probes fail or error."""
        with patch("locale.getlocale", side_effect=Exception("locale error")), \
             patch("locale.getdefaultlocale", side_effect=Exception("locale error")), \
             patch("sys.platform", "linux"):
            assert detect_language() == "en"

    def test_detect_system_language_alias(self):
        """Verifies detect_system_language is an alias for detect_language."""
        assert detect_system_language() == detect_language()


# ── Group 3: Explicit Language Switching & Overrides ──────────────────────────
class TestLanguageStateManagement:
    """Verifies runtime state changes via set_language, get_language, and reset."""

    def test_default_active_language(self):
        """Initial active language reflects auto-detection or 'en'."""
        assert get_language() in ("en", "ru")

    def test_set_language_valid(self):
        """Explicitly setting language changes active language."""
        set_language("ru")
        assert get_language() == "ru"

        set_language("en")
        assert get_language() == "en"

    def test_set_unsupported_language_defaults_to_en(self):
        """Passing unsupported language code defaults safely to 'en'."""
        set_language("de")
        assert get_language() == "en"

    def test_explicit_lang_override_in_t(self):
        """Passing lang parameter to t() overrides active global language for this call."""
        set_language("en")
        assert get_language() == "en"
        ru_text = t("cli_description", lang="ru")
        en_text = t("cli_description", lang="en")
        assert ru_text != en_text
        assert "криминалистического" in ru_text
        assert "Forensic" in en_text


# ── Group 4: String Translation & Interpolation ───────────────────────────────
class TestTranslationHelper:
    """Verifies retrieval, formatting, and fallback behavior of t()."""

    def test_basic_translation_en_and_ru(self):
        """Retrieves correct translated strings for each language."""
        set_language("en")
        en_res = t("cli_description")
        assert "Forensic repair tool" in en_res

        set_language("ru")
        ru_res = t("cli_description")
        assert "Инструмент криминалистического восстановления" in ru_res

    def test_interpolation_with_named_kwargs(self):
        """Replaces named format placeholders with kwargs."""
        with patch.dict(TRANSLATIONS["en"], {"test_fmt": "Found {count} files in {path}."}), \
             patch.dict(TRANSLATIONS["ru"], {"test_fmt": "Найдено {count} файлов в {path}."}):

            set_language("en")
            assert t("test_fmt", count=42, path="/photos") == "Found 42 files in /photos."

            set_language("ru")
            assert t("test_fmt", count=42, path="/photos") == "Найдено 42 файлов в /photos."

    def test_missing_format_kwarg_does_not_crash(self):
        """Missing kwargs should not raise KeyError, but preserve template or format safely."""
        with patch.dict(TRANSLATIONS["en"], {"test_missing": "Hello {name}, your score is {score}"}):
            set_language("en")
            res = t("test_missing", name="Alice")
            assert "Alice" in res

    def test_unknown_key_returns_key_itself(self):
        """Querying a non-existent key returns the key itself as fallback."""
        missing_key = "non_existent_diagnostic_key_12345"
        assert t(missing_key) == missing_key
        assert t(missing_key, default="Default fallback") == "Default fallback"


# ── Group 5: Windows Console UTF-8 Safety ─────────────────────────────────────
class TestWindowsConsoleUtf8:
    """Verifies safety helper for reconfiguring standard I/O streams in Windows."""

    def test_reconfigure_called_when_supported(self):
        """Calls reconfigure with encoding='utf-8' and errors='replace'."""
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        with patch.object(sys, "stdout", mock_stdout), \
             patch.object(sys, "stderr", mock_stderr):
            ensure_windows_utf8()

            mock_stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
            mock_stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_safe_when_reconfigure_not_supported(self):
        """Does not raise error if stream lacks reconfigure attribute."""
        class StreamWithoutReconfigure:
            pass

        with patch.object(sys, "stdout", StreamWithoutReconfigure()), \
             patch.object(sys, "stderr", StreamWithoutReconfigure()):
            ensure_windows_utf8()

    def test_safe_when_reconfigure_raises_exception(self):
        """Silently catches exceptions if reconfigure fails."""
        mock_stdout = MagicMock()
        mock_stdout.reconfigure.side_effect = OSError("Pipe closed")

        with patch.object(sys, "stdout", mock_stdout), \
             patch.object(sys, "stderr", mock_stdout):
            ensure_windows_utf8()


# ── Group 6: CLI Parser Localization & Integration ────────────────────────────
class TestCliParserLocalization:
    """Verifies argparse construction with localized help and argument messages."""

    def test_build_parser_en(self):
        """Parser constructed in English has English description and epilog."""
        parser = build_parser(lang="en")
        help_text = parser.format_help()
        assert "Forensic repair tool for SSD TRIM-damaged photo archives" in help_text
        assert "triage" in help_text
        assert "quarantine" in help_text

    def test_build_parser_ru(self):
        """Parser constructed in Russian has Russian description and epilog."""
        parser = build_parser(lang="ru")
        help_text = parser.format_help()
        assert "Инструмент криминалистического восстановления" in help_text
        assert "triage" in help_text
        assert re.search(r"[\u0400-\u04FF]", help_text)

    def test_subcommand_arguments_localized_in_ru(self):
        """Subcommands (triage, heal, batch-heal, quarantine, carve) have Russian help text."""
        parser = build_parser(lang="ru")
        subparsers_actions = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        assert subparsers_actions, "Subparsers action must exist"
        choices = subparsers_actions[0].choices

        for cmd in ("triage", "heal", "batch-heal", "quarantine", "carve"):
            assert cmd in choices, f"Subcommand '{cmd}' not found"
            sub_help = choices[cmd].format_help()
            assert re.search(r"[\u0400-\u04FF]", sub_help), f"Russian text missing in '{cmd}' help"


# ── Group 7: CLI End-to-End Integration Tests ─────────────────────────────────
class TestCliIntegration:
    """Verifies command line execution behavior with --lang and environment variables."""

    def test_cli_help_flag_explicit_ru(self, capsys: pytest.CaptureFixture):
        """photo-healer --lang ru --help displays Russian help."""
        with pytest.raises(SystemExit) as exc:
            main(["--lang", "ru", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert re.search(r"[\u0400-\u04FF]", captured.out), "Russian letters expected in output"

    def test_cli_help_flag_explicit_en(self, capsys: pytest.CaptureFixture):
        """photo-healer --lang en --help explicitly displays English help."""
        with pytest.raises(SystemExit) as exc:
            main(["--lang", "en", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "Forensic repair tool" in captured.out

    def test_cli_subcommand_help_ru(self, capsys: pytest.CaptureFixture):
        """photo-healer --lang ru triage --help displays Russian triage help."""
        with pytest.raises(SystemExit) as exc:
            main(["--lang", "ru", "triage", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert re.search(r"[\u0400-\u04FF]", captured.out)

    def test_cli_subcommand_help_en(self, capsys: pytest.CaptureFixture):
        """photo-healer --lang en triage --help displays English triage help."""
        with pytest.raises(SystemExit) as exc:
            main(["--lang", "en", "triage", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "High-speed streaming triage" in captured.out or "Directory path to scan and audit" in captured.out

    def test_cli_env_var_override(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
        """PHOTO_HEALER_LANG=ru displays Russian help without explicit flag."""
        monkeypatch.setenv("PHOTO_HEALER_LANG", "ru")
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert re.search(r"[\u0400-\u04FF]", captured.out)

    def test_cli_lang_flag_overrides_env_var(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
        """CLI flag --lang en takes precedence over PHOTO_HEALER_LANG=ru."""
        monkeypatch.setenv("PHOTO_HEALER_LANG", "ru")
        with pytest.raises(SystemExit) as exc:
            main(["--lang", "en", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "Forensic repair tool" in captured.out
