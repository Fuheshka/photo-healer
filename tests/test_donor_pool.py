# -*- coding: utf-8 -*-
"""Unit tests for DonorIndex and multi-criteria donor pool matching."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from photo_healer.core.donor_pool import (
    DonorEntry,
    DonorIndex,
    DonorMatch,
    extract_filename_prefix,
    extract_jpeg_metadata,
    find_best_donor,
)
from tests.helpers import JPEGTestKit


@pytest.fixture
def synthetic_jpeg_pool(tmp_path: Path):
    """Create a structured pool of synthetic JPEGs with different DQTs and SOFs."""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()

    folder_sanyo = pool_dir / "sanyo_camera"
    folder_sanyo.mkdir()

    folder_canon = pool_dir / "canon_camera"
    folder_canon.mkdir()

    folder_other = pool_dir / "other_camera"
    folder_other.mkdir()

    # DQT tables
    dqt_table_a = bytes(range(1, 65))       # Sanyo quality A
    dqt_table_b = bytes([x * 2 % 256 for x in range(1, 65)])  # Canon quality B
    dqt_table_c = bytes([10] * 64)          # Other quality C

    # 1. Sanyo donor 1 (640x480, dqt_table_a)
    sanyo_donor_1 = folder_sanyo / "SANY0001.JPG"
    sanyo_header_1 = (
        JPEGTestKit.SOI
        + JPEGTestKit.dqt(table_id=0, values=dqt_table_a)
        + JPEGTestKit.sof0(width=640, height=480, components=3)
        + JPEGTestKit.dht(table_class=0, table_id=0)
        + JPEGTestKit.sos()
    )
    sanyo_donor_1.write_bytes(sanyo_header_1 + b"\x12\x34\x56" * 1000 + JPEGTestKit.EOI)

    # 2. Sanyo donor 2 in same folder (640x480, dqt_table_a, larger file)
    sanyo_donor_2 = folder_sanyo / "SANY0002.JPG"
    sanyo_donor_2.write_bytes(sanyo_header_1 + b"\x12\x34\x56" * 3000 + JPEGTestKit.EOI)

    # 3. Canon donor (1920x1080, dqt_table_b)
    canon_donor = folder_canon / "IMG_1001.JPG"
    canon_header = (
        JPEGTestKit.SOI
        + JPEGTestKit.dqt(table_id=0, values=dqt_table_b)
        + JPEGTestKit.sof0(width=1920, height=1080, components=3)
        + JPEGTestKit.dht(table_class=0, table_id=0)
        + JPEGTestKit.sos()
    )
    canon_donor.write_bytes(canon_header + b"\xab\xcd\xef" * 2000 + JPEGTestKit.EOI)

    # 4. Other donor (800x600, dqt_table_c)
    other_donor = folder_other / "DSC0050.JPG"
    other_header = (
        JPEGTestKit.SOI
        + JPEGTestKit.dqt(table_id=0, values=dqt_table_c)
        + JPEGTestKit.sof0(width=800, height=600, components=3)
        + JPEGTestKit.dht(table_class=0, table_id=0)
        + JPEGTestKit.sos()
    )
    other_donor.write_bytes(other_header + b"\x77\x88\x99" * 1500 + JPEGTestKit.EOI)

    return {
        "root": pool_dir,
        "folder_sanyo": folder_sanyo,
        "folder_canon": folder_canon,
        "folder_other": folder_other,
        "sanyo_1": sanyo_donor_1,
        "sanyo_2": sanyo_donor_2,
        "canon": canon_donor,
        "other": other_donor,
        "dqt_a": dqt_table_a,
        "dqt_b": dqt_table_b,
        "dqt_c": dqt_table_c,
    }


class TestPrefixExtraction:
    """Test filename prefix parsing for photo series."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("SANY0015.JPG", "SANY"),
            ("sany0015.jpg", "SANY"),
            ("IMG_1234.JPEG", "IMG_"),
            ("img_9999.JPG", "IMG_"),
            ("DSC0001.JPG", "DSC"),
            ("DSCF0001.JPG", "DSCF"),
            ("P1010001.JPG", "P"),
            ("DCIM001.JPG", "DCIM"),
            ("PHOTO_12.jpg", "PHOTO_"),
            ("12345.jpg", ""),
            ("!broken.jpg", ""),
        ],
    )
    def test_extract_filename_prefix(self, filename: str, expected: str):
        assert extract_filename_prefix(filename) == expected


class TestJpegMetadataExtraction:
    """Test O(1) RAM streaming metadata extraction from JPEG headers."""

    def test_extract_metadata_healthy_file(self, synthetic_jpeg_pool):
        sanyo_1 = synthetic_jpeg_pool["sanyo_1"]
        meta = extract_jpeg_metadata(sanyo_1)

        assert meta is not None
        assert meta["width"] == 640
        assert meta["height"] == 480
        assert meta["components"] == 3
        assert meta["subsampling"] == "4:2:0"
        assert meta["dqt_hash"] is not None
        assert len(meta["dqt_hash"]) == 64  # SHA-256
        assert meta["dht_hash"] is not None
        assert len(meta["dht_hash"]) == 64  # SHA-256

    def test_extract_metadata_identical_dqt_hash(self, synthetic_jpeg_pool):
        sanyo_1 = synthetic_jpeg_pool["sanyo_1"]
        sanyo_2 = synthetic_jpeg_pool["sanyo_2"]
        canon = synthetic_jpeg_pool["canon"]

        meta_sanyo_1 = extract_jpeg_metadata(sanyo_1)
        meta_sanyo_2 = extract_jpeg_metadata(sanyo_2)
        meta_canon = extract_jpeg_metadata(canon)

        assert meta_sanyo_1["dqt_hash"] == meta_sanyo_2["dqt_hash"]
        assert meta_sanyo_1["dqt_hash"] != meta_canon["dqt_hash"]

    def test_extract_metadata_zeroed_file_returns_none(self, tmp_path: Path):
        zero_file = tmp_path / "zero.jpg"
        zero_file.write_bytes(b"\x00" * 8192)
        assert extract_jpeg_metadata(zero_file) is None

    def test_extract_metadata_non_jpeg_returns_none(self, tmp_path: Path):
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Hello World", encoding="utf-8")
        assert extract_jpeg_metadata(txt_file) is None


class TestDonorIndex:
    """Test folder indexing, cache serialization, and invalidation."""

    def test_add_folder_and_len(self, synthetic_jpeg_pool):
        idx = DonorIndex()
        count = idx.add_folder(synthetic_jpeg_pool["folder_sanyo"])
        assert count == 2
        assert len(idx) == 2
        assert len(idx.entries) == 2

    def test_add_multiple_folders_recursive(self, synthetic_jpeg_pool):
        idx = DonorIndex()
        count = idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)
        assert count == 4
        assert len(idx) == 4

    def test_remove_folder(self, synthetic_jpeg_pool):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)
        assert len(idx) == 4

        removed = idx.remove_folder(synthetic_jpeg_pool["folder_sanyo"])
        assert removed == 2
        assert len(idx) == 2

    def test_json_serialization_and_cache_validity(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        json_path = tmp_path / "donor_pool.json"
        idx.save_to_json(json_path)

        assert json_path.exists()
        loaded = DonorIndex.load_from_json(json_path)
        assert len(loaded) == 4
        assert loaded.is_cache_valid() is True

        # Invalidate cache by modifying a folder
        new_file = synthetic_jpeg_pool["folder_sanyo"] / "new.jpg"
        new_file.write_bytes(b"\x00" * 10)
        # Update folder mtime explicitly
        now = time.time() + 10
        os.utime(synthetic_jpeg_pool["folder_sanyo"], (now, now))

        assert loaded.is_cache_valid() is False


class TestMultiCriteriaRanking:
    """Test multi-criteria donor ranking across tiers."""

    def test_tier1_folder_and_prefix_match(self, synthetic_jpeg_pool):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate in same folder as sanyo_1 and sanyo_2, zeroed header
        cand = synthetic_jpeg_pool["folder_sanyo"] / "SANY0015.JPG"
        cand.write_bytes(b"\x00" * 4096 + b"\x12\x34" * 1000)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]

        # Top match should be from same folder with SANY prefix (Tier 1)
        assert best.path.parent.resolve() == synthetic_jpeg_pool["folder_sanyo"].resolve()
        assert best.entry.prefix == "SANY"
        assert best.tier == 1
        assert "Same folder" in best.match_reason

    def test_tier2_prefix_match_across_pool(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate in isolated folder (outside sanyo_camera), but named SANY0099.JPG
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        cand = isolated / "SANY0099.JPG"
        cand.write_bytes(b"\x00" * 4096 + b"\x12\x34" * 1000)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]

        assert best.entry.prefix == "SANY"
        assert best.tier == 2
        assert "Prefix match across pool" in best.match_reason

    def test_tier3_size_cluster_match(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate with unknown prefix, size closely matches Canon donor
        isolated = tmp_path / "isolated_size"
        isolated.mkdir()
        cand = isolated / "UNKNOWN_001.JPG"
        canon_size = synthetic_jpeg_pool["canon"].stat().st_size
        # Make candidate size within 2% of canon
        cand.write_bytes(b"\x00" * canon_size)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]

        assert best.path.resolve() == synthetic_jpeg_pool["canon"].resolve()
        assert best.tier == 3
        assert "Size cluster" in best.match_reason

    def test_dqt_hash_exact_match_for_partial_header(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate with non-matching name in other folder, but intact header with dqt_table_b (Canon)
        isolated = tmp_path / "isolated_dqt"
        isolated.mkdir()
        cand = isolated / "CORRUPT_CUSTOM.JPG"
        cand_header = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(table_id=0, values=synthetic_jpeg_pool["dqt_b"])
            + JPEGTestKit.sof0(width=1000, height=800, components=3)
            + JPEGTestKit.dht(table_class=0, table_id=0)
            + JPEGTestKit.sos()
        )
        cand.write_bytes(cand_header + b"\x00" * 2000)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]

        # Exact DQT match should score 1.0 and beat any prefix or size heuristics!
        assert best.path.resolve() == synthetic_jpeg_pool["canon"].resolve()
        assert best.score == 1.0
        assert "Exact DQT" in best.match_reason

    def test_geometry_match_for_partial_header(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate with unknown DQT, but identical geometry to other_donor (800x600)
        isolated = tmp_path / "isolated_geom"
        isolated.mkdir()
        cand = isolated / "CUSTOM_001.JPG"
        cand_header = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(table_id=0, values=bytes([99] * 64))  # Unique DQT
            + JPEGTestKit.sof0(width=800, height=600, components=3)
            + JPEGTestKit.dht(table_class=0, table_id=0)
            + JPEGTestKit.sos()
        )
        cand.write_bytes(cand_header + b"\x00" * 2000)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]

        # Geometry match should match other_donor (800x600)
        assert best.path.resolve() == synthetic_jpeg_pool["other"].resolve()
        assert "Geometry match" in best.match_reason

    def test_tier4_fallback_when_no_prefix_or_size_match(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        isolated = tmp_path / "isolated_fallback"
        isolated.mkdir()
        cand = isolated / "NOMATCH_999.JPG"
        # File size 100x larger than anything in pool
        cand.write_bytes(b"\x00" * 50_000_000)

        matches = find_best_donor(cand, idx)
        assert len(matches) > 0
        best = matches[0]
        assert best.tier == 4
        assert "fallback" in best.match_reason.lower()

    def test_exclude_specified_file_from_pool(self, synthetic_jpeg_pool, tmp_path: Path):
        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        cand = synthetic_jpeg_pool["folder_sanyo"] / "SANY0015.JPG"
        cand.write_bytes(b"\x00" * 4096 + b"\x12\x34" * 1000)

        # Exclude sanyo_1
        matches = find_best_donor(cand, idx, exclude=synthetic_jpeg_pool["sanyo_1"])
        assert len(matches) > 0
        matched_paths = [m.path.resolve() for m in matches]
        assert synthetic_jpeg_pool["sanyo_1"].resolve() not in matched_paths
        # sanyo_2 should still be found
        assert synthetic_jpeg_pool["sanyo_2"].resolve() in matched_paths

    def test_empty_pool_returns_empty_list(self, tmp_path: Path):
        idx = DonorIndex()
        cand = tmp_path / "test.jpg"
        cand.write_bytes(b"\x00" * 100)
        assert find_best_donor(cand, idx) == []


class TestHealWorkerDonorIndexIntegration:
    """Test HealWorker background execution using DonorIndex."""

    def test_heal_worker_with_donor_index(self, synthetic_jpeg_pool, tmp_path: Path):
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        from photo_healer.core.validator import JpegValidator
        from photo_healer.gui.workers.heal_worker import HealWorker

        app = QApplication.instance() or QApplication([])

        idx = DonorIndex()
        idx.add_folders([synthetic_jpeg_pool["root"]], recursive=True)

        # Candidate in isolated folder with SANY prefix
        cand_dir = tmp_path / "cand_dir"
        cand_dir.mkdir()
        cand = cand_dir / "SANY9999.JPG"
        cand.write_bytes(b"\x00" * 4096 + b"\x12\x34\x56" * 500 + JPEGTestKit.EOI)

        out_dir = tmp_path / "healed_out"

        worker = HealWorker(
            candidates=[cand],
            auto_donor=True,
            donor_index=idx,
            output_dir=out_dir,
            create_backup=False,
        )

        healed_records: list[dict] = []
        loop = QEventLoop()
        worker.file_healed.connect(healed_records.append)
        worker.finished.connect(lambda _: loop.quit())
        worker.start()
        loop.exec()

        assert len(healed_records) == 1
        assert healed_records[0]["status"] == "healed"
        out_healed = out_dir / "SANY9999_HEALED.JPG"
        assert out_healed.exists()
        val = JpegValidator.validate(out_healed)
        assert val.is_valid is True


