# -*- coding: utf-8 -*-
"""Application icon resolution utilities for Photo Healer GUI.

Provides reliable cross-environment icon resolution across local development,
wheel installation, and PyInstaller frozen executable bundles (sys._MEIPASS).
"""

from __future__ import annotations

import sys
from pathlib import Path
from PySide6.QtGui import QIcon


def get_app_icon_path() -> Path | None:
    """Resolve path to the application icon across development and PyInstaller runtime."""
    # 1. PyInstaller bundled resources (sys._MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass_icon = Path(sys._MEIPASS) / "assets" / "icon.png"
        if meipass_icon.is_file():
            return meipass_icon

    # 2. Local development source tree: repo root / assets / icon.png
    dev_icon = Path(__file__).resolve().parents[3] / "assets" / "icon.png"
    if dev_icon.is_file():
        return dev_icon

    # 3. Fallback: current working directory
    cwd_icon = Path.cwd() / "assets" / "icon.png"
    if cwd_icon.is_file():
        return cwd_icon

    return None


def get_app_icon() -> QIcon:
    """Return QIcon loaded from resolved icon path, or empty QIcon if not found."""
    path = get_app_icon_path()
    if path and path.is_file():
        return QIcon(str(path))
    return QIcon()
