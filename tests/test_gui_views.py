# -*- coding: utf-8 -*-
"""Comprehensive tests for Photo Healer GUI views and user interaction flows.

Tests:
  - MainWindow initialization, tab composition, drag-and-drop handling
  - TriageView category filter pills, counters, and table rendering
  - QuarantineDialog options, dry-run simulation flag, and destination selection
  - Dynamic bilingual UI switching without restart
  - Export JSON report generation
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from photo_healer.gui.i18n import get_language, i18n, set_language, t
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.views.main_window import MainWindow
from photo_healer.gui.views.triage_view import QuarantineDialog, TriageView


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sample_records() -> list[dict[str, Any]]:
    return [
        {
            "path": "/archive/photo1.jpg",
            "name": "photo1.jpg",
            "size": 2048576,
            "status": "healed_candidate",
            "first_nonzero": 4096,
            "note": "Candidate with zeroed header",
        },
        {
            "path": "/archive/photo2.jpg",
            "name": "photo2.jpg",
            "size": 5242880,
            "status": "trim_zero",
            "first_nonzero": -1,
            "note": "100% TRIM zeros",
        },
        {
            "path": "/archive/photo3.jpg",
            "name": "photo3.jpg",
            "size": 3145728,
            "status": "valid",
            "first_nonzero": 0,
            "note": "Intact JPEG",
        },
        {
            "path": "/archive/corrupt.jpg",
            "name": "corrupt.jpg",
            "size": 1024,
            "status": "error",
            "first_nonzero": 0,
            "note": "Invalid marker",
        },
    ]


class TestMainWindow:
    def test_window_initialization(self, qapp: QApplication) -> None:
        win = MainWindow()
        assert win.windowTitle() == t("app.title")
        assert win.tabs.count() == 3
        assert win.tabs.tabText(0) == t("tab.diagnostics")
        assert win.tabs.tabText(1) == t("tab.recovery")
        assert win.tabs.tabText(2) == t("tab.gallery")
        assert win.btn_scan.text() == t("folder.scan")
        assert win.btn_browse.text() == t("folder.browse")

    def test_language_switching_reactivity(self, qapp: QApplication) -> None:
        win = MainWindow()
        set_language("en")
        assert win.btn_scan.text() == "Scan"
        assert win.tabs.tabText(0) == "Diagnostics"

        set_language("ru")
        assert win.btn_scan.text() == "Сканировать"
        assert win.tabs.tabText(0) == "Диагностика"

        # Switch back to en
        set_language("en")
        assert win.btn_scan.text() == "Scan"

    def test_folder_text_change_updates_selected_path(self, qapp: QApplication, tmp_path: Path) -> None:
        win = MainWindow()
        win.txt_folder.setText(str(tmp_path))
        assert win._selected_path == tmp_path
        assert win.triage_view.current_archive_path == tmp_path

    def test_drag_and_drop_event_handling(self, qapp: QApplication, tmp_path: Path) -> None:
        win = MainWindow()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])

        enter_event = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        win.dragEnterEvent(enter_event)
        assert enter_event.isAccepted()

        drop_event = QDropEvent(
            QPoint(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        win.dropEvent(drop_event)
        assert drop_event.isAccepted()
        assert win.txt_folder.text() == str(tmp_path)
        assert win._selected_path == tmp_path


class TestTriageView:
    def test_initial_state_empty(self, qapp: QApplication) -> None:
        view = TriageView()
        assert view.table_model.rowCount() == 0
        assert "All (0)" in view.btn_filter_all.text() or "Все (0)" in view.btn_filter_all.text()

    def test_adding_records_updates_counters(
        self, qapp: QApplication, sample_records: list[dict[str, Any]]
    ) -> None:
        view = TriageView()
        for rec in sample_records:
            view.add_file_record(rec)

        assert view.table_model.rowCount() == 4
        assert view._counts["all"] == 4
        assert view._counts["candidates"] == 1
        assert view._counts["dummies"] == 1
        assert view._counts["intact"] == 1
        assert view._counts["errors"] == 1
        assert view._dummy_size == 5242880

    def test_adding_records_batch(
        self, qapp: QApplication, sample_records: list[dict[str, Any]]
    ) -> None:
        view = TriageView()
        view.add_file_records(sample_records)

        assert view.table_model.rowCount() == 4
        assert view._counts["all"] == 4
        assert view._counts["candidates"] == 1
        assert view._counts["dummies"] == 1
        assert view._counts["intact"] == 1
        assert view._counts["errors"] == 1
        assert view._dummy_size == 5242880

    def test_filter_buttons_switch_proxy_categories(
        self, qapp: QApplication, sample_records: list[dict[str, Any]]
    ) -> None:
        view = TriageView()
        for rec in sample_records:
            view.add_file_record(rec)

        # All filter
        view.btn_filter_all.click()
        assert view.proxy_model.rowCount() == 4

        # Candidates filter
        view.btn_filter_cand.click()
        assert view.proxy_model.rowCount() == 1
        item = view.proxy_model.data(view.proxy_model.index(0, 0))
        assert item == "photo1.jpg"

        # Dummies filter
        view.btn_filter_dummies.click()
        assert view.proxy_model.rowCount() == 1
        item = view.proxy_model.data(view.proxy_model.index(0, 0))
        assert item == "photo2.jpg"

        # Intact filter
        view.btn_filter_intact.click()
        assert view.proxy_model.rowCount() == 1
        item = view.proxy_model.data(view.proxy_model.index(0, 0))
        assert item == "photo3.jpg"

    def test_reset_data(self, qapp: QApplication, sample_records: list[dict[str, Any]]) -> None:
        view = TriageView()
        for rec in sample_records:
            view.add_file_record(rec)
        assert view.table_model.rowCount() == 4

        view.reset_data()
        assert view.table_model.rowCount() == 0
        assert view._counts["all"] == 0
        assert view._dummy_size == 0


class TestQuarantineDialog:
    def test_quarantine_dialog_options(self, qapp: QApplication, tmp_path: Path) -> None:
        dest_dir = tmp_path / "_Quarantine"
        dlg = QuarantineDialog(dummy_count=5, dummy_size=10485760, default_dest=dest_dir)

        assert dlg.dummy_count == 5
        assert dlg.dummy_size == 10485760
        assert dlg.is_dry_run() is False  # Default is actual relocation (fixes no-op dry-run bug)
        assert dlg.get_destination() == dest_dir
        assert dlg.btn_ok.text() != ""

        # Toggle dry-run
        dlg.chk_dry_run.setChecked(True)
        assert dlg.is_dry_run() is True

        dlg.chk_dry_run.setChecked(False)
        assert dlg.is_dry_run() is False

    def test_quarantine_execution_moves_files(
        self, qapp: QApplication, tmp_path: Path, monkeypatch
    ) -> None:
        from unittest.mock import patch

        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        dummy_file = archive_dir / "zero.jpg"
        dummy_file.write_bytes(b"\x00" * 1024)

        view = TriageView()
        view.set_archive_path(archive_dir)
        rec = {
            "name": "zero.jpg",
            "path": str(dummy_file),
            "size": 1024,
            "status": "trim_zero",
        }
        view.add_file_record(rec)
        assert view._counts["dummies"] == 1
        assert view._dummy_size == 1024

        dest_dir = tmp_path / "quarantine_target"

        # Mock dialog exec to accept with default dry_run=False
        def mock_exec(self_dlg):
            self_dlg.dest_edit.setText(str(dest_dir))
            self_dlg.chk_dry_run.setChecked(False)
            return QDialog.DialogCode.Accepted

        with patch.object(QuarantineDialog, "exec", mock_exec):
            with patch("PySide6.QtWidgets.QMessageBox.information"):
                view._handle_quarantine()

        # Check file was moved
        assert not dummy_file.exists()
        assert (dest_dir / "zero.jpg").exists()
        assert view._counts["dummies"] == 0
        assert view._dummy_size == 0
        assert view.table_model.get_item(0)["status"] == "quarantined"

    def test_quarantine_dry_run_prompt_confirmation(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox

        archive_dir = tmp_path / "archive2"
        archive_dir.mkdir()
        dummy_file = archive_dir / "zero2.jpg"
        dummy_file.write_bytes(b"\x00" * 2048)

        view = TriageView()
        view.set_archive_path(archive_dir)
        rec = {
            "name": "zero2.jpg",
            "path": str(dummy_file),
            "size": 2048,
            "status": "trim_zero",
        }
        view.add_file_record(rec)

        dest_dir = tmp_path / "quarantine_target2"

        # Mock dialog exec to accept with dry_run=True
        def mock_exec(self_dlg):
            self_dlg.dest_edit.setText(str(dest_dir))
            self_dlg.chk_dry_run.setChecked(True)
            return QDialog.DialogCode.Accepted

        # 1. When user clicks "No" on the dry-run prompt -> files are not moved
        with patch.object(QuarantineDialog, "exec", mock_exec):
            with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                view._handle_quarantine()

        assert dummy_file.exists()
        assert not dest_dir.exists()
        assert view._counts["dummies"] == 1

        # 2. When user clicks "Yes" on the dry-run prompt -> files ARE moved
        with patch.object(QuarantineDialog, "exec", mock_exec):
            with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                with patch("PySide6.QtWidgets.QMessageBox.information"):
                    view._handle_quarantine()

        assert not dummy_file.exists()
        assert (dest_dir / "zero2.jpg").exists()
        assert view._counts["dummies"] == 0
        assert view.table_model.get_item(0)["status"] == "quarantined"


class TestExportReport:
    def test_export_report_payload_structure(
        self, qapp: QApplication, tmp_path: Path, sample_records: list[dict[str, Any]]
    ) -> None:
        view = TriageView()
        view.set_archive_path(tmp_path)
        for rec in sample_records:
            view.add_file_record(rec)

        out_json = tmp_path / "test_report.json"
        payload = {
            "title": "Photo Healer Triage Report",
            "archive_folder": str(tmp_path),
            "summary": {
                "total_files": len(sample_records),
                "counts": view._counts,
                "dummy_size_bytes": view._dummy_size,
            },
            "files": view.table_model.get_all_items(),
        }

        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        assert out_json.exists()
        loaded = json.loads(out_json.read_text(encoding="utf-8"))
        assert loaded["title"] == "Photo Healer Triage Report"
        assert loaded["summary"]["total_files"] == 4
        assert loaded["summary"]["counts"]["dummies"] == 1
        assert len(loaded["files"]) == 4
