# -*- coding: utf-8 -*-
"""Unit tests for virtualized FileTableModel and FileFilterProxyModel."""

from __future__ import annotations

import os
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication

from photo_healer.gui.i18n import set_language, t
from photo_healer.gui.models.file_table_model import (
    COLUMN_KEYS,
    STATUS_COLORS,
    FileFilterProxyModel,
    FileTableModel,
    StatusBadgeDelegate,
    format_size,
)

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Ensure a GUI application instance exists for QBrush/QColor/Delegate tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def ensure_english_locale():
    """Set language to English for consistent assertions."""
    set_language("en")
    yield
    set_language("en")


@pytest.fixture
def sample_file_items() -> list[dict[str, Any]]:
    """Sample classified file dictionaries representing all categories."""
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
            "note": "100% TRIM erased",
        },
        {
            "path": "/archive/intact_photo.jpg",
            "name": "intact_photo.jpg",
            "size": 10485760,  # 10 MB
            "status": "valid",
            "first_nonzero": 0,
            "note": "Valid JPEG magic",
        },
        {
            "path": "/archive/corrupted.jpg",
            "name": "corrupted.jpg",
            "size": 1024,  # 1 KB
            "status": "error",
            "first_nonzero": 0,
            "note": "Read error",
        },
        {
            "path": "/archive/empty_file.jpg",
            "name": "empty_file.jpg",
            "size": 0,  # 0 B
            "status": "empty",
            "first_nonzero": -1,
            "note": "Zero length",
        },
        {
            "path": "/archive/doc.pdf",
            "name": "doc.pdf",
            "size": 51200,  # 50 KB
            "status": "other",
            "first_nonzero": 0,
            "note": "Not an image",
        },
    ]


class TestFormatSize:
    """Tests for format_size byte formatting utility."""

    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.00 KB"
        assert format_size(2048) == "2.00 KB"

    def test_megabytes(self):
        assert format_size(1048576) == "1.00 MB"
        assert format_size(5242880) == "5.00 MB"

    def test_gigabytes(self):
        assert format_size(1073741824) == "1.00 GB"


class TestFileTableModel:
    """Tests for FileTableModel virtualized rowCount, columnCount, data, and mutations."""

    def test_initial_state_empty(self):
        model = FileTableModel()
        assert model.rowCount() == 0
        assert model.columnCount() == 4
        assert model.get_all_items() == []
        assert model.get_item(0) is None

    def test_parent_index_returns_zero_rows_and_cols(self, sample_file_items):
        model = FileTableModel()
        model.add_item(sample_file_items[0])
        valid_parent = model.index(0, 0)
        assert valid_parent.isValid()
        assert model.rowCount(valid_parent) == 0
        assert model.columnCount(valid_parent) == 0

    def test_add_item_and_get_item(self, sample_file_items):
        model = FileTableModel()
        inserted_rows: list[tuple[int, int]] = []
        model.rowsInserted.connect(lambda parent, first, last: inserted_rows.append((first, last)))

        item0 = sample_file_items[0]
        model.add_item(item0)

        assert model.rowCount() == 1
        assert len(inserted_rows) == 1
        assert inserted_rows[0] == (0, 0)
        assert model.get_item(0) == item0
        assert model.get_item(1) is None
        assert model.get_item(-1) is None

    def test_add_items_batch(self, sample_file_items):
        model = FileTableModel()
        batch_events: list[tuple[int, int]] = []
        model.rowsInserted.connect(lambda parent, first, last: batch_events.append((first, last)))

        model.add_items(sample_file_items)

        assert model.rowCount() == len(sample_file_items)
        assert len(batch_events) == 1
        assert batch_events[0] == (0, len(sample_file_items) - 1)
        assert model.get_all_items() == sample_file_items

    def test_add_items_empty_noop(self):
        model = FileTableModel()
        batch_events = []
        model.rowsInserted.connect(lambda p, f, l: batch_events.append((f, l)))

        model.add_items([])
        assert model.rowCount() == 0
        assert len(batch_events) == 0

    def test_clear_model(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)
        assert model.rowCount() == 6

        reset_called = []
        model.modelReset.connect(lambda: reset_called.append(True))

        model.clear()
        assert model.rowCount() == 0
        assert len(reset_called) == 1
        assert model.get_all_items() == []

    def test_data_display_role(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        # Row 0: damaged_01.jpg, healed_candidate, 5 MB
        idx_name = model.index(0, 0)
        idx_status = model.index(0, 1)
        idx_size = model.index(0, 2)
        idx_path = model.index(0, 3)

        assert model.data(idx_name, Qt.ItemDataRole.DisplayRole) == "damaged_01.jpg"
        assert model.data(idx_status, Qt.ItemDataRole.DisplayRole) == t("status.healed_candidate")
        assert model.data(idx_size, Qt.ItemDataRole.DisplayRole) == format_size(5242880)
        assert model.data(idx_path, Qt.ItemDataRole.DisplayRole) == "/archive/damaged_01.jpg"

    def test_data_foreground_role(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        # Row 0: healed_candidate -> #22c55e
        idx_cand = model.index(0, 1)
        brush_cand: QBrush = model.data(idx_cand, Qt.ItemDataRole.ForegroundRole)
        assert isinstance(brush_cand, QBrush)
        assert brush_cand.color().name() == QColor(STATUS_COLORS["healed_candidate"]).name()

        # Row 1: trim_zero -> #ef4444
        idx_trim = model.index(1, 1)
        brush_trim: QBrush = model.data(idx_trim, Qt.ItemDataRole.ForegroundRole)
        assert brush_trim.color().name() == QColor(STATUS_COLORS["trim_zero"]).name()

        # Non-status column: default #f4f4f5
        idx_name = model.index(0, 0)
        brush_name: QBrush = model.data(idx_name, Qt.ItemDataRole.ForegroundRole)
        assert brush_name.color().name() == QColor("#f4f4f5").name()

    def test_data_alignment_role(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        idx_name = model.index(0, 0)
        idx_status = model.index(0, 1)
        idx_size = model.index(0, 2)
        idx_path = model.index(0, 3)

        assert model.data(idx_status, Qt.ItemDataRole.TextAlignmentRole) == int(Qt.AlignmentFlag.AlignCenter)
        assert model.data(idx_size, Qt.ItemDataRole.TextAlignmentRole) == int(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        assert model.data(idx_name, Qt.ItemDataRole.TextAlignmentRole) == int(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        assert model.data(idx_path, Qt.ItemDataRole.TextAlignmentRole) == int(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    def test_data_user_role(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        idx = model.index(0, 0)
        user_data = model.data(idx, Qt.ItemDataRole.UserRole)
        assert user_data == sample_file_items[0]

    def test_data_invalid_index(self, sample_file_items):
        model = FileTableModel()
        model.add_items(sample_file_items)

        invalid_idx = model.index(999, 999)
        assert model.data(invalid_idx, Qt.ItemDataRole.DisplayRole) is None
        assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None

    def test_header_data_horizontal(self):
        model = FileTableModel()
        set_language("en")

        headers = [
            model.headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for i in range(len(COLUMN_KEYS))
        ]
        assert headers == ["Filename", "Status", "Size", "Path"]

        # Switch to Russian and verify headers update
        set_language("ru")
        ru_headers = [
            model.headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for i in range(len(COLUMN_KEYS))
        ]
        assert ru_headers == ["Имя файла", "Статус", "Размер", "Путь"]

    def test_header_data_vertical_or_unhandled(self):
        model = FileTableModel()
        assert model.headerData(0, Qt.Orientation.Vertical, Qt.ItemDataRole.DisplayRole) is None
        assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.EditRole) is None
        assert model.headerData(99, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) is None


class TestFileFilterProxyModel:
    """Tests for FileFilterProxyModel category filtering and numeric sorting."""

    def test_default_filter_shows_all(self, sample_file_items):
        source = FileTableModel()
        source.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)

        assert proxy.get_category_filter() == "all"
        assert proxy.rowCount() == len(sample_file_items)

    def test_filter_candidates(self, sample_file_items):
        source = FileTableModel()
        source.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.set_category_filter("candidates")

        assert proxy.rowCount() == 1
        item = proxy.data(proxy.index(0, 0), Qt.ItemDataRole.UserRole)
        assert item["status"] == "healed_candidate"
        assert item["name"] == "damaged_01.jpg"

    def test_filter_dummies(self, sample_file_items):
        source = FileTableModel()
        source.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.set_category_filter("dummies")

        assert proxy.rowCount() == 1
        item = proxy.data(proxy.index(0, 0), Qt.ItemDataRole.UserRole)
        assert item["status"] == "trim_zero"
        assert item["name"] == "zero_dummy.jpg"

    def test_filter_intact(self, sample_file_items):
        source = FileTableModel()
        source.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.set_category_filter("intact")

        assert proxy.rowCount() == 1
        item = proxy.data(proxy.index(0, 0), Qt.ItemDataRole.UserRole)
        assert item["status"] == "valid"
        assert item["name"] == "intact_photo.jpg"

    def test_filter_errors_combines_error_empty_other(self, sample_file_items):
        source = FileTableModel()
        source.add_items(sample_file_items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.set_category_filter("errors")

        # corrupted.jpg (error), empty_file.jpg (empty), doc.pdf (other)
        assert proxy.rowCount() == 3
        statuses = [
            proxy.data(proxy.index(r, 0), Qt.ItemDataRole.UserRole)["status"]
            for r in range(proxy.rowCount())
        ]
        assert set(statuses) == {"error", "empty", "other"}

    def test_numeric_size_sorting_column_2(self):
        """Verify column 2 sorts by raw numeric byte size, NOT lexical string comparison."""
        items = [
            {"path": "/a.jpg", "name": "a.jpg", "size": 10485760, "status": "valid"},  # 10 MB ("10.00 MB")
            {"path": "/b.jpg", "name": "b.jpg", "size": 2048, "status": "valid"},      # 2 KB ("2.00 KB")
            {"path": "/c.jpg", "name": "c.jpg", "size": 512, "status": "valid"},       # 512 B ("512 B")
            {"path": "/d.jpg", "name": "d.jpg", "size": 1048576, "status": "valid"},   # 1 MB ("1.00 MB")
        ]
        source = FileTableModel()
        source.add_items(items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)

        # Lexically, "1.00 MB" < "10.00 MB" < "2.00 KB" < "512 B".
        # Numerically: 512 < 2048 < 1048576 < 10485760.
        proxy.sort(2, Qt.SortOrder.AscendingOrder)
        sorted_sizes_asc = [
            proxy.data(proxy.index(r, 0), Qt.ItemDataRole.UserRole)["size"]
            for r in range(proxy.rowCount())
        ]
        assert sorted_sizes_asc == [512, 2048, 1048576, 10485760]

        # Descending: 10485760 > 1048576 > 2048 > 512
        proxy.sort(2, Qt.SortOrder.DescendingOrder)
        sorted_sizes_desc = [
            proxy.data(proxy.index(r, 0), Qt.ItemDataRole.UserRole)["size"]
            for r in range(proxy.rowCount())
        ]
        assert sorted_sizes_desc == [10485760, 1048576, 2048, 512]

    def test_case_insensitive_name_sorting_column_0(self):
        items = [
            {"path": "/zeta.jpg", "name": "zeta.jpg", "size": 100, "status": "valid"},
            {"path": "/Alpha.jpg", "name": "Alpha.jpg", "size": 100, "status": "valid"},
            {"path": "/beta.jpg", "name": "beta.jpg", "size": 100, "status": "valid"},
        ]
        source = FileTableModel()
        source.add_items(items)

        proxy = FileFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.sort(0, Qt.SortOrder.AscendingOrder)

        sorted_names = [
            proxy.data(proxy.index(r, 0), Qt.ItemDataRole.DisplayRole)
            for r in range(proxy.rowCount())
        ]
        assert sorted_names == ["Alpha.jpg", "beta.jpg", "zeta.jpg"]


class TestStatusBadgeDelegate:
    """Verify StatusBadgeDelegate instantiation."""

    def test_delegate_initialization(self):
        delegate = StatusBadgeDelegate()
        assert delegate is not None
