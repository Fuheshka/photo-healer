# -*- coding: utf-8 -*-
"""Unit tests for TriageWorker and audit_file_streaming."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

from photo_healer.gui.workers.triage_worker import (
    DEFAULT_EXTS,
    IMAGE_MAGICS,
    TriageWorker,
    audit_file_streaming,
)
from tests.helpers import JPEGTestKit

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Ensure a QApplication exists for Qt QThread and signal loop testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def test_archive_dir(tmp_path: Path) -> Path:
    """Creates a realistic test archive directory containing all triage classes."""
    archive = tmp_path / "test_photos"
    archive.mkdir(parents=True, exist_ok=True)

    # 1. Valid JPEG
    valid_file = archive / "valid_01.jpg"
    valid_file.write_bytes(JPEGTestKit.minimal_donor_header(width=320, height=240) + b"\x12\x34\x56" + JPEGTestKit.EOI)

    # 2. TRIM-zero dummy file (all 0x00 bytes)
    trim_file = archive / "trim_dummy.jpg"
    trim_file.write_bytes(b"\x00" * 65536)

    # 3. Healed candidate (leading 0x00 followed by valid bitstream)
    cand_file = archive / "candidate_01.jpg"
    cand_file.write_bytes((b"\x00" * 4096) + JPEGTestKit.minimal_donor_header() + b"\xab\xcd\xef")

    # 4. Zero-length empty file
    empty_file = archive / "empty.jpg"
    empty_file.write_bytes(b"")

    # 5. Non-image file that should be ignored by default extension filter
    txt_file = archive / "notes.txt"
    txt_file.write_bytes(b"Investigation notes")

    return archive


class TestAuditFileStreaming:
    """Direct unit tests for audit_file_streaming O(1) RAM classifier."""

    def test_audit_valid_jpeg(self, tmp_path: Path):
        file_path = tmp_path / "valid.jpg"
        file_path.write_bytes(JPEGTestKit.minimal_donor_header() + JPEGTestKit.EOI)

        record = audit_file_streaming(file_path)
        assert record["status"] == "valid"
        assert record["name"] == "valid.jpg"
        assert record["first_nonzero"] == 0
        assert record["size"] == len(file_path.read_bytes())
        assert "Intact" in record["note"]

    def test_audit_trim_zero(self, tmp_path: Path):
        file_path = tmp_path / "trim.jpg"
        file_path.write_bytes(b"\x00" * 131072)

        record = audit_file_streaming(file_path)
        assert record["status"] == "trim_zero"
        assert record["first_nonzero"] == -1
        assert record["size"] == 131072
        assert "TRIM-erased" in record["note"]

    def test_audit_healed_candidate(self, tmp_path: Path):
        file_path = tmp_path / "cand.jpg"
        file_path.write_bytes(b"\x00" * 8192 + b"\xff\xd8\xff\xdb" + b"\x55" * 100)

        record = audit_file_streaming(file_path)
        assert record["status"] == "healed_candidate"
        assert record["first_nonzero"] == 8192
        assert record["size"] == 8192 + 4 + 100
        assert "TRIM header zeroed" in record["note"]

    def test_audit_empty_file(self, tmp_path: Path):
        file_path = tmp_path / "zero.jpg"
        file_path.write_bytes(b"")

        record = audit_file_streaming(file_path)
        assert record["status"] == "empty"
        assert record["size"] == 0
        assert "Zero-length" in record["note"]

    def test_audit_other_magic(self, tmp_path: Path):
        file_path = tmp_path / "bogus.jpg"
        file_path.write_bytes(b"NOT_A_JPEG_HEADER_AT_ALL")

        record = audit_file_streaming(file_path)
        assert record["status"] == "other"
        assert record["first_nonzero"] == 0
        assert "Unexpected magic" in record["note"]

    def test_audit_nonexistent_file(self, tmp_path: Path):
        file_path = tmp_path / "does_not_exist.jpg"
        record = audit_file_streaming(file_path)

        assert record["status"] == "error"
        assert "Stat error" in record["note"]


class TestTriageWorker:
    """Tests for asynchronous TriageWorker signals and thread execution."""

    def test_worker_signals_payload_synchronous(self, test_archive_dir: Path):
        worker = TriageWorker(folder_path=test_archive_dir)

        progress_events: list[tuple[int, int, str]] = []
        found_records: list[dict[str, Any]] = []
        finished_summaries: list[dict[str, Any]] = []

        worker.progress.connect(lambda cur, tot, name: progress_events.append((cur, tot, name)))
        worker.file_found.connect(found_records.append)
        worker.finished.connect(finished_summaries.append)

        # Run synchronously
        worker.run()

        # Check finished summary
        assert len(finished_summaries) == 1
        summary = finished_summaries[0]
        assert summary["status"] == "completed"
        assert summary["folder"] == str(test_archive_dir)
        assert summary["total_files"] == 4  # 4 image files (notes.txt ignored)
        assert summary["scanned_files"] == 4

        # Check counts
        counts = summary["counts"]
        assert counts["valid"] == 1
        assert counts["trim_zero"] == 1
        assert counts["healed_candidate"] == 1
        assert counts["empty"] == 1
        assert counts["error"] == 0

        # Check progress events
        assert len(progress_events) == 4
        for idx, (cur, tot, name) in enumerate(progress_events, 1):
            assert cur == idx
            assert tot == 4
            assert len(name) > 0

        # Check found records
        assert len(found_records) == 4
        statuses = {r["name"]: r["status"] for r in found_records}
        assert statuses["valid_01.jpg"] == "valid"
        assert statuses["trim_dummy.jpg"] == "trim_zero"
        assert statuses["candidate_01.jpg"] == "healed_candidate"
        assert statuses["empty.jpg"] == "empty"

    def test_worker_qthread_asynchronous_execution(self, test_archive_dir: Path):
        """Verify worker runs cleanly in QThread with Qt event loop."""
        worker = TriageWorker(folder_path=test_archive_dir)

        finished_box: list[dict[str, Any]] = []
        loop = QEventLoop()

        def on_finished(summary: dict[str, Any]):
            finished_box.append(summary)
            loop.quit()

        worker.finished.connect(on_finished)

        # Safety timer to prevent hanging
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(loop.quit)
        timeout_timer.start(5000)

        worker.start()
        loop.exec()
        timeout_timer.stop()
        worker.wait(2000)

        assert len(finished_box) == 1
        assert finished_box[0]["status"] == "completed"
        assert finished_box[0]["scanned_files"] == 4

    def test_worker_stop_halts_processing_cleanly(self, tmp_path: Path):
        """Create multiple files and verify stop() halts loop before scanning all."""
        large_dir = tmp_path / "large_archive"
        large_dir.mkdir(parents=True, exist_ok=True)

        for i in range(15):
            f = large_dir / f"img_{i:02d}.jpg"
            f.write_bytes(JPEGTestKit.minimal_donor_header())

        worker = TriageWorker(folder_path=large_dir)
        scanned_count = [0]
        finished_box: list[dict[str, Any]] = []

        def on_file_found(record: dict[str, Any]):
            scanned_count[0] += 1
            if scanned_count[0] == 3:
                worker.stop()

        worker.file_found.connect(on_file_found, Qt.ConnectionType.DirectConnection)
        worker.finished.connect(finished_box.append)

        worker.run()

        assert len(finished_box) == 1
        summary = finished_box[0]
        assert summary["status"] == "cancelled"
        assert summary["total_files"] == 15
        assert summary["scanned_files"] == 3

    def test_worker_nonexistent_directory(self, tmp_path: Path):
        nonexistent = tmp_path / "does_not_exist_dir"
        worker = TriageWorker(folder_path=nonexistent)

        finished_box: list[dict[str, Any]] = []
        worker.finished.connect(finished_box.append)

        worker.run()

        assert len(finished_box) == 1
        assert finished_box[0]["status"] == "error"
        assert "Folder not found" in finished_box[0]["error"]

    def test_worker_empty_directory(self, tmp_path: Path):
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        worker = TriageWorker(folder_path=empty_dir)
        finished_box: list[dict[str, Any]] = []
        worker.finished.connect(finished_box.append)

        worker.run()

        assert len(finished_box) == 1
        assert finished_box[0]["status"] == "completed"
        assert finished_box[0]["total_files"] == 0
        assert finished_box[0]["scanned_files"] == 0

    def test_worker_custom_extension_filtering(self, tmp_path: Path):
        custom_dir = tmp_path / "custom_exts"
        custom_dir.mkdir(parents=True, exist_ok=True)

        (custom_dir / "photo.jpg").write_bytes(JPEGTestKit.minimal_donor_header())
        (custom_dir / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (custom_dir / "photo.bmp").write_bytes(b"BM\x00\x00\x00\x00")

        # Only process .jpg and .png, ignore .bmp
        worker = TriageWorker(folder_path=custom_dir, extensions={"jpg", ".png"})
        found_names: list[str] = []
        worker.file_found.connect(lambda r: found_names.append(r["name"]))

        worker.run()

        assert sorted(found_names) == ["photo.jpg", "photo.png"]

    def test_worker_batch_found_and_discovering(self, test_archive_dir: Path):
        worker = TriageWorker(folder_path=test_archive_dir)
        batches: list[list[dict[str, Any]]] = []
        discovering_events: list[tuple[int, str]] = []

        worker.batch_found.connect(batches.append)
        worker.discovering.connect(lambda cnt, d: discovering_events.append((cnt, d)))

        worker.run()

        assert len(batches) >= 1
        flat_records = [rec for b in batches for rec in b]
        assert len(flat_records) == 4
        assert any(r["status"] == "healed_candidate" for r in flat_records)
        assert any(r["status"] == "trim_zero" for r in flat_records)
        assert any(r["status"] == "valid" for r in flat_records)

        assert len(discovering_events) >= 1
        assert discovering_events[-1][0] >= 4

    def test_fast_zero_chunk_audit(self, tmp_path: Path):
        import time
        # Create a 4MB TRIM-zero dummy file
        trim_file = tmp_path / "large_trim.jpg"
        trim_file.write_bytes(b"\x00" * (4 * 1024 * 1024))

        t0 = time.perf_counter()
        result = audit_file_streaming(str(trim_file), cached_size=4 * 1024 * 1024, cached_name="large_trim.jpg")
        elapsed = time.perf_counter() - t0

        assert result["status"] == "trim_zero"
        assert result["first_nonzero"] == -1
        assert elapsed < 0.05

    def test_cli_classify_file_fast(self, test_archive_dir: Path):
        from photo_healer.cli.main import classify_file
        valid_rec = classify_file(test_archive_dir / "valid_01.jpg")
        assert valid_rec["status"] == "valid"

        trim_rec = classify_file(test_archive_dir / "trim_dummy.jpg")
        assert trim_rec["status"] == "trim_zero"

        cand_rec = classify_file(test_archive_dir / "candidate_01.jpg")
        assert cand_rec["status"] == "healed_candidate"

    def test_root_triage_script_fast(self, test_archive_dir: Path):
        import triage
        valid_rec = triage.classify(test_archive_dir / "valid_01.jpg")
        assert valid_rec["status"] == "valid"

        trim_rec = triage.classify(test_archive_dir / "trim_dummy.jpg")
        assert trim_rec["status"] == "trim_zero"

        cand_rec = triage.classify(test_archive_dir / "candidate_01.jpg")
        assert cand_rec["status"] == "healed_candidate"

        results = triage.scan(test_archive_dir, {".jpg"})
        assert len(results) == 4
