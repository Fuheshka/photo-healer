"""Unit and integration tests for GitHub Releases update checker (photo_healer.cli.updater)."""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from photo_healer import __version__
from photo_healer.cli.i18n import set_language
from photo_healer.cli.main import build_parser, main
from photo_healer.cli.updater import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_REPO,
    UpdateResult,
    check_and_notify_background,
    check_cooldown,
    check_for_updates,
    find_platform_asset,
    format_update_notice,
    is_version_newer,
    load_cache,
    parse_semver,
    save_cache,
)


@pytest.fixture(autouse=True)
def enforce_test_language(monkeypatch: pytest.MonkeyPatch):
    """Enforce English interface language by default for predictable string assertions."""
    monkeypatch.setenv("PHOTO_HEALER_LANG", "en")
    set_language("en")
    yield
    set_language(None)


@pytest.fixture
def temp_cache_file(tmp_path: Path) -> Path:
    """Provide an isolated temporary cache file for update check testing."""
    return tmp_path / "update_check.json"


# ── Group 1: SemVer Parsing & Validation ──────────────────────────────────────
class TestSemVerComparison:
    """Verifies semantic versioning parser and comparison logic according to app-update-checker."""

    def test_basic_version_comparison(self):
        assert is_version_newer("1.1.0", "1.0.0") is True
        assert is_version_newer("1.0.0", "1.1.0") is False
        assert is_version_newer("0.2.1", "0.2.0") is True
        assert is_version_newer("0.2.0", "0.2.0") is False

    def test_strip_prefix_v_and_v(self):
        assert is_version_newer("v1.2.0", "1.1.0") is True
        assert is_version_newer("V2.0.0", "1.9.9") is True
        assert is_version_newer("1.2.0", "v1.1.0") is True
        assert is_version_newer("v0.2.0", "V0.2.0") is False

    def test_numeric_segment_comparison(self):
        # 1.10.0 > 1.9.5 numerically (not lexicographically)
        assert is_version_newer("1.10.0", "1.9.5") is True
        assert is_version_newer("1.9.5", "1.10.0") is False
        assert is_version_newer("2.0.0", "1.99.99") is True
        assert is_version_newer("1.99.99", "2.0.0") is False

    def test_unequal_segment_lengths(self):
        assert is_version_newer("1.0.1", "1.0") is True
        assert is_version_newer("1.0", "1.0.0") is False
        assert is_version_newer("1.0.0", "1.0") is False
        assert is_version_newer("2", "1.9.9") is True

    def test_pre_release_tags_and_metadata(self):
        # Numeric extraction should handle -rc, -beta tags cleanly
        assert is_version_newer("1.2.0-rc1", "1.1.0") is True
        assert is_version_newer("1.1.0", "1.2.0-beta") is False

    def test_parse_semver_robustness(self):
        assert parse_semver("v1.2.3") == [1, 2, 3]
        assert parse_semver("  V0.9.8  ") == [0, 9, 8]
        assert parse_semver("") == [0]
        assert parse_semver("invalid") == [0]


# ── Group 2: Platform Asset Detection ─────────────────────────────────────────
class TestPlatformAssetDetection:
    """Verifies priority matching of GitHub Release assets by operating system."""

    SAMPLE_ASSETS = [
        {"name": "photo-healer-windows-x64.zip", "browser_download_url": "https://gh.com/dl/win.zip"},
        {"name": "photo-healer-macos.zip", "browser_download_url": "https://gh.com/dl/mac.zip"},
        {"name": "photo-healer-macos.dmg", "browser_download_url": "https://gh.com/dl/mac.dmg"},
        {"name": "photo-healer-linux.AppImage", "browser_download_url": "https://gh.com/dl/linux.AppImage"},
        {"name": "photo-healer-linux.tar.gz", "browser_download_url": "https://gh.com/dl/linux.tar.gz"},
    ]

    def test_match_windows_assets(self):
        asset = find_platform_asset(self.SAMPLE_ASSETS, target_platform="win32")
        assert asset is not None
        assert "win" in asset["name"].lower()
        assert asset["url"] == "https://gh.com/dl/win.zip"

    def test_match_macos_assets_dmg_over_zip(self):
        # On macOS, .dmg is preferred over .zip
        asset = find_platform_asset(self.SAMPLE_ASSETS, target_platform="darwin")
        assert asset is not None
        assert asset["name"] == "photo-healer-macos.dmg"
        assert asset["url"] == "https://gh.com/dl/mac.dmg"

    def test_match_linux_assets_appimage_over_targz(self):
        # On Linux, .AppImage is preferred over .tar.gz
        asset = find_platform_asset(self.SAMPLE_ASSETS, target_platform="linux")
        assert asset is not None
        assert asset["name"] == "photo-healer-linux.AppImage"
        assert asset["url"] == "https://gh.com/dl/linux.AppImage"

    def test_fallback_extensions_when_no_os_name_in_title(self):
        generic_assets = [
            {"name": "setup.exe", "browser_download_url": "https://gh.com/setup.exe"},
            {"name": "package.deb", "browser_download_url": "https://gh.com/package.deb"},
        ]
        win_asset = find_platform_asset(generic_assets, target_platform="win32")
        assert win_asset is not None
        assert win_asset["name"] == "setup.exe"

        linux_asset = find_platform_asset(generic_assets, target_platform="linux")
        assert linux_asset is not None
        assert linux_asset["name"] == "package.deb"

    def test_no_matching_asset_returns_none(self):
        unrelated_assets = [
            {"name": "checksums.txt", "browser_download_url": "https://gh.com/checksums.txt"},
            {"name": "source-code.zip", "browser_download_url": "https://gh.com/src.zip"},
        ]
        assert find_platform_asset(unrelated_assets, target_platform="darwin") is None

    def test_empty_asset_list(self):
        assert find_platform_asset([], target_platform="win32") is None


# ── Group 3: Rate Limit Guard & Cache Management ──────────────────────────────
class TestRateLimitGuard:
    """Verifies local 24-hour rate limit guard and cache file persistence."""

    def test_load_cache_nonexistent(self, temp_cache_file: Path):
        assert load_cache(temp_cache_file) == {}

    def test_load_cache_corrupt_file_safe(self, temp_cache_file: Path):
        temp_cache_file.write_text("{corrupted-json-content...", encoding="utf-8")
        assert load_cache(temp_cache_file) == {}

    def test_save_and_load_cache(self, temp_cache_file: Path):
        data = {
            "last_check_timestamp": 1700000000.0,
            "latest_version": "1.0.0",
            "download_url": "https://example.com/dl",
        }
        save_cache(temp_cache_file, data)
        assert temp_cache_file.exists()
        loaded = load_cache(temp_cache_file)
        assert loaded == data

    def test_check_cooldown_active(self, temp_cache_file: Path):
        recent_time = time.time() - 3600  # 1 hour ago (< 24 hours)
        save_cache(temp_cache_file, {"last_check_timestamp": recent_time})
        assert check_cooldown(temp_cache_file, cooldown_seconds=86400) is True

    def test_check_cooldown_expired(self, temp_cache_file: Path):
        old_time = time.time() - 90000  # 25 hours ago (> 24 hours)
        save_cache(temp_cache_file, {"last_check_timestamp": old_time})
        assert check_cooldown(temp_cache_file, cooldown_seconds=86400) is False

    def test_check_cooldown_missing_file(self, temp_cache_file: Path):
        assert check_cooldown(temp_cache_file) is False


# ── Group 4: GitHub API Checks & Network Scenarios ────────────────────────────
class TestGitHubApiIntegration:
    """Verifies GitHub Releases API queries, SemVer checking, and fail-safe network handling."""

    def test_update_available_scenario(self, temp_cache_file: Path):
        mock_response = io.BytesIO(
            json.dumps({
                "tag_name": "v9.9.9",
                "html_url": "https://github.com/fuheshka/photo-healer/releases/tag/v9.9.9",
                "body": "Major forensic enhancements",
                "assets": [
                    {
                        "name": "photo-healer-windows-x64.zip",
                        "browser_download_url": "https://github.com/releases/download/v9.9.9/photo-healer-windows-x64.zip",
                    }
                ],
            }).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
                target_platform="win32",
            )

        assert result.status == "update_available"
        assert result.latest_version == "9.9.9"
        assert result.download_url == "https://github.com/releases/download/v9.9.9/photo-healer-windows-x64.zip"
        assert temp_cache_file.exists()

    def test_up_to_date_scenario(self, temp_cache_file: Path):
        mock_response = io.BytesIO(
            json.dumps({
                "tag_name": "v0.2.0",
                "html_url": "https://github.com/fuheshka/photo-healer/releases/tag/v0.2.0",
                "assets": [],
            }).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
            )

        assert result.status == "up_to_date"
        assert result.latest_version == "0.2.0"

    def test_throttled_skips_network_call(self, temp_cache_file: Path):
        save_cache(temp_cache_file, {"last_check_timestamp": time.time() - 100})

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=False,
            )
            mock_urlopen.assert_not_called()

        assert result.status == "throttled"
        assert result.cached is True

    def test_http_403_rate_limit_fail_safe(self, temp_cache_file: Path):
        error = urllib.error.HTTPError(
            url="https://api.github.com/repos/fuheshka/photo-healer/releases/latest",
            code=403,
            msg="rate limit exceeded",
            hdrs={},
            fp=io.BytesIO(b"rate limit exceeded"),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
            )

        assert result.status == "error"
        assert "403" in str(result.error_message)

    def test_network_offline_urlerror_fail_safe(self, temp_cache_file: Path):
        error = urllib.error.URLError(reason="Name resolution failure / Offline")
        with patch("urllib.request.urlopen", side_effect=error):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
            )

        assert result.status == "error"
        assert "Name resolution failure" in str(result.error_message)

    def test_timeout_error_fail_safe(self, temp_cache_file: Path):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
            )

        assert result.status == "error"
        assert "timed out" in str(result.error_message).lower()

    def test_malformed_json_response_fail_safe(self, temp_cache_file: Path):
        mock_response = io.BytesIO(b"<!DOCTYPE html><html>Not JSON</html>")
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = check_for_updates(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                force=True,
            )

        assert result.status == "error"


# ── Group 5: Notification Formatting & Bilingual Localization ─────────────────
class TestNotificationFormatting:
    """Verifies dual-language (EN/RU) notification output formatting."""

    def test_format_notice_english(self):
        res = UpdateResult(
            status="update_available",
            current_version="0.2.0",
            latest_version="0.3.0",
            download_url="https://example.com/dl.zip",
        )
        notice = format_update_notice(res, lang="en")
        assert "Update Available" in notice
        assert "0.3.0" in notice
        assert "https://example.com/dl.zip" in notice

    def test_format_notice_russian(self):
        res = UpdateResult(
            status="update_available",
            current_version="0.2.0",
            latest_version="0.3.0",
            download_url="https://example.com/dl.zip",
        )
        notice = format_update_notice(res, lang="ru")
        assert "Доступно обновление" in notice
        assert "0.3.0" in notice
        assert "https://example.com/dl.zip" in notice

    def test_format_notice_up_to_date_empty(self):
        res = UpdateResult(status="up_to_date", current_version="0.2.0", latest_version="0.2.0")
        assert format_update_notice(res, lang="en") == ""


# ── Group 6: Background Check Fail-Safe Operation ─────────────────────────────
class TestBackgroundCheck:
    """Verifies that background auto-check never crashes the app and remains quiet."""

    def test_background_check_notifies_on_update(self, temp_cache_file: Path):
        mock_response = io.BytesIO(
            json.dumps({
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/fuheshka/photo-healer/releases/tag/v1.0.0",
                "assets": [],
            }).encode("utf-8")
        )
        mock_response.status = 200

        stream = io.StringIO()
        with patch("urllib.request.urlopen", return_value=mock_response):
            res = check_and_notify_background(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                lang="en",
                stream=stream,
            )

        assert res is not None
        assert res.status == "update_available"
        assert "Update Available" in stream.getvalue()

    def test_background_check_silent_on_up_to_date(self, temp_cache_file: Path):
        mock_response = io.BytesIO(
            json.dumps({"tag_name": "v0.2.0", "html_url": "https://example.com", "assets": []}).encode("utf-8")
        )
        mock_response.status = 200

        stream = io.StringIO()
        with patch("urllib.request.urlopen", return_value=mock_response):
            res = check_and_notify_background(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                stream=stream,
            )

        assert res is not None
        assert res.status == "up_to_date"
        assert stream.getvalue() == ""

    def test_background_check_silent_on_network_failure(self, temp_cache_file: Path):
        stream = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("No connection")):
            res = check_and_notify_background(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                stream=stream,
            )

        assert res is not None
        assert res.status == "error"
        assert stream.getvalue() == ""

    def test_background_check_swallows_all_unexpected_crashes(self, temp_cache_file: Path):
        stream = io.StringIO()
        with patch("photo_healer.cli.updater.check_for_updates", side_effect=RuntimeError("Catastrophic error")):
            res = check_and_notify_background(
                current_version="0.2.0",
                cache_file=temp_cache_file,
                stream=stream,
            )

        assert res is None
        assert stream.getvalue() == ""


# ── Group 7: CLI Subcommand Integration ───────────────────────────────────────
class TestCliUpdateCheckCommand:
    """Verifies manual 'photo-healer update-check' CLI subcommand."""

    def test_parser_has_update_check_command(self):
        parser = build_parser(lang="en")
        subparsers_action = next(a for a in parser._actions if getattr(a, "dest", None) == "command")
        assert "update-check" in subparsers_action.choices

    def test_cli_update_check_update_available(self, temp_cache_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        monkeypatch.setattr("photo_healer.cli.updater.DEFAULT_CACHE_FILE", temp_cache_file)
        mock_response = io.BytesIO(
            json.dumps({
                "tag_name": "v99.0.0",
                "html_url": "https://github.com/fuheshka/photo-healer/releases/tag/v99.0.0",
                "assets": [],
            }).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            code = main(["update-check", "--no-banner"])

        assert code == 0
        captured = capsys.readouterr()
        assert "99.0.0" in captured.out or "99.0.0" in captured.err

    def test_cli_update_check_up_to_date(self, temp_cache_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        from photo_healer import __version__
        monkeypatch.setattr("photo_healer.cli.updater.DEFAULT_CACHE_FILE", temp_cache_file)
        mock_response = io.BytesIO(
            json.dumps({"tag_name": f"v{__version__}", "html_url": "https://example.com", "assets": []}).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            code = main(["update-check", "--no-banner"])

        assert code == 0
        captured = capsys.readouterr()
        assert "latest version" in captured.out

    def test_cli_update_check_quiet_flag(self, temp_cache_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        from photo_healer import __version__
        monkeypatch.setattr("photo_healer.cli.updater.DEFAULT_CACHE_FILE", temp_cache_file)
        mock_response = io.BytesIO(
            json.dumps({"tag_name": f"v{__version__}", "html_url": "https://example.com", "assets": []}).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            code = main(["update-check", "--quiet", "--no-banner"])

        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_cli_update_check_russian(self, temp_cache_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        from photo_healer import __version__
        monkeypatch.setattr("photo_healer.cli.updater.DEFAULT_CACHE_FILE", temp_cache_file)
        mock_response = io.BytesIO(
            json.dumps({"tag_name": f"v{__version__}", "html_url": "https://example.com", "assets": []}).encode("utf-8")
        )
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            code = main(["update-check", "--lang", "ru", "--no-banner"])

        assert code == 0
        captured = capsys.readouterr()
        assert "свежая версия" in captured.out
