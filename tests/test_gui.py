# -*- coding: utf-8 -*-
"""Comprehensive tests for Photo Healer GUI components, models, i18n, worker, and CLI.

Covers:
  - FileTableModel logic (sorting, filtering, Qt data roles)
  - i18n localization dictionaries and reactive language switching
  - CLI subcommand 'photo-healer gui' (argument validation, missing PySide6 handling)
  - CarveWorker background preview extractor (non-blocking QThread, signals, cancellation)
  - CarveView visual gallery grid (card rendering, filtering, export selected & all)
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication

from photo_healer.gui.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    I18nManager,
    get_language,
    i18n,
    set_language,
    t,
)
from photo_healer.gui.models.file_table_model import (
    COLUMN_KEYS,
    FileFilterProxyModel,
    FileTableModel,
    format_size,
)
from tests.helpers import JPEGTestKit

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Ensure a QApplication exists for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def reset_locale():
    """Reset language to en after each test."""
    initial = get_language()
    yield
    set_language(initial)


@pytest.fixture
def sample_file_items() -> list[dict[str, Any]]:
    return [
        {
            "path": "/archive/damaged_01.jpg",
            "name": "damaged_01.jpg",
            "size": 5242880,  # 5 MB
            "status": "healed_candidate",
            "first_nonzero": 4096,
            "note": "Leading zeros",
        },
        {
            "path": "/archive/zero_dummy.jpg",
            "name": "zero_dummy.jpg",
            "size": 2097152,  # 2 MB
            "status": "trim_zero",
            "first_nonzero": -1,
            "note": "100% TRIM-erased zeros",
        },
        {
            "path": "/archive/intact_photo.jpg",
            "name": "intact_photo.jpg",
            "size": 1048576,  # 1 MB
            "status": "valid",
            "first_nonzero": 0,
            "note": "Healthy JPEG",
        },
        {
            "path": "/archive/corrupted_header.jpg",
            "name": "corrupted_header.jpg",
            "size": 512000,  # 500 KB
            "status": "error",
            "first_nonzero": 0,
            "note": "Unknown marker",
        },
    ]


# ── 1. FileTableModel & Proxy Model Tests ─────────────────────────────────────
class TestFileTableModelLogic:
    """Tests for FileTableModel data roles, population, and FileFilterProxyModel sorting & filtering."""

    def test_model_data_roles(self, sample_file_items):
        set_language("en")
        model = FileTableModel()
        model.add_items(sample_file_items)

        assert model.rowCount() == 4
        assert model.columnCount() == 4

        # Col 0: Filename
        idx_name = model.index(0, 0)
        assert model.data(idx_name, Qt.ItemDataRole.DisplayRole) == "damaged_01.jpg"

        # Col 1: Status (localized)
        idx_status = model.index(0, 1)
        assert model.data(idx_status, Qt.ItemDataRole.DisplayRole) == "Heal candidate"
        # ForegroundRole for status
        brush = model.data(idx_status, Qt.ItemDataRole.ForegroundRole)
        assert isinstance(brush, QBrush)
        assert brush.color().name().lower() == "#22c55e"

        # Col 2: Size (formatted)
        idx_size = model.index(0, 2)
        assert model.data(idx_size, Qt.ItemDataRole.DisplayRole) == "5.00 MB"
        # AlignmentRole for size
        align = model.data(idx_size, Qt.ItemDataRole.TextAlignmentRole)
        assert (align & int(Qt.AlignmentFlag.AlignRight)) != 0

        # Col 3: Path
        idx_path = model.index(0, 3)
        assert model.data(idx_path, Qt.ItemDataRole.DisplayRole) == "/archive/damaged_01.jpg"

        # UserRole returns full dict
        user_data = model.data(idx_name, Qt.ItemDataRole.UserRole)
        assert user_data["name"] == "damaged_01.jpg"
        assert user_data["first_nonzero"] == 4096

    def test_model_item_manipulation(self, sample_file_items):
        model = FileTableModel()
        assert model.rowCount() == 0

        model.add_item(sample_file_items[0])
        assert model.rowCount() == 1
        assert model.get_item(0)["name"] == "damaged_01.jpg"
        assert model.get_item(99) is None

        all_items = model.get_all_items()
        assert len(all_items) == 1

        model.clear()
        assert model.rowCount() == 0
        assert len(model.get_all_items()) == 0

    def test_proxy_category_filtering(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(model)

        # Default: all
        proxy.set_category_filter("all")
        assert proxy.rowCount() == 4

        # Candidates
        proxy.set_category_filter("candidates")
        assert proxy.rowCount() == 1
        assert proxy.data(proxy.index(0, 0)) == "damaged_01.jpg"

        # Dummies
        proxy.set_category_filter("dummies")
        assert proxy.rowCount() == 1
        assert proxy.data(proxy.index(0, 0)) == "zero_dummy.jpg"

        # Intact
        proxy.set_category_filter("intact")
        assert proxy.rowCount() == 1
        assert proxy.data(proxy.index(0, 0)) == "intact_photo.jpg"

        # Errors
        proxy.set_category_filter("errors")
        assert proxy.rowCount() == 1
        assert proxy.data(proxy.index(0, 0)) == "corrupted_header.jpg"

    def test_proxy_numeric_and_text_sorting(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(model)

        # Sort by Size (Col 2) Ascending
        proxy.sort(2, Qt.SortOrder.AscendingOrder)
        assert proxy.data(proxy.index(0, 0)) == "corrupted_header.jpg"  # 500 KB
        assert proxy.data(proxy.index(3, 0)) == "damaged_01.jpg"        # 5 MB

        # Sort by Size Descending
        proxy.sort(2, Qt.SortOrder.DescendingOrder)
        assert proxy.data(proxy.index(0, 0)) == "damaged_01.jpg"        # 5 MB
        assert proxy.data(proxy.index(3, 0)) == "corrupted_header.jpg"  # 500 KB

        # Sort by Name (Col 0) Ascending
        proxy.sort(0, Qt.SortOrder.AscendingOrder)
        assert proxy.data(proxy.index(0, 0)) == "corrupted_header.jpg"
        assert proxy.data(proxy.index(3, 0)) == "zero_dummy.jpg"


# ── 2. GUI Localization and Language Switching Tests ─────────────────────────
class TestGuiLocalizationAndLanguageSwitching:
    """Tests for i18n dictionaries, language toggling, and signal propagation."""

    def test_gui_translations_symmetry(self):
        """Ensure RU and EN dictionaries have core parity for GUI keys."""
        en_keys = set(TRANSLATIONS["en"].keys())
        ru_keys = set(TRANSLATIONS["ru"].keys())

        # Test essential keys present in both
        essential_keys = [
            "app.title",
            "tab.diagnostics",
            "tab.recovery",
            "tab.gallery",
            "folder.label",
            "folder.scan",
            "folder.browse",
            "status.healed_candidate",
            "status.trim_zero",
            "status.valid",
        ]
        for key in essential_keys:
            assert key in en_keys, f"Missing key in EN: {key}"
            assert key in ru_keys, f"Missing key in RU: {key}"

    def test_language_switch_emits_signal_and_updates_translations(self):
        set_language("en")
        received_languages: list[str] = []
        i18n.language_changed.connect(received_languages.append)

        try:
            set_language("ru")
            assert get_language() == "ru"
            assert t("tab.gallery") == "Галерея превью"

            set_language("en")
            assert get_language() == "en"
            assert t("tab.gallery") == "Preview Gallery"

            assert "ru" in received_languages
            assert "en" in received_languages
        finally:
            i18n.language_changed.disconnect(received_languages.append)

    def test_translation_interpolation_and_fallback(self):
        set_language("en")
        res = t("metric.files", count=42)
        assert res == "Files: 42"

        # Unknown key returns key itself
        assert t("non_existent_key_xyz") == "non_existent_key_xyz"
        # Custom default
        assert t("non_existent_key_xyz", default="Fallback") == "Fallback"


# ── 3. CLI 'photo-healer gui' Subcommand Tests ─────────────────────────────────
class TestCliGuiSubcommand:
    """Tests for CLI 'photo-healer gui' argument parsing and missing dependency handling."""

    def test_gui_parser_registered(self):
        from photo_healer.cli.main import build_parser
        parser = build_parser(lang="en")
        args = parser.parse_args(["gui", "--folder", "/test/archive", "--lang", "ru"])
        assert args.command == "gui"
        assert args.folder == "/test/archive"
        assert args.lang == "ru"

    def test_gui_missing_pyside6_gives_clear_error_instructions(self, capsys):
        from photo_healer.cli.main import main as cli_main

        # Mock PySide6 as not installed
        with patch.dict(sys.modules, {"PySide6": None, "photo_healer.gui.app": None}):
            # Force import to fail
            with patch("builtins.__import__", side_effect=lambda name, *args, **kwargs: (
                sys.modules[name] if name != "PySide6" and not name.startswith("PySide6.")
                else (_ for _ in ()).throw(ImportError("No module named 'PySide6'"))
            )):
                code = cli_main(["gui", "--no-banner"])
                assert code == 1
                captured = capsys.readouterr()
                assert "pip install photo-healer[gui]" in captured.err or "pip install photo-healer[gui]" in captured.out

    def test_gui_successful_launch_delegation(self):
        from photo_healer.cli.main import main as cli_main

        mock_gui_app_main = MagicMock(return_value=0)
        with patch("photo_healer.gui.app.main", mock_gui_app_main):
            code = cli_main(["gui", "--folder", "C:/Photos", "--lang", "en", "--no-banner"])
            assert code == 0
            assert mock_gui_app_main.called
            called_args = mock_gui_app_main.call_args[0][0]
            assert "--folder" in called_args
            assert "C:/Photos" in called_args


# ── 4. CarveWorker Background Worker Tests ────────────────────────────────────
class TestCarveWorker:
    """Tests for CarveWorker asynchronous preview extraction."""

    def test_carve_worker_extracts_previews(self, tmp_path):
        from photo_healer.gui.workers.carve_worker import CarveWorker

        # Create a test JPEG with minimal donor header + valid JPEG
        valid_jpeg = JPEGTestKit.minimal_donor_header(width=320, height=240) + b"\x00" * 32 + JPEGTestKit.EOI
        p1 = tmp_path / "photo_01.jpg"
        p1.write_bytes(valid_jpeg)

        # Create damaged candidate that has carved stream
        p2 = tmp_path / "photo_02.jpg"
        p2.write_bytes(b"\x00" * 2048 + valid_jpeg)

        worker = CarveWorker(folder_path=tmp_path)
        found_previews: list[dict[str, Any]] = []
        progress_events: list[tuple[int, int, str]] = []
        summary_container: list[dict[str, Any]] = []

        worker.preview_found.connect(found_previews.append)
        worker.progress.connect(lambda c, t, f: progress_events.append((c, t, f)))
        worker.finished.connect(summary_container.append)

        # Run synchronously for test verification
        worker.run()

        assert len(summary_container) == 1
        summary = summary_container[0]
        assert summary["status"] == "completed"
        assert summary["scanned_files"] == 2
        assert len(found_previews) >= 1

        # Check preview metadata fields
        prev = found_previews[0]
        assert "source_path" in prev
        assert "source_name" in prev
        assert "preview_type" in prev
        assert "type_label" in prev
        assert "resolution" in prev
        assert "size_bytes" in prev
        assert "data" in prev
        assert isinstance(prev["data"], bytes)

    def test_carve_worker_stop_cancellation(self, tmp_path):
        from photo_healer.gui.workers.carve_worker import CarveWorker

        worker = CarveWorker(folder_path=tmp_path)
        worker.stop()
        summary_container: list[dict[str, Any]] = []
        worker.finished.connect(summary_container.append)
        worker.run()

        assert len(summary_container) == 1
        assert summary_container[0]["status"] == "cancelled"


# ── 5. CarveView Visual Gallery Grid Tests ────────────────────────────────────
class TestCarveView:
    """Tests for CarveView preview gallery grid, cards, and export operations."""

    def test_carve_view_initialization(self):
        from photo_healer.gui.views.carve_view import CarveView

        view = CarveView()
        assert view is not None
        assert view.btn_export_selected is not None
        assert view.btn_export_all is not None
        assert view.card_count() == 0

    def test_carve_view_card_addition_and_filtering(self):
        from photo_healer.gui.views.carve_view import CarveView

        view = CarveView()
        valid_jpeg = JPEGTestKit.minimal_donor_header(width=160, height=120) + b"\x00" * 16 + JPEGTestKit.EOI

        item1 = {
            "source_path": "/photos/IMG_001.jpg",
            "source_name": "IMG_001.jpg",
            "preview_type": "mpf",
            "type_label": "APP2 MPF Full HD",
            "resolution": "1920x1080",
            "size_bytes": 102400,
            "size_str": "100.0 KB",
            "data": valid_jpeg,
        }
        item2 = {
            "source_path": "/photos/IMG_002.jpg",
            "source_name": "IMG_002.jpg",
            "preview_type": "exif_thumb",
            "type_label": "APP1 EXIF Thumb",
            "resolution": "160x120",
            "size_bytes": 12288,
            "size_str": "12.0 KB",
            "data": valid_jpeg,
        }

        view.add_preview(item1)
        view.add_preview(item2)

        assert view.card_count() == 2

        # Select all and deselect
        view.select_all(True)
        assert len(view.get_selected_previews()) == 2

        view.select_all(False)
        assert len(view.get_selected_previews()) == 0

    def test_carve_view_export_operations(self, tmp_path):
        from photo_healer.gui.views.carve_view import CarveView

        view = CarveView()
        valid_jpeg = JPEGTestKit.minimal_donor_header(width=160, height=120) + b"\x00" * 16 + JPEGTestKit.EOI

        item = {
            "source_path": str(tmp_path / "source.jpg"),
            "source_name": "source.jpg",
            "preview_type": "mpf",
            "type_label": "APP2 MPF Full HD",
            "resolution": "1920x1080",
            "size_bytes": len(valid_jpeg),
            "size_str": format_size(len(valid_jpeg)),
            "data": valid_jpeg,
        }
        view.add_preview(item)
        view.select_all(True)

        dest_dir = tmp_path / "exported"
        exported = view.export_previews(view.get_selected_previews(), dest_dir)
        assert len(exported) == 1
        assert exported[0].exists()
        assert exported[0].read_bytes() == valid_jpeg
