# -*- coding: utf-8 -*-
"""Comprehensive tests for Photo Healer HealView and user interaction flows.

Tests:
  - HealView initialization, splitter layout, and default widget states
  - Candidate queue operations (add, set, clear, duplicate detection)
  - Donor management (auto-donor resolution, manual drop, mode toggling)
  - Settings (pad_geometry, backup) and live preview trigger
  - View modes and zoom controls
  - Dynamic bilingual UI retranslation without restart
  - MainWindow integration (tab placement, triage double-click forwarding, archive_root propagation)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from photo_healer.gui.i18n import get_language, set_language, t
from photo_healer.gui.views.heal_view import DonorDropBox, HealView
from photo_healer.gui.views.main_window import MainWindow
from tests.helpers import JPEGTestKit


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_files(tmp_path: Path) -> dict[str, Path]:
    donor = tmp_path / "valid_donor.jpg"
    donor.write_bytes(JPEGTestKit.minimal_donor_header(64, 48) + b"\x12\x34\x56" + JPEGTestKit.EOI)

    cand1 = tmp_path / "damaged_01.jpg"
    cand1_data = (b"\x00" * 4096) + JPEGTestKit.minimal_donor_header(64, 48) + b"\x55" * 60 + JPEGTestKit.EOI
    cand1.write_bytes(cand1_data)

    cand2 = tmp_path / "damaged_02.jpg"
    cand2_data = (b"\x00" * 8192) + JPEGTestKit.minimal_donor_header(64, 48) + b"\x55" * 80 + JPEGTestKit.EOI
    cand2.write_bytes(cand2_data)

    return {
        "donor": donor,
        "cand1": cand1,
        "cand2": cand2,
    }


class TestHealViewInitialization:
    def test_default_ui_states(self, qapp: QApplication) -> None:
        view = HealView()
        assert view.splitter is not None
        assert view.list_candidates.count() == 0
        assert view.chk_auto_donor.isChecked() is True
        assert view.chk_pad_geometry.isChecked() is False
        assert view.chk_create_backup.isChecked() is True
        assert view.btn_mode_split.isChecked() is True
        assert view.batch_progress.isVisible() is False
        assert view.btn_stop_batch.isVisible() is False
        assert view.current_candidate is None
        assert view.manual_donor_path is None

    def test_donor_drop_box_init(self, qapp: QApplication) -> None:
        drop_card = DonorDropBox()
        assert drop_card.acceptDrops() is True
        assert drop_card.objectName() == "donorCard"


class TestCandidateQueueOperations:
    def test_add_and_clear_candidates(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        cand1 = test_files["cand1"]
        cand2 = test_files["cand2"]

        view.add_candidate(cand1, select=True)
        assert view.list_candidates.count() == 1
        assert view.current_candidate == cand1

        # Duplicate addition should be ignored
        view.add_candidate(cand1, select=True)
        assert view.list_candidates.count() == 1

        view.add_candidate(cand2, select=True)
        assert view.list_candidates.count() == 2
        assert view.current_candidate == cand2

        # Non-existent file should be ignored
        view.add_candidate("non_existent_file.jpg")
        assert view.list_candidates.count() == 2

        view.clear_candidates()
        assert view.list_candidates.count() == 0
        assert view.current_candidate is None

    def test_set_candidates_batch(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        items = [
            {"path": str(test_files["cand1"])},
            str(test_files["cand2"]),
        ]
        view.set_candidates(items)
        assert view.list_candidates.count() == 2
        assert view.current_candidate == test_files["cand1"]

    def test_selection_change_updates_preview(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        view.add_candidate(test_files["cand1"])
        view.add_candidate(test_files["cand2"])

        view.list_candidates.setCurrentRow(1)
        assert view.current_candidate == test_files["cand2"]

        view.list_candidates.clearSelection()
        view.list_candidates.setCurrentItem(None)
        assert view.current_candidate is None


class TestDonorManagement:
    def test_auto_donor_resolution(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        view.set_archive_path(test_files["donor"].parent)
        view.add_candidate(test_files["cand1"])

        donor = view.get_effective_donor()
        assert donor is not None
        assert donor == test_files["donor"]

    def test_manual_donor_drop(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        view.add_candidate(test_files["cand1"])

        # Manual donor drop overrides auto-donor
        view._on_donor_file_dropped(test_files["donor"])
        assert view.chk_auto_donor.isChecked() is False
        assert view.manual_donor_path == test_files["donor"]
        assert view.get_effective_donor() == test_files["donor"]

    def test_toggle_auto_donor_clears_manual(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        view._on_donor_file_dropped(test_files["donor"])
        assert view.manual_donor_path is not None

        # Re-enabling auto-donor should clear manual donor override
        view.chk_auto_donor.setChecked(True)
        assert view.manual_donor_path is None


class TestPreviewControlsAndSettings:
    def test_view_mode_toggles(self, qapp: QApplication) -> None:
        view = HealView()
        view.btn_mode_before.click()
        assert view.preview_widget._view_mode == "before"

        view.btn_mode_after.click()
        assert view.preview_widget._view_mode == "after"

        view.btn_mode_split.click()
        assert view.preview_widget._view_mode == "split"

    def test_zoom_buttons(self, qapp: QApplication) -> None:
        view = HealView()
        view.btn_zoom_in.click()
        assert view.preview_widget._scale_factor > 1.0

        view.btn_zoom_100.click()
        assert view.preview_widget._scale_factor == 1.0

        view.btn_zoom_fit.click()
        assert view.preview_widget._fit_mode is True

    def test_pad_geometry_toggle_reloads_preview(self, qapp: QApplication, test_files: dict[str, Path]) -> None:
        view = HealView()
        view.set_archive_path(test_files["donor"].parent)
        view.add_candidate(test_files["cand1"])

        # Toggle pad geometry
        view.chk_pad_geometry.setChecked(True)
        assert view.chk_pad_geometry.isChecked() is True
        assert "Pad: ON" in view.lbl_file_details.text()

        view.chk_pad_geometry.setChecked(False)
        assert "Pad: OFF" in view.lbl_file_details.text()


class TestBilingualRetranslation:
    def test_language_switch_updates_strings(self, qapp: QApplication) -> None:
        view = HealView()

        set_language("en")
        en_title = view.lbl_queue_title.text()
        assert en_title == "Candidate Queue"

        set_language("ru")
        ru_title = view.lbl_queue_title.text()
        assert ru_title == "Очередь кандидатов"


class TestMainWindowIntegration:
    def test_heal_view_mounted_in_tab(self, qapp: QApplication) -> None:
        window = MainWindow()
        assert window.tabs.count() >= 2
        assert window.tabs.widget(1) == window.heal_view
        assert window.recovery_tab == window.heal_view

    def test_archive_path_propagates_to_heal_view(self, qapp: QApplication, tmp_path: Path) -> None:
        window = MainWindow()
        window.txt_folder.setText(str(tmp_path))
        assert window.heal_view.archive_root == tmp_path

    def test_double_click_triage_candidate_switches_to_heal(
        self, qapp: QApplication, test_files: dict[str, Path]
    ) -> None:
        window = MainWindow()
        rec = {
            "path": str(test_files["cand1"]),
            "name": test_files["cand1"].name,
            "size": 2048,
            "status": "healed_candidate",
            "first_nonzero": 50,
            "note": "Test candidate",
        }
        window.triage_view.add_file_record(rec)
        assert window.triage_view.table_model.rowCount() == 1

        # Simulate double-click on proxy index (row 0)
        proxy_idx = window.triage_view.proxy_model.index(0, 0)
        window._on_triage_row_double_clicked(proxy_idx)

        assert window.tabs.currentIndex() == 1
        assert window.heal_view.current_candidate == test_files["cand1"]
