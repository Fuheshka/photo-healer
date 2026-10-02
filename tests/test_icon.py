# -*- coding: utf-8 -*-
"""Unit tests for Photo Healer Apple HIG icon generation and resolution."""

from __future__ import annotations

import struct
from pathlib import Path
from PIL import Image
from PySide6.QtWidgets import QApplication

from photo_healer.gui.icon import get_app_icon, get_app_icon_path


def test_master_png_integrity() -> None:
    """Verify assets/icon.png is 1024x1024 RGBA with transparent corners."""
    icon_path = Path("assets/icon.png")
    assert icon_path.is_file(), "assets/icon.png does not exist"

    with Image.open(icon_path) as im:
        assert im.format == "PNG"
        assert im.size == (1024, 1024)
        assert im.mode == "RGBA"
        # Corner pixels should have 0 or very faint alpha outside the squircle
        top_left_alpha = im.getpixel((10, 10))[3]
        assert top_left_alpha == 0, f"Expected transparent corner, got alpha {top_left_alpha}"
        # Center should be completely opaque
        center_alpha = im.getpixel((512, 512))[3]
        assert center_alpha == 255, f"Expected opaque center, got alpha {center_alpha}"


def test_windows_ico_layers() -> None:
    """Verify assets/icon.ico contains 7 standard Windows resolution layers (16 to 256)."""
    ico_path = Path("assets/icon.ico")
    assert ico_path.is_file(), "assets/icon.ico does not exist"

    with open(ico_path, "rb") as f:
        header = f.read(6)
        reserved, ico_type, count = struct.unpack("<HHH", header)
        assert reserved == 0
        assert ico_type == 1  # 1 = ICO
        assert count == 7, f"Expected 7 ICO layers, found {count}"

        expected_sizes = [16, 24, 32, 48, 64, 128, 256]
        actual_sizes = []
        for _ in range(count):
            entry = f.read(16)
            width, height, _, _, _, bpp, _, _ = struct.unpack("<BBBBHHII", entry)
            w = 256 if width == 0 else width
            h = 256 if height == 0 else height
            assert w == h
            assert bpp == 32
            actual_sizes.append(w)

        assert actual_sizes == expected_sizes, f"Layers mismatch: {actual_sizes} != {expected_sizes}"


def test_icon_resolution_dev_and_meipass(monkeypatch) -> None:
    """Verify get_app_icon_path resolves in development and PyInstaller _MEIPASS bundle."""
    # Development mode
    dev_path = get_app_icon_path()
    assert dev_path is not None
    assert dev_path.name == "icon.png"
    assert dev_path.is_file()

    # PyInstaller bundle simulation
    fake_meipass = Path("assets").resolve().parent
    monkeypatch.setattr("sys._MEIPASS", str(fake_meipass), raising=False)
    bundle_path = get_app_icon_path()
    assert bundle_path is not None
    assert bundle_path.is_file()


def test_get_app_icon_qicon() -> None:
    """Verify get_app_icon returns non-null QIcon."""
    app = QApplication.instance() or QApplication([])
    icon = get_app_icon()
    assert not icon.isNull()
