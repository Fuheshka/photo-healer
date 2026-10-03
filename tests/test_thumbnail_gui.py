# -*- coding: utf-8 -*-
"""Comprehensive tests for ThumbnailWorker, ThumbnailFixDialog, and thumbnail GUI integration.

Tests:
  - ThumbnailWorker background thread execution (strip mode, rebuild mode, cancellation, cache flush)
  - ThumbnailFixDialog UI setup, folder prefilling, mode selection, and reactive bilingual retranslation
  - HealView integration with chk_strip_thumbnail checkbox and HealWorker parameter propagation
  - CarveView and MainWindow integration (menu bar items, toolbar button, action handlers)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photo_healer.core.thumbnail import clear_windows_thumbnail_cache
from photo_healer.gui.i18n import get_language, set_language, t
from photo_healer.gui.views.carve_view import CarveView
from photo_healer.gui.views.heal_view import HealView
from photo_healer.gui.views.main_window import MainWindow
from photo_healer.gui.views.thumbnail_dialog import ThumbnailFixDialog
from photo_healer.gui.workers.heal_worker import HealWorker
from photo_healer.gui.workers.thumbnail_worker import ThumbnailWorker
from tests.helpers import JPEGTestKit


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_jpeg_dir(tmp_path: Path) -> Path:
    """Creates a temporary folder containing valid JPEG files with donor thumbnails."""
    folder = tmp_path / "photos"
    folder.mkdir()

    # Create 3 synthetic JPEGs with donor thumbnails
    for i in range(1, 4):
        p = folder / f"photo_{i:02d}.jpg"
        header = JPEGTestKit.minimal_donor_header(128, 96)
        # Append mock thumbnail payload
        thumb_data = b"\xff\xd8\xff\xdb" + b"\x00" * 200 + b"\xff\xd9"
        app1_thumb = b"\xff\xe1" + len(thumb_data + b"Exif\x00\x00").to_bytes(2, "big") + b"Exif\x00\x00" + thumb_data
        full_content = header[:2] + app1_thumb + header[2:] + b"\x12\x34\x56" * 50 + JPEGTestKit.EOI
        p.write_bytes(full_content)

    return folder


class TestThumbnailWorker:
    """Tests for the background ThumbnailWorker thread."""

    def test_worker_strip_mode(self, qapp: QApplication, sample_jpeg_dir: Path) -> None:
        worker = ThumbnailWorker(folder_path=sample_jpeg_dir, mode="strip")
        processed_files: list[Any] = []
        progress_events: list[tuple[int, int, str]] = []
        finished_summary: list[dict[str, Any]] = []

        worker.file_processed.connect(processed_files.append)
        worker.progress.connect(lambda cur, tot, name: progress_events.append((cur, tot, name)))
        worker.finished.connect(finished_summary.append)

        # Run synchronously for test verification
        worker.run()

        assert len(finished_summary) == 1
        summary = finished_summary[0]
        assert summary["status"] == "completed"
        assert summary["total"] == 3
        assert summary["processed"] == 3
        assert len(processed_files) == 3
        assert len(progress_events) == 3

    def test_worker_rebuild_mode(self, qapp: QApplication, sample_jpeg_dir: Path) -> None:
        worker = ThumbnailWorker(folder_path=sample_jpeg_dir, mode="rebuild", size=(64, 48))
        finished_summary: list[dict[str, Any]] = []
        worker.finished.connect(finished_summary.append)

        worker.run()

        assert len(finished_summary) == 1
        summary = finished_summary[0]
        assert summary["status"] == "completed"
        assert summary["total"] == 3
        assert summary["processed"] == 3

    def test_worker_cancellation(self, qapp: QApplication, sample_jpeg_dir: Path) -> None:
        worker = ThumbnailWorker(folder_path=sample_jpeg_dir, mode="strip")
        finished_summary: list[dict[str, Any]] = []
        worker.finished.connect(finished_summary.append)

        # Stop immediately before run
        worker.stop()
        worker.run()

        assert len(finished_summary) == 1
        assert finished_summary[0]["status"] == "cancelled"

    def test_worker_empty_directory(self, qapp: QApplication, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        worker = ThumbnailWorker(folder_path=empty_dir, mode="strip")
        finished_summary: list[dict[str, Any]] = []
        worker.finished.connect(finished_summary.append)

        worker.run()

        assert len(finished_summary) == 1
        assert finished_summary[0]["total"] == 0
        assert finished_summary[0]["processed"] == 0

    def test_worker_clear_cache_option(self, qapp: QApplication, tmp_path: Path) -> None:
        worker = ThumbnailWorker(folder_path=tmp_path, mode="strip", clear_cache=True)
        cache_events: list[dict[str, Any]] = []
        worker.cache_cleared.connect(cache_events.append)

        worker.run()

        assert len(cache_events) == 1
        assert "success" in cache_events[0]


class TestThumbnailFixDialog:
    """Tests for the ThumbnailFixDialog view."""

    def test_dialog_initialization_defaults(self, qapp: QApplication) -> None:
        dlg = ThumbnailFixDialog()
        assert dlg.radio_strip.isChecked()
        assert not dlg.radio_rebuild.isChecked()
        assert not dlg.chk_backup.isChecked()
        assert not dlg.btn_start.isHidden()
        assert dlg.btn_stop.isHidden()
        assert dlg.progress_bar.isHidden()
        assert dlg.txt_folder.text() == ""
        dlg.close()

    def test_dialog_prefill_folder(self, qapp: QApplication, tmp_path: Path) -> None:
        dlg = ThumbnailFixDialog(target_folder=tmp_path)
        assert dlg.txt_folder.text() == str(tmp_path)
        assert dlg.target_folder == tmp_path
        dlg.close()

    def test_dialog_retranslation(self, qapp: QApplication, tmp_path: Path) -> None:
        dlg = ThumbnailFixDialog(target_folder=tmp_path)

        set_language("en")
        assert dlg.windowTitle() == "Fix Photo Previews & Thumbnails"
        assert dlg.btn_start.text() == "Start Repair"
        assert dlg.btn_reset_cache.text() == "Reset Windows Icon Cache"

        set_language("ru")
        assert dlg.windowTitle() == "Исправление превью и миниатюр"
        assert dlg.btn_start.text() == "Запустить исправление"
        assert dlg.btn_reset_cache.text() == "Сбросить кэш иконок Windows"

        dlg.close()

    def test_dialog_reset_cache_click(self, qapp: QApplication) -> None:
        dlg = ThumbnailFixDialog()
        with patch("photo_healer.gui.views.thumbnail_dialog.clear_windows_thumbnail_cache") as mock_cache:
            mock_cache.return_value = {"success": True, "actions": ["test"], "errors": []}
            with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
                dlg._on_reset_cache_clicked()
                mock_cache.assert_called_once()
                mock_info.assert_called_once()
        dlg.close()


class TestHealViewStripThumbnailSetting:
    """Tests for HealView strip_thumbnail checkbox and HealWorker propagation."""

    def test_checkbox_present_and_default_checked(self, qapp: QApplication) -> None:
        view = HealView()
        assert hasattr(view, "chk_strip_thumbnail")
        assert view.chk_strip_thumbnail.isChecked()
        assert view.chk_strip_thumbnail.toolTip() != ""

    def test_checkbox_bilingual_retranslation(self, qapp: QApplication) -> None:
        view = HealView()
        set_language("ru")
        assert "Удалять превью донора" in view.chk_strip_thumbnail.text()
        set_language("en")
        assert "Strip donor thumbnails" in view.chk_strip_thumbnail.text()

    def test_heal_worker_receives_strip_thumbnail(self, qapp: QApplication, tmp_path: Path) -> None:
        cand = tmp_path / "cand.jpg"
        cand.write_bytes(b"\x00" * 100)

        # Default: strip_thumbnail=True
        worker_default = HealWorker(candidates=[cand])
        assert worker_default.strip_thumbnail is True

        # Custom: strip_thumbnail=False
        worker_keep = HealWorker(candidates=[cand], strip_thumbnail=False)
        assert worker_keep.strip_thumbnail is False


class TestMainWindowAndCarveViewIntegration:
    """Tests for MenuBar and CarveView toolbar integration."""

    def test_main_window_menu_tools(self, qapp: QApplication) -> None:
        win = MainWindow()
        assert hasattr(win, "menu_tools")
        assert hasattr(win, "act_fix_previews")
        assert hasattr(win, "act_clear_cache")

        set_language("ru")
        assert win.menu_tools.title() == "Инструменты"
        assert win.act_fix_previews.text() == "Исправить превью в папке..."
        assert win.act_clear_cache.text() == "Сбросить кэш иконок Windows"

        set_language("en")
        assert win.menu_tools.title() == "Tools"
        assert win.act_fix_previews.text() == "Fix Previews in Folder..."
        assert win.act_clear_cache.text() == "Reset Windows Icon Cache"

    def test_carve_view_fix_previews_button(self, qapp: QApplication) -> None:
        carve = CarveView()
        assert hasattr(carve, "btn_fix_previews")

        set_language("ru")
        assert carve.btn_fix_previews.text() == "Исправить превью..."

        set_language("en")
        assert carve.btn_fix_previews.text() == "Fix Previews..."
