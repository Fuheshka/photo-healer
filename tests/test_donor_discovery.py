# -*- coding: utf-8 -*-
"""Unit tests for DonorDiscovery and donor folder heuristics."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from photo_healer.core.donor_discovery import (
    DRIVE_REMOVABLE,
    DonorDiscovery,
    DonorFolderCandidate,
    enumerate_removable_drives,
    is_network_drive,
    is_symlink_or_junction,
)
from tests.helpers import JPEGTestKit


def create_dummy_jpeg(path: Path, intact: bool = True, size: int = 500) -> None:
    """Helper to write a valid synthetic JPEG file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = JPEGTestKit.minimal_donor_header()
    body = b"\x12\x34\x56\x78" * ((size - len(header) - 2) // 4)
    trailer = JPEGTestKit.EOI if intact else b"\x00\x00"
    path.write_bytes(header + body + trailer)


class TestDonorFolderCandidate:
    """Tests for DonorFolderCandidate dataclass."""

    def test_candidate_properties_and_dict(self, tmp_path: Path):
        folder = tmp_path / "DCIM" / "100SANYO"
        cand = DonorFolderCandidate(
            path=folder,
            jpeg_count=42,
            healthy_ratio=0.95,
            prefixes={"SANY", "IMG"},
            priority_score=0.88,
            total_files=45,
        )
        assert cand.name == "100SANYO"
        assert "IMG" in cand.prefix_summary
        assert "SANY" in cand.prefix_summary

        d = cand.to_dict()
        assert d["path"] == str(folder)
        assert d["name"] == "100SANYO"
        assert d["jpeg_count"] == 42
        assert d["healthy_ratio"] == 0.95
        assert d["prefixes"] == ["IMG", "SANY"]
        assert d["priority_score"] == 0.88
        assert d["total_files"] == 45


class TestDonorDiscoveryScanning:
    """Tests for directory scanning, depth limiting, and filtering."""

    def test_nonexistent_or_file_root_returns_empty(self, tmp_path: Path):
        assert DonorDiscovery.discover_donor_folders(tmp_path / "nonexistent") == []

        dummy_file = tmp_path / "file.txt"
        dummy_file.write_text("hello")
        assert DonorDiscovery.discover_donor_folders(dummy_file) == []

    def test_empty_folder_returns_empty(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert DonorDiscovery.discover_donor_folders(empty_dir) == []

    def test_depth_limiting(self, tmp_path: Path):
        # Create deep directory hierarchy: root -> d1 -> d2 -> d3 -> d4
        deep_dir = tmp_path / "d1" / "d2" / "d3" / "d4"
        deep_dir.mkdir(parents=True)
        create_dummy_jpeg(deep_dir / "PHOTO1.JPG")

        # max_depth=2 should not reach d4
        candidates_depth_2 = DonorDiscovery.discover_donor_folders(tmp_path, max_depth=2)
        assert len(candidates_depth_2) == 0

        # max_depth=4 should discover d4
        candidates_depth_4 = DonorDiscovery.discover_donor_folders(tmp_path, max_depth=4)
        assert len(candidates_depth_4) == 1
        assert candidates_depth_4[0].path == deep_dir.resolve()
        assert candidates_depth_4[0].jpeg_count == 1

    def test_skips_healed_and_bak_files(self, tmp_path: Path):
        folder = tmp_path / "photos"
        folder.mkdir()
        create_dummy_jpeg(folder / "IMG_0001_HEALED.jpg")
        create_dummy_jpeg(folder / "IMG_0002.jpg.bak")
        (folder / "text.txt").write_text("data")

        # Folder has only healed, bak, and text -> no valid candidates
        candidates = DonorDiscovery.discover_donor_folders(folder)
        assert len(candidates) == 0

        # Add one valid donor
        create_dummy_jpeg(folder / "IMG_0003.JPG")
        candidates = DonorDiscovery.discover_donor_folders(folder)
        assert len(candidates) == 1
        assert candidates[0].jpeg_count == 1

    def test_skips_hidden_and_system_directories(self, tmp_path: Path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        create_dummy_jpeg(git_dir / "FAKE.JPG")

        pycache_dir = tmp_path / "__pycache__"
        pycache_dir.mkdir()
        create_dummy_jpeg(pycache_dir / "CACHED.JPG")

        candidates = DonorDiscovery.discover_donor_folders(tmp_path, max_depth=3)
        assert len(candidates) == 0


class TestPriorityHeuristics:
    """Tests for candidate scoring heuristics."""

    def test_marker_folder_name_bonus(self, tmp_path: Path):
        marker_dir = tmp_path / "DCIM"
        marker_dir.mkdir()
        for i in range(5):
            create_dummy_jpeg(marker_dir / f"IMG_{i:04d}.JPG")

        generic_dir = tmp_path / "random_stuff"
        generic_dir.mkdir()
        for i in range(5):
            create_dummy_jpeg(generic_dir / f"IMG_{i:04d}.JPG")

        candidates = DonorDiscovery.discover_donor_folders(tmp_path, max_depth=2)
        assert len(candidates) == 2

        # DCIM must be ranked first due to marker bonus
        assert candidates[0].path == marker_dir.resolve()
        assert candidates[0].priority_score > candidates[1].priority_score

    def test_prefix_matching_bonus(self, tmp_path: Path):
        folder_sanyo = tmp_path / "camera_a"
        folder_sanyo.mkdir()
        create_dummy_jpeg(folder_sanyo / "SANY001.JPG")
        create_dummy_jpeg(folder_sanyo / "SANY002.JPG")

        folder_sony = tmp_path / "camera_b"
        folder_sony.mkdir()
        create_dummy_jpeg(folder_sony / "DSC0001.JPG")
        create_dummy_jpeg(folder_sony / "DSC0002.JPG")

        # When looking for SANY prefix, camera_a gets priority
        candidates = DonorDiscovery.discover_donor_folders(
            tmp_path,
            max_depth=2,
            target_prefixes={"SANY"},
        )
        assert len(candidates) == 2
        assert candidates[0].path == folder_sanyo.resolve()
        assert "SANY" in candidates[0].prefixes

    def test_archive_proximity_bonus(self, tmp_path: Path):
        archive_root = tmp_path / "damaged_archive"
        archive_root.mkdir()

        neighbor_dir = tmp_path / "sister_photos"
        neighbor_dir.mkdir()
        create_dummy_jpeg(neighbor_dir / "PHOTO1.JPG")

        cand = DonorDiscovery.evaluate_folder(neighbor_dir, archive_root=archive_root)
        assert cand is not None
        # Should have received proximity bonus
        assert cand.priority_score > 0.30

    def test_cancellation_callback(self, tmp_path: Path):
        # Create multiple folders
        for i in range(5):
            d = tmp_path / f"folder_{i}"
            d.mkdir()
            create_dummy_jpeg(d / "TEST.JPG")

        calls = [0]

        def cancel_after_first() -> bool:
            calls[0] += 1
            return calls[0] > 2

        candidates = DonorDiscovery.discover_donor_folders(
            tmp_path,
            max_depth=2,
            is_cancelled=cancel_after_first,
        )
        assert len(candidates) < 5


class TestSystemAndDriveDiscovery:
    """Tests for system photo folders and removable drive detection."""

    def test_discover_system_photo_folders_returns_list(self):
        folders = DonorDiscovery.discover_system_photo_folders()
        assert isinstance(folders, list)
        for f in folders:
            assert isinstance(f, Path)
            assert f.is_dir()

    @patch("sys.platform", "win32")
    def test_removable_drive_enumeration_mocked(self):
        # Mock ctypes calls to simulate drive E:\ as removable
        with patch("ctypes.windll.kernel32.GetLogicalDrives", return_value=0b10000):  # 5th bit -> E:\
            with patch("ctypes.windll.kernel32.GetDriveTypeW", return_value=DRIVE_REMOVABLE):
                with patch("pathlib.Path.is_dir", return_value=True):
                    drives = enumerate_removable_drives()
                    assert len(drives) == 1
                    assert str(drives[0]).startswith("E:")

    def test_is_network_drive(self, tmp_path: Path):
        assert not is_network_drive(tmp_path)
        assert is_network_drive(Path(r"\\192.168.1.100\photos"))
        assert is_network_drive(Path("//nas-server/share/photos"))

    def test_is_symlink_or_junction(self, tmp_path: Path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        assert not is_symlink_or_junction(real_dir)
