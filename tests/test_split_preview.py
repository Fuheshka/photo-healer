# -*- coding: utf-8 -*-
"""Unit tests for SplitPreviewWidget and interactive comparison rendering."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from photo_healer.gui.widgets.split_preview import SplitPreviewWidget, pil_to_qimage
from tests.helpers import JPEGTestKit

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def synthetic_pair(tmp_path: Path) -> tuple[Path, Path]:
    donor_path = tmp_path / "donor.jpg"
    donor_path.write_bytes(JPEGTestKit.minimal_donor_header(width=320, height=240) + b"\x12\x34\x56" + JPEGTestKit.EOI)

    cand_path = tmp_path / "candidate.jpg"
    # Leading zero bytes followed by bitstream
    cand_bytes = (b"\x00" * 4096) + JPEGTestKit.minimal_donor_header(width=320, height=240) + b"\x55" * 100 + JPEGTestKit.EOI
    cand_path.write_bytes(cand_bytes)

    return cand_path, donor_path


class TestSplitPreviewWidget:
    """Test suite for SplitPreviewWidget rendering, caching, and zoom/pan."""

    def test_widget_initialization(self, qapp: QApplication):
        widget = SplitPreviewWidget()
        assert widget.get_view_mode() == "split"
        assert widget.get_scale_factor() == 1.0
        assert widget._split_ratio == 0.5
        assert widget._before_image is None
        assert widget._after_image is None

    def test_pil_to_qimage_conversion(self):
        pil_img = Image.new("RGB", (64, 48), color=(255, 0, 0))
        qimg = pil_to_qimage(pil_img)
        assert not qimg.isNull()
        assert qimg.width() == 64
        assert qimg.height() == 48

    def test_set_images_and_clear(self, qapp: QApplication):
        widget = SplitPreviewWidget()
        img1 = QImage(100, 100, QImage.Format.Format_RGB888)
        img2 = QImage(200, 200, QImage.Format.Format_RGB888)

        widget.set_images(img1, img2, before_info="Original", after_info="Reconstructed")
        assert widget._before_image is not None
        assert widget._after_image is not None
        assert widget._before_info == "Original"
        assert widget._after_info == "Reconstructed"

        widget.clear()
        assert widget._before_image is None
        assert widget._after_image is None
        assert widget._before_info == ""
        assert widget._after_info == ""

    def test_zoom_operations_and_clamping(self, qapp: QApplication):
        widget = SplitPreviewWidget()
        widget.resize(600, 400)

        # Zoom in
        initial_scale = widget.get_scale_factor()
        widget.zoom_in(2.0)
        assert widget.get_scale_factor() > initial_scale

        # Zoom out
        widget.zoom_out(0.25)
        assert widget.get_scale_factor() < initial_scale * 2.0

        # Reset zoom
        widget.reset_zoom()
        assert widget.get_scale_factor() == 1.0

        # Clamping upper bound
        for _ in range(30):
            widget.zoom_in(2.0)
        assert widget.get_scale_factor() <= 20.0

        # Clamping lower bound
        for _ in range(30):
            widget.zoom_out(0.5)
        assert widget.get_scale_factor() >= 0.05

    def test_split_ratio_clamping(self, qapp: QApplication):
        widget = SplitPreviewWidget()
        widget.set_split_ratio(0.75)
        assert widget._split_ratio == 0.75

        widget.set_split_ratio(-0.5)
        assert widget._split_ratio == 0.02

        widget.set_split_ratio(1.5)
        assert widget._split_ratio == 0.98

    def test_view_mode_switching_and_signals(self, qapp: QApplication):
        widget = SplitPreviewWidget()
        emitted_modes: list[str] = []
        widget.view_mode_changed.connect(emitted_modes.append)

        widget.set_view_mode("before")
        assert widget.get_view_mode() == "before"
        assert emitted_modes == ["before"]

        widget.set_view_mode("after")
        assert widget.get_view_mode() == "after"
        assert emitted_modes == ["before", "after"]

        widget.set_view_mode("split")
        assert widget.get_view_mode() == "split"
        assert emitted_modes == ["before", "after", "split"]

    def test_load_comparison_synthetic(self, qapp: QApplication, synthetic_pair: tuple[Path, Path]):
        cand_p, donor_p = synthetic_pair
        widget = SplitPreviewWidget()
        widget.resize(800, 600)

        ok, desc = widget.load_comparison(cand_p, donor_p, pad_geometry=False)
        assert ok is True
        assert widget._before_image is not None
        assert widget._after_image is not None
        assert widget._after_image.width() == 320
        assert widget._after_image.height() == 240

        # Verify caching
        assert len(widget._cache) >= 1
        widget.clear_cache()
        assert len(widget._cache) == 0

    def test_load_comparison_missing_file(self, qapp: QApplication, tmp_path: Path):
        widget = SplitPreviewWidget()
        ok, desc = widget.load_comparison(tmp_path / "non_existent.jpg")
        assert ok is False
        assert "File not found" in desc

    def test_diagnostic_card_generation(self, qapp: QApplication, tmp_path: Path):
        widget = SplitPreviewWidget()
        all_zeros = tmp_path / "zeros.jpg"
        all_zeros.write_bytes(b"\x00" * 65536)

        ok, desc = widget.load_comparison(all_zeros, donor_path=None)
        assert ok is False  # No donor
        assert widget._is_diagnostic_pattern is True
        assert widget._before_image is not None
        assert widget._before_image.width() == 1280
        assert widget._before_image.height() == 960

    def test_paint_event_modes_no_crash(self, qapp: QApplication, synthetic_pair: tuple[Path, Path]):
        cand_p, donor_p = synthetic_pair
        widget = SplitPreviewWidget()
        widget.resize(600, 400)
        widget.load_comparison(cand_p, donor_p)

        # Offscreen render in all modes
        modes = ["split", "before", "after"]
        for mode in modes:
            widget.set_view_mode(mode)
            pix = QPixmap(widget.size())
            pix.fill(Qt.GlobalColor.black)
            widget.render(pix)
            assert not pix.isNull()
