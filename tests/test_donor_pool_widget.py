# -*- coding: utf-8 -*-
"""Unit tests for DonorPoolWidget, DonorIndexWorker, and DonorDiscoverWorker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import os
import pytest
from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate QSettings and config directories so tests do not touch the Windows Registry or user home."""
    test_settings_dict: dict[str, object] = {}

    class MockQSettings:
        def __init__(self, *args, **kwargs):
            pass

        def setValue(self, key, value):
            test_settings_dict[key] = value

        def value(self, key, default=None):
            return test_settings_dict.get(key, default)

        def remove(self, key):
            test_settings_dict.pop(key, None)

        def sync(self):
            pass

        def clear(self):
            test_settings_dict.clear()

    monkeypatch.setattr("photo_healer.gui.widgets.donor_pool_widget.QSettings", MockQSettings)
    monkeypatch.setattr("photo_healer.gui.widgets.donor_pool_widget.SETTINGS_DIR", tmp_path)
    monkeypatch.setattr("photo_healer.gui.widgets.donor_pool_widget.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("photo_healer.gui.widgets.donor_pool_widget.DEFAULT_CACHE_POOL", tmp_path / "donor_pool.json")

from photo_healer.core.donor_discovery import DonorFolderCandidate
from photo_healer.core.donor_pool import DonorIndex
from photo_healer.gui.widgets.donor_pool_widget import (
    DEFAULT_CACHE_POOL,
    SETTINGS_FILE,
    DonorDropBox,
    DonorPoolWidget,
)
from photo_healer.gui.workers.donor_discover_worker import DonorDiscoverWorker
from photo_healer.gui.workers.donor_index_worker import DonorIndexWorker
from tests.helpers import JPEGTestKit


def create_donor_jpeg(path: Path, width: int = 640, height: int = 480) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = JPEGTestKit.minimal_donor_header(width=width, height=height)
    body = b"\x12\x34\x56\x78" * 50
    path.write_bytes(header + body + JPEGTestKit.EOI)


@pytest.fixture
def mock_pool_fs(tmp_path: Path):
    folder1 = tmp_path / "camera_sanyo"
    folder1.mkdir()
    create_donor_jpeg(folder1 / "SANY0001.JPG", 640, 480)
    create_donor_jpeg(folder1 / "SANY0002.JPG", 640, 480)

    folder2 = tmp_path / "camera_canon"
    folder2.mkdir()
    create_donor_jpeg(folder2 / "IMG_0001.JPG", 1920, 1080)

    cand_damaged = tmp_path / "damaged" / "SANY0099.JPG"
    cand_damaged.parent.mkdir()
    cand_damaged.write_bytes(b"\x00" * 2048 + JPEGTestKit.minimal_donor_header(640, 480) + b"\x55" * 40 + JPEGTestKit.EOI)

    return {
        "folder1": folder1,
        "folder2": folder2,
        "cand": cand_damaged,
    }


class TestDonorWorkers:
    """Tests for QThread background workers."""

    def test_donor_index_worker_execution(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        worker = DonorIndexWorker(
            folders=[mock_pool_fs["folder1"], mock_pool_fs["folder2"]],
            recursive=False,
        )

        progress_events = []
        indexed_events = []
        finished_results = []

        loop = QEventLoop()
        worker.progress.connect(lambda cur, tot, name: progress_events.append((cur, tot, name)))
        worker.folder_indexed.connect(lambda p, cnt: indexed_events.append((p, cnt)))

        def on_finished(idx):
            finished_results.append(idx)
            loop.quit()

        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        assert len(finished_results) == 1
        index: DonorIndex = finished_results[0]
        assert len(index) == 3
        assert len(indexed_events) == 2
        assert len(progress_events) == 2

    def test_donor_index_worker_stop(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        worker = DonorIndexWorker(
            folders=[mock_pool_fs["folder1"], mock_pool_fs["folder2"]],
        )
        worker.stop()
        assert worker.is_stopped is True

    def test_donor_discover_worker_execution(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        worker = DonorDiscoverWorker(
            root_dirs=[mock_pool_fs["folder1"].parent],
            max_depth=2,
        )

        found_candidates = []
        finished_candidates = []

        loop = QEventLoop()
        worker.folder_found.connect(found_candidates.append)

        def on_finished(cands):
            finished_candidates.append(cands)
            loop.quit()

        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        assert len(finished_candidates) == 1
        assert len(found_candidates) >= 2


class TestDonorPoolWidgetUI:
    """Tests for DonorPoolWidget UI interaction and modes."""

    def test_initial_ui_state(self, qapp: QApplication):
        widget = DonorPoolWidget()
        assert widget.current_mode in {"file", "folder", "auto"}
        assert widget.stack.count() == 3
        assert widget.lbl_compat_indicator is not None

    def test_mode_switching(self, qapp: QApplication):
        widget = DonorPoolWidget()

        widget.set_mode("file")
        assert widget.current_mode == "file"
        assert widget.stack.currentIndex() == 0
        assert widget.btn_mode_file.isChecked() is True

        widget.set_mode("folder")
        assert widget.current_mode == "folder"
        assert widget.stack.currentIndex() == 1
        assert widget.btn_mode_folder.isChecked() is True

        widget.set_mode("auto")
        assert widget.current_mode == "auto"
        assert widget.stack.currentIndex() == 2
        assert widget.btn_mode_auto.isChecked() is True

    def test_add_and_remove_folder(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        widget = DonorPoolWidget()
        folder1 = mock_pool_fs["folder1"]

        # Synchronously index folder
        widget.folder_paths.append(folder1)
        widget.donor_index.add_folder(folder1, recursive=False)
        widget._refresh_folder_list_widget()
        widget._refresh_pool_status_label()

        assert len(widget.folder_paths) == 1
        assert widget.list_folders.count() == 1
        assert len(widget.donor_index) == 2

        # Remove folder
        widget.list_folders.setCurrentRow(0)
        widget._remove_selected_folder()

        assert len(widget.folder_paths) == 0
        assert widget.list_folders.count() == 0
        assert len(widget.donor_index) == 0

    def test_manual_donor_file_mode(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        widget = DonorPoolWidget()
        widget.set_mode("file")

        donor_file = mock_pool_fs["folder1"] / "SANY0001.JPG"
        widget.set_manual_donor_file(donor_file)

        assert widget.manual_donor_path == donor_file
        assert widget.get_effective_donor() == donor_file
        assert "SANY0001.JPG" in widget.lbl_compat_indicator.text()

        widget.set_manual_donor_file(None)
        assert widget.manual_donor_path is None
        assert widget.get_effective_donor() is None

    def test_compatibility_ranking(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        widget = DonorPoolWidget()
        widget.set_mode("folder")

        # Add both folders
        folder1 = mock_pool_fs["folder1"]
        folder2 = mock_pool_fs["folder2"]
        widget.folder_paths.extend([folder1, folder2])
        widget.donor_index.add_folder(folder1)
        widget.donor_index.add_folder(folder2)

        # Candidate is SANY0099.JPG (640x480)
        cand = mock_pool_fs["cand"]
        widget.set_current_candidate(cand)

        effective = widget.get_effective_donor()
        assert effective is not None
        assert "SANY" in effective.name
        assert "Best donor" in widget.lbl_compat_indicator.text() or "Лучший донор" in widget.lbl_compat_indicator.text()

    def test_clear_entire_pool_confirmed(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        widget = DonorPoolWidget()
        widget.folder_paths.append(mock_pool_fs["folder1"])
        widget.donor_index.add_folder(mock_pool_fs["folder1"])

        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            widget._clear_entire_pool()

        assert len(widget.folder_paths) == 0
        assert len(widget.donor_index) == 0
        assert widget.list_folders.count() == 0

    def test_auto_discovery_checklist_and_add(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        widget = DonorPoolWidget()
        widget.set_mode("auto")

        cand1 = DonorFolderCandidate(
            path=mock_pool_fs["folder1"],
            jpeg_count=10,
            healthy_ratio=1.0,
            prefixes={"SANY"},
            priority_score=0.90,
            total_files=10,
        )

        widget._on_discovered_folder_found(cand1)
        assert widget.list_discovered.count() == 1
        item = widget.list_discovered.item(0)
        assert item.checkState() == Qt.CheckState.Checked

        # Add checked item
        with patch.object(widget, "index_folders") as mock_index:
            widget._add_selected_discovered_to_pool()
            assert mock_pool_fs["folder1"] in widget.folder_paths

    def test_settings_persistence(self, qapp: QApplication, tmp_path: Path):
        widget = DonorPoolWidget()
        custom_folder = (tmp_path / "photos").resolve()
        custom_folder.mkdir()
        create_donor_jpeg(custom_folder / "TEST.JPG")

        widget.folder_paths = [custom_folder]
        widget.set_mode("folder")
        widget.donor_index.add_folder(custom_folder)

        widget.save_settings()

        # Create new widget and test reload
        widget2 = DonorPoolWidget()
        widget2.load_settings()

        resolved_paths = [p.resolve() for p in widget2.folder_paths]
        assert custom_folder in resolved_paths
        assert widget2.current_mode == "folder"

    def test_e2e_full_donor_pool_lifecycle(self, qapp: QApplication, mock_pool_fs: dict[str, Path]):
        """E2E test verifying:
        1. Add folder with healthy photos to pool
        2. Auto-discovery finding DCIM folder
        3. Selecting candidate shows best donor with compatibility score
        4. Close and reopen GUI restores pool from settings
        """
        from photo_healer.core.donor_discovery import DonorDiscovery
        from photo_healer.gui.views.heal_view import HealView

        # Step 1: Open HealView and add folder of healthy JPEGs
        heal_view = HealView()
        folder1 = mock_pool_fs["folder1"]
        heal_view.donor_pool_widget.set_mode("folder")
        heal_view.donor_pool_widget.folder_paths.append(folder1)
        heal_view.donor_pool_widget.donor_index.add_folder(folder1, recursive=False)
        heal_view.donor_pool_widget._refresh_folder_list_widget()
        heal_view.donor_pool_widget._refresh_pool_status_label()

        assert len(heal_view.donor_pool_widget.donor_index) == 2
        assert "2" in heal_view.donor_pool_widget.lbl_pool_status.text()

        # Step 2: Auto-discovery detects DCIM folders
        dcim_dir = folder1.parent / "DCIM" / "100SANYO"
        dcim_dir.mkdir(parents=True, exist_ok=True)
        create_donor_jpeg(dcim_dir / "SANY0088.JPG")
        discovered = DonorDiscovery.discover_donor_folders(folder1.parent, max_depth=3)
        assert any("DCIM" in str(d.path) for d in discovered)

        # Step 3: Select damaged candidate and verify compatibility card
        cand = mock_pool_fs["cand"]
        heal_view.add_candidate(cand, select=True)
        effective_donor = heal_view.get_effective_donor()
        assert effective_donor is not None
        assert "SANY" in effective_donor.name
        compat_text = heal_view.donor_pool_widget.lbl_compat_indicator.text()
        assert "SANY" in compat_text
        assert ("Best donor" in compat_text) or ("Лучший донор" in compat_text)

        # Step 4: Save settings, create new HealView, verify persistence
        heal_view.donor_pool_widget.save_settings()

        heal_view2 = HealView()
        heal_view2.donor_pool_widget.load_settings()
        assert folder1 in heal_view2.donor_pool_widget.folder_paths
        assert heal_view2.donor_pool_widget.current_mode == "folder"
