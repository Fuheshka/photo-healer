# -*- coding: utf-8 -*-
"""Unit and integration tests for the Photo Healer terminal ASCII banner (photo_healer.cli.banner)."""

from __future__ import annotations

import io
import os
import re
from unittest.mock import MagicMock

import pytest

from photo_healer import __version__
from photo_healer.cli.banner import (
    DEFAULT_BANNER_WIDTH,
    get_banner_text,
    should_enable_color,
    show_banner,
    strip_ansi,
    visible_len,
)
from photo_healer.cli.main import main
from tests.helpers import JPEGTestKit


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


# ── Group 1: Geometry, Line Count & Character Palette ─────────────────────────
class TestBannerGeometryAndFormatting:
    """Verifies box dimensions, line counts, and Unicode Box Drawing character fidelity."""

    def test_banner_exact_line_count(self):
        """Banner must strictly contain at most 7 lines to avoid crowding terminal output."""
        banner = get_banner_text(color=False)
        lines = banner.splitlines()
        assert len(lines) == 7, f"Banner must have exactly 7 lines, got {len(lines)}"

    def test_banner_box_drawing_characters(self):
        """Banner must utilize rounded box drawing characters (╭─╮│╰─╯) and horizontal divider."""
        banner = get_banner_text(color=False)
        lines = banner.splitlines()

        # Top border
        assert lines[0].startswith("╭─")
        assert lines[0].endswith("╮")

        # Vertical borders
        for mid_line in (lines[1], lines[2], lines[4], lines[5]):
            assert mid_line.startswith("│")
            assert mid_line.endswith("│")

        # Divider line
        assert lines[3].startswith("├")
        assert lines[3].endswith("┤")

        # Bottom border
        assert lines[6].startswith("╰")
        assert lines[6].endswith("╯")

    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize("color", [False, True])
    def test_visual_width_fits_80_columns(self, lang: str, color: bool):
        """Every line must maintain an exact visual width of 76 columns without wrapping on 80-col terminals."""
        banner = get_banner_text(lang=lang, color=color, width=DEFAULT_BANNER_WIDTH)
        lines = banner.splitlines()

        for idx, line in enumerate(lines, start=1):
            line_vlen = visible_len(line)
            assert line_vlen == DEFAULT_BANNER_WIDTH, (
                f"Line {idx} visual width is {line_vlen}, expected {DEFAULT_BANNER_WIDTH} (lang={lang}, color={color})"
            )
            assert line_vlen <= 80, f"Line {idx} exceeds 80 columns: {line_vlen}"


# ── Group 2: Content, Metadata & Bilingual Support ────────────────────────────
class TestBannerContentAndBilingual:
    """Verifies display of version, forensic engine status, license, author, and repository link."""

    def test_version_rendering_in_title(self):
        """Specified version must appear in the top title tag."""
        banner = get_banner_text(version="1.2.3", color=False)
        assert "Photo Healer v1.2.3" in banner

    def test_default_version_is_package_version(self):
        """When version is omitted, photo_healer.__version__ must be rendered."""
        banner = get_banner_text(color=False)
        assert f"Photo Healer v{__version__}" in banner

    def test_license_and_author_displayed(self):
        """Banner must display MIT license and author Fuheshka."""
        banner = get_banner_text(color=False)
        assert "MIT" in banner
        assert "Fuheshka" in banner

    def test_repository_url_displayed(self):
        """Banner must include repository URL."""
        banner = get_banner_text(color=False)
        assert "https://github.com/Fuheshka/photo-healer" in banner

    def test_russian_content_elements(self):
        """Russian banner must render Russian engine status, tagline, and repository label."""
        banner = get_banner_text(lang="ru", color=False)
        assert "Экспертиза и ремонт" in banner
        assert "Движок: готов (JPEG/TRIM)" in banner
        assert "Репозиторий:" in banner

    def test_english_content_elements(self):
        """English banner must render English engine status, tagline, and repository label."""
        banner = get_banner_text(lang="en", color=False)
        assert "Forensics & Recovery" in banner
        assert "Engine: ready (JPEG/TRIM)" in banner
        assert "Repository:" in banner


# ── Group 3: ANSI Color Control & Fallback Logic ──────────────────────────────
class TestBannerColorControl:
    """Verifies safe color handling, NO_COLOR standard, TERM=dumb, and pipe redirection."""

    def test_plain_banner_has_no_ansi_escapes(self):
        """When color=False, output must not contain any ANSI escape codes."""
        banner = get_banner_text(color=False)
        assert "\x1b" not in banner
        assert not ANSI_ESCAPE_RE.search(banner)

    def test_colored_banner_has_ansi_escapes(self):
        """When color=True, output must contain ANSI escape codes for styling."""
        banner = get_banner_text(color=True)
        assert "\x1b" in banner
        assert ANSI_ESCAPE_RE.search(banner)

    def test_should_enable_color_respects_no_color_env(self, monkeypatch: pytest.MonkeyPatch):
        """NO_COLOR environment variable must disable ANSI colors."""
        monkeypatch.setenv("NO_COLOR", "1")
        mock_tty = MagicMock()
        mock_tty.isatty.return_value = True

        assert not should_enable_color(mock_tty)

    def test_should_enable_color_respects_term_dumb(self, monkeypatch: pytest.MonkeyPatch):
        """TERM=dumb must disable ANSI colors."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        mock_tty = MagicMock()
        mock_tty.isatty.return_value = True

        assert not should_enable_color(mock_tty)

    def test_should_enable_color_disables_for_non_tty(self, monkeypatch: pytest.MonkeyPatch):
        """Non-TTY streams (file pipes, StringIO) must disable ANSI colors."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)

        string_io = io.StringIO()
        assert not should_enable_color(string_io)

    def test_should_enable_color_enables_for_clean_tty(self, monkeypatch: pytest.MonkeyPatch):
        """Interactive TTY with clean environment must enable ANSI colors."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")

        mock_tty = MagicMock()
        mock_tty.isatty.return_value = True
        assert should_enable_color(mock_tty)

    def test_show_banner_prints_to_custom_stream(self):
        """show_banner prints output directly to the specified stream."""
        buf = io.StringIO()
        show_banner(version="0.2.0", lang="ru", stream=buf, color=False)
        output = buf.getvalue()

        assert "Photo Healer v0.2.0" in output
        assert len(output.splitlines()) == 7


# ── Group 4: CLI Integration & --no-banner Flag ───────────────────────────────
class TestCliBannerIntegration:
    """Verifies banner output in CLI and suppression via --no-banner / --quiet."""

    @pytest.fixture
    def test_archive(self, tmp_path):
        """Create a minimal archive directory with a healthy JPEG."""
        archive = tmp_path / "archive"
        archive.mkdir()
        healthy_bytes = JPEGTestKit.minimal_donor_header() + JPEGTestKit.EOI
        (archive / "photo.jpg").write_bytes(healthy_bytes)
        return archive

    def test_cli_triage_shows_banner_by_default(self, test_archive, capsys):
        """Running triage command without flags outputs the banner."""
        code = main(["triage", str(test_archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "Photo Healer v" in captured.out
        assert "╭─" in captured.out

    def test_cli_triage_suppresses_banner_with_no_banner_flag(self, test_archive, capsys):
        """Running triage with --no-banner suppresses the banner."""
        code = main(["triage", str(test_archive), "--no-banner"])
        assert code == 0
        captured = capsys.readouterr()
        assert "╭─" not in captured.out
        assert "Photo Healer v" not in captured.out

    def test_cli_triage_suppresses_banner_with_preceding_no_banner_flag(self, test_archive, capsys):
        """Running photo-healer --no-banner triage suppresses the banner."""
        code = main(["--no-banner", "triage", str(test_archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "╭─" not in captured.out
        assert "Photo Healer v" not in captured.out

    def test_cli_triage_suppresses_banner_in_quiet_mode(self, test_archive, capsys):
        """Running triage with --quiet suppresses both banner and summary tables."""
        code = main(["triage", str(test_archive), "--quiet"])
        assert code == 0
        captured = capsys.readouterr()
        assert "╭─" not in captured.out
        assert "Photo Healer v" not in captured.out

    def test_cli_no_args_shows_banner_before_help(self, capsys):
        """Running CLI without args displays banner followed by help usage."""
        code = main([])
        assert code == 1
        captured = capsys.readouterr()
        assert "╭─" in captured.out
        assert "usage: photo-healer" in captured.out or "usage: photo-healer" in captured.err

    def test_strip_ansi_utility(self):
        """strip_ansi correctly removes color escape sequences."""
        colored = "\x1b[36mHello\x1b[0m \x1b[1;32mWorld\x1b[0m"
        assert strip_ansi(colored) == "Hello World"
