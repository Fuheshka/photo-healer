# -*- coding: utf-8 -*-
"""Unit tests for HealWorker background thread and donor matching heuristics."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from photo_healer.core.validator import JpegValidator
from photo_healer.gui.workers.heal_worker import (
    HealWorker,
    extract_camera_info,
    find_matching_donor,
)
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
def healing_env(tmp_path: Path):
    """Sets up a realistic folder structure for healing tests."""
    archive = tmp_path / "archive"
    archive.mkdir()

    sub1 = archive / "folder_a"
    sub1.mkdir()
    sub2 = archive / "folder_b"
    sub2.mkdir()

    # 1. Donor in sub1
    donor_a = sub1 / "SANY0058.JPG"
    donor_a.write_bytes(JPEGTestKit.minimal_donor_header(640, 480) + b"\x12\x34\x56" + JPEGTestKit.EOI)

    # 2. Candidate with matching prefix in sub1
    cand_a = sub1 / "SANY0015.JPG"
    cand_a.write_bytes((b"\x00" * 4096) + JPEGTestKit.minimal_donor_header(640, 480) + b"\x99" * 200 + JPEGTestKit.EOI)

    # 3. Candidate without donor in sub2
    cand_b = sub2 / "OTHER001.JPG"
    cand_b.write_bytes((b"\x00" * 2048) + JPEGTestKit.minimal_donor_header(320, 240) + b"\xaa" * 100 + JPEGTestKit.EOI)

    return archive, donor_a, cand_a, cand_b


class TestDonorHeuristics:
    """Tests for camera info extraction and matching donor discovery."""

    def test_find_matching_donor_same_folder_prefix(self, healing_env):
        archive, donor_a, cand_a, _ = healing_env
        found = find_matching_donor(cand_a, archive_root=archive)
        assert found is not None
        assert found.resolve() == donor_a.resolve()

    def test_find_matching_donor_fallback_to_archive_root(self, healing_env):
        archive, donor_a, _, cand_b = healing_env
        found = find_matching_donor(cand_b, archive_root=archive)
        assert found is not None
        assert found.resolve() == donor_a.resolve()

    def test_find_matching_donor_none_when_empty(self, tmp_path: Path):
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        cand = isolated / "test.jpg"
        cand.write_bytes(b"\x00" * 1000)
        found = find_matching_donor(cand)
        assert found is None

    def test_extract_camera_info_returns_tuple(self, healing_env):
        _, donor_a, _, _ = healing_env
        make, model, dim = extract_camera_info(donor_a)
        assert isinstance(make, str)
        assert isinstance(model, str)
        assert isinstance(dim, tuple)


class TestHealWorkerExecution:
    """Tests for single and batch HealWorker operations."""

    def test_single_heal_worker(self, qapp: QApplication, healing_env, tmp_path: Path):
        archive, donor_a, cand_a, _ = healing_env
        out_dir = tmp_path / "output"

        worker = HealWorker(
            candidates=[cand_a],
            donor_path=donor_a,
            auto_donor=False,
            pad_geometry=False,
            create_backup=True,
            output_dir=out_dir,
            archive_root=archive,
        )

        healed_records: list[dict] = []
        summary_holder: list[dict] = []

        loop = QEventLoop()
        worker.file_healed.connect(healed_records.append)

        def on_finished(summary: dict):
            summary_holder.append(summary)
            loop.quit()

        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        assert len(healed_records) == 1
        rec = healed_records[0]
        assert rec["status"] == "healed"
        assert rec["name"] == "SANY0015.JPG"

        out_file = Path(rec["output"])
        assert out_file.exists()
        val = JpegValidator.validate(out_file)
        assert val.is_valid is True

        assert len(summary_holder) == 1
        assert summary_holder[0]["healed"] == 1
        assert summary_holder[0]["errors"] == 0

    def test_batch_heal_worker_with_auto_donor(self, qapp: QApplication, healing_env):
        archive, donor_a, cand_a, cand_b = healing_env

        worker = HealWorker(
            candidates=[cand_a, cand_b],
            donor_path=None,
            auto_donor=True,
            pad_geometry=False,
            create_backup=True,
            archive_root=archive,
        )

        progress_calls: list[tuple[int, int, str]] = []
        healed_records: list[dict] = []
        summary_holder: list[dict] = []

        loop = QEventLoop()
        worker.progress.connect(lambda cur, tot, name: progress_calls.append((cur, tot, name)))
        worker.file_healed.connect(healed_records.append)

        def on_finished(summary: dict):
            summary_holder.append(summary)
            loop.quit()

        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        assert len(progress_calls) == 2
        assert len(healed_records) == 2
        assert summary_holder[0]["healed"] == 2
        assert summary_holder[0]["errors"] == 0

    def test_heal_worker_inplace_creates_backup(self, qapp: QApplication, tmp_path: Path):
        donor = tmp_path / "donor.jpg"
        donor.write_bytes(JPEGTestKit.minimal_donor_header(320, 240) + b"\x12\x34\x56" + JPEGTestKit.EOI)

        cand = tmp_path / "inplace_cand.jpg"
        orig_bytes = (b"\x00" * 1024) + JPEGTestKit.minimal_donor_header(320, 240) + b"\x99" * 100 + JPEGTestKit.EOI
        cand.write_bytes(orig_bytes)

        worker = HealWorker(
            candidates=[cand],
            donor_path=donor,
            auto_donor=False,
            inplace=True,
            create_backup=True,
        )

        loop = QEventLoop()
        worker.finished.connect(lambda _: loop.quit())
        worker.start()
        loop.exec()

        bak_file = cand.with_suffix(cand.suffix + ".bak")
        assert bak_file.exists()
        assert bak_file.read_bytes() == orig_bytes

        # Original is replaced with valid JPEG
        assert cand.exists()
        val = JpegValidator.validate(cand)
        assert val.is_valid is True

    def test_heal_worker_cancellation(self, qapp: QApplication, healing_env):
        archive, _, cand_a, _ = healing_env
        # Create list of 20 items to cancel midway
        candidates = [cand_a] * 20

        worker = HealWorker(
            candidates=candidates,
            auto_donor=True,
            archive_root=archive,
        )

        loop = QEventLoop()
        summary_holder: list[dict] = []

        def on_progress(cur, tot, name):
            if cur >= 2:
                worker.stop()

        def on_finished(summary: dict):
            summary_holder.append(summary)
            loop.quit()

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        assert len(summary_holder) == 1
        assert summary_holder[0]["status"] == "cancelled"
        assert summary_holder[0]["healed"] < 20
