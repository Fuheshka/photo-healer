# -*- coding: utf-8 -*-
"""High-contrast Box Drawing terminal splash screen and ASCII banner for Photo Healer.

Complies with the ascii-banner-designer standard:
- Compact vertical layout (7 lines maximum)
- Rounded box corners (╭─╮│╰─╯)
- High contrast mini-block typography
- Version, forensic engine status, license, author, and repository URL
- Safe ANSI color rendering with auto-fallback (NO_COLOR, TERM=dumb, non-TTY pipes)
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

from photo_healer import __version__
from photo_healer.cli.i18n import ensure_windows_utf8, get_language

# Ensure Windows console supports UTF-8 box drawing characters
ensure_windows_utf8()

ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
DEFAULT_BANNER_WIDTH = 76


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_STRIP_RE.sub("", text)


def visible_len(text: str) -> int:
    """Calculate the visible character length of a string, ignoring ANSI escape codes."""
    return len(strip_ansi(text))


def should_enable_color(stream: Any = sys.stdout) -> bool:
    """Determine if ANSI color codes should be used for terminal output.

    Colors are disabled when:
    - NO_COLOR environment variable is set and non-empty (https://no-color.org)
    - TERM environment variable is set to 'dumb'
    - Output stream is not an interactive TTY (e.g. redirected to a file or pipe)
    """
    no_color = os.environ.get("NO_COLOR")
    if no_color is not None and no_color != "":
        return False

    if os.environ.get("TERM") == "dumb":
        return False

    if stream is None or not hasattr(stream, "isatty") or not stream.isatty():
        return False

    return True


def get_banner_text(
    version: str | None = None,
    lang: str = "ru",
    color: bool | None = None,
    width: int = DEFAULT_BANNER_WIDTH,
) -> str:
    """Generate the formatted Box Drawing ASCII banner text.

    Args:
        version: Application version string (defaults to photo_healer.__version__).
        lang: Interface language ('ru' or 'en', defaults to 'ru').
        color: Force enable/disable ANSI colors, or None to auto-detect.
        width: Total width in characters including borders (defaults to 76).

    Returns:
        Multi-line banner string (exactly 7 lines).
    """
    if version is None:
        version = __version__

    if lang is None:
        lang = get_language()

    is_ru = lang.lower().startswith("ru")

    if color is None:
        color = should_enable_color(sys.stdout)

    inner = width - 2

    # High-contrast 2-line mini block lettering for "PHOTO HEALER" (45 cols)
    p1 = "█▀█ █ █ █▀█ ▀█▀ █▀█   █ █ █▀▀ ▄▀█ █   █▀▀ █▀█"
    p2 = "█▀▀ █▀█ █▄█  █  █▄█   █▀█ ██▄ █▀█ █▄▄ ██▄ █▀▄"

    # ANSI styles
    CYAN = "\x1b[36m" if color else ""
    BOLD_CYAN = "\x1b[1;36m" if color else ""
    BOLD_WHITE = "\x1b[1;37m" if color else ""
    BOLD_GREEN = "\x1b[1;32m" if color else ""
    DIM = "\x1b[90m" if color else ""
    RESET = "\x1b[0m" if color else ""

    if is_ru:
        top_title = f" Photo Healer v{version} "
        r1 = "Экспертиза и ремонт"
        r2 = "SSD TRIM Forensics"
        stat_prefix = "  Движок: "
        stat_val = "готов (JPEG/TRIM)"
        stat_mid = "  •  Лицензия: "
        stat_lic = "MIT"
        stat_author = "  •  Автор: Fuheshka"
        repo_prefix = "  Репозиторий: "
        repo_val = "https://github.com/Fuheshka/photo-healer"
    else:
        top_title = f" Photo Healer v{version} "
        r1 = "Forensics & Recovery"
        r2 = "SSD TRIM Forensics"
        stat_prefix = "  Engine: "
        stat_val = "ready (JPEG/TRIM)"
        stat_mid = "  •  License: "
        stat_lic = "MIT"
        stat_author = "  •  Author: Fuheshka"
        repo_prefix = "  Repository: "
        repo_val = "https://github.com/Fuheshka/photo-healer"

    # Line 1: Top border with title tag
    border_fill = "─" * (inner - len(top_title) - 1)
    line1 = f"{CYAN}╭─{RESET}{BOLD_CYAN}{top_title}{RESET}{CYAN}{border_fill}╮{RESET}"

    # Line 2: Logo row 1 + tagline
    pad1 = inner - 2 - len(p1) - len(r1) - 2
    spacing1 = " " * max(pad1, 1)
    line2 = f"{CYAN}│{RESET}  {BOLD_CYAN}{p1}{RESET}{spacing1}{BOLD_WHITE}{r1}{RESET}  {CYAN}│{RESET}"

    # Line 3: Logo row 2 + subtitle
    pad2 = inner - 2 - len(p2) - len(r2) - 2
    spacing2 = " " * max(pad2, 1)
    line3 = f"{CYAN}│{RESET}  {BOLD_CYAN}{p2}{RESET}{spacing2}{DIM}{r2}{RESET}  {CYAN}│{RESET}"

    # Line 4: Inner horizontal divider
    inner_bar = "─" * inner
    line4 = f"{CYAN}├{inner_bar}┤{RESET}"

    # Line 5: Engine status, license, and author
    stat_plain = f"{stat_prefix}{stat_val}{stat_mid}{stat_lic}{stat_author}"
    pad_stat = " " * (inner - len(stat_plain))
    line5 = (
        f"{CYAN}│{RESET}"
        f"{stat_prefix}{BOLD_GREEN}{stat_val}{RESET}"
        f"{DIM}{stat_mid}{RESET}{stat_lic}"
        f"{DIM}{stat_author}{RESET}"
        f"{pad_stat}{CYAN}│{RESET}"
    )

    # Line 6: Repository URL
    repo_plain = f"{repo_prefix}{repo_val}"
    pad_repo = " " * (inner - len(repo_plain))
    line6 = (
        f"{CYAN}│{RESET}"
        f"{repo_prefix}{CYAN}{repo_val}{RESET}"
        f"{pad_repo}{CYAN}│{RESET}"
    )

    # Line 7: Bottom border
    line7 = f"{CYAN}╰{inner_bar}╯{RESET}"

    lines = [line1, line2, line3, line4, line5, line6, line7]
    return "\n".join(lines)


def show_banner(
    version: str | None = None,
    lang: str = "ru",
    stream: Any = None,
    color: bool | None = None,
) -> None:
    """Print the Photo Healer ASCII banner to the specified stream.

    Args:
        version: Application version string (defaults to photo_healer.__version__).
        lang: Interface language ('ru' or 'en', defaults to 'ru').
        stream: Target text stream (defaults to sys.stdout).
        color: Force enable/disable ANSI colors, or None to auto-detect from stream and env.
    """
    if stream is None:
        stream = sys.stdout

    if color is None:
        color = should_enable_color(stream)

    banner_text = get_banner_text(version=version, lang=lang, color=color)
    print(banner_text, file=stream)
