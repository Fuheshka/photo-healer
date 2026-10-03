"""Tests for thumbnail stripping, IFD1 rebuilding, and fix-previews CLI."""

from __future__ import annotations

import io
import os
import shutil
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from photo_healer.cli.i18n import set_language
from photo_healer.cli.main import main
from photo_healer.core.carver import ThumbnailCarver
from photo_healer.core.thumbnail import (
    FileProcessResult,
    _clean_exif_payload,
    clear_windows_thumbnail_cache,
    process_directory,
    process_file,
    rebuild_jpeg_thumbnail,
    strip_jpeg_thumbnails,
)
from photo_healer.core.validator import JpegValidator
from tests.helpers import JPEGTestKit
from tests.test_carver import build_exif_app1, build_mpf_app2, build_synthetic_jpeg


@pytest.fixture(autouse=True)
def enforce_test_language(monkeypatch: pytest.MonkeyPatch):
    """Enforce English interface language for predictable output assertions."""
    monkeypatch.setenv("PHOTO_HEALER_LANG", "en")
    set_language("en")
    yield
    set_language(None)


# ── Synthetic Fixture Helpers ──────────────────────────────────────────────────

def build_test_jpeg_with_thumbs(width: int = 640, height: int = 480) -> bytes:
    """Builds a valid JPEG with both an APP1 IFD1 thumbnail and an APP2 MPF preview."""
    thumb_bytes = build_synthetic_jpeg(width=160, height=120)
    app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=True)

    preview_bytes = build_synthetic_jpeg(width=1920, height=1080)
    base = build_synthetic_jpeg(width=width, height=height)

    # ICC profile segment to test that non-MPF APP2 segments are preserved
    icc_payload = b"ICC_PROFILE\x00\x01\x00" + b"\x00" * 20
    app2_icc = b"\xff\xe2" + struct.pack(">H", len(icc_payload) + 2) + icc_payload

    mpf_offset = 2 + len(app1) + len(app2_icc) + 10
    temp_app2 = build_mpf_app2([preview_bytes], primary_len=0, endian="<", mpf_header_file_offset=mpf_offset)
    primary_len = len(JPEGTestKit.SOI + app1 + app2_icc + temp_app2 + base[2:])
    app2_mpf = build_mpf_app2([preview_bytes], primary_len=primary_len, endian="<", mpf_header_file_offset=mpf_offset)

    return JPEGTestKit.SOI + app1 + app2_icc + app2_mpf + base[2:] + preview_bytes


@pytest.fixture
def sample_jpeg_with_thumbs() -> bytes:
    return build_test_jpeg_with_thumbs(640, 480)


@pytest.fixture
def clean_jpeg() -> bytes:
    return build_synthetic_jpeg(640, 480)


@pytest.fixture
def test_photo_tree(tmp_path: Path) -> Path:
    """Creates a nested directory with photos for batch and CLI tests."""
    root = tmp_path / "archive"
    root.mkdir()
    sub = root / "vacation"
    sub.mkdir()

    # Photos with legacy donor thumbnails
    (root / "img_01.jpg").write_bytes(build_test_jpeg_with_thumbs(640, 480))
    (sub / "img_02.jpg").write_bytes(build_test_jpeg_with_thumbs(800, 600))

    # Clean photo without thumbnails
    (root / "clean.jpg").write_bytes(build_synthetic_jpeg(640, 480))

    # Non-JPEG file
    (root / "notes.txt").write_text("not an image", encoding="utf-8")

    return root


# ── Unit Tests: strip_jpeg_thumbnails ─────────────────────────────────────────

class TestStripJpegThumbnails:
    def test_strip_removes_mpf_and_ifd1(self, sample_jpeg_with_thumbs):
        cleaned, freed = strip_jpeg_thumbnails(sample_jpeg_with_thumbs)
        assert freed > 0
        assert len(cleaned) < len(sample_jpeg_with_thumbs)

        # Structure remains valid JPEG
        val = JpegValidator.validate(cleaned)
        assert val.is_valid
        assert val.width == 640
        assert val.height == 480

        # Embedded previews are completely removed
        carver = ThumbnailCarver()
        assert carver.extract_mpf_preview(cleaned) is None
        assert carver.extract_exif_thumbnail(cleaned) is None
        assert b"MPF\x00" not in cleaned

    def test_strip_preserves_icc_profile_app2(self, sample_jpeg_with_thumbs):
        cleaned, _ = strip_jpeg_thumbnails(sample_jpeg_with_thumbs)
        assert b"ICC_PROFILE\x00" in cleaned

    def test_strip_idempotent_on_clean_jpeg(self, clean_jpeg):
        cleaned, freed = strip_jpeg_thumbnails(clean_jpeg)
        assert freed == 0
        assert cleaned == clean_jpeg

    def test_strip_big_endian_exif(self):
        thumb_bytes = build_synthetic_jpeg(160, 120)
        app1 = build_exif_app1(thumb_bytes, endian=">", thumb_in_ifd1=True)
        raw_jpeg = JPEGTestKit.SOI + app1 + build_synthetic_jpeg(640, 480)[2:]

        cleaned, freed = strip_jpeg_thumbnails(raw_jpeg)
        assert freed > 0
        assert JpegValidator.validate(cleaned).is_valid
        assert ThumbnailCarver.extract_exif_thumbnail(cleaned) is None

    def test_strip_invalid_jpeg_raises(self):
        with pytest.raises(ValueError, match="SOI marker"):
            strip_jpeg_thumbnails(b"NOT_A_JPEG_FILE")


# ── Unit Tests: rebuild_jpeg_thumbnail ────────────────────────────────────────

class TestRebuildJpegThumbnail:
    def test_rebuild_default_thumbnail_160x120(self, clean_jpeg):
        rebuilt = rebuild_jpeg_thumbnail(clean_jpeg, size=(160, 120), quality=75)
        val = JpegValidator.validate(rebuilt)
        assert val.is_valid
        assert val.width == 640
        assert val.height == 480

        # Verify extracted thumbnail
        thumb_data = ThumbnailCarver.extract_exif_thumbnail(rebuilt)
        assert thumb_data is not None
        with Image.open(io.BytesIO(thumb_data)) as img:
            assert img.width <= 160
            assert img.height <= 120
            img.verify()

    def test_rebuild_custom_thumbnail_320x240(self, clean_jpeg):
        rebuilt = rebuild_jpeg_thumbnail(clean_jpeg, size=(320, 240), quality=80)
        thumb_data = ThumbnailCarver.extract_exif_thumbnail(rebuilt)
        assert thumb_data is not None
        with Image.open(io.BytesIO(thumb_data)) as img:
            assert img.width <= 320
            assert img.height <= 240

    def test_rebuild_strips_existing_donor_mpf(self, sample_jpeg_with_thumbs):
        rebuilt = rebuild_jpeg_thumbnail(sample_jpeg_with_thumbs, size=(160, 120))
        carver = ThumbnailCarver()
        # MPF must be removed
        assert carver.extract_mpf_preview(rebuilt) is None
        # IFD1 thumbnail must be present
        assert carver.extract_exif_thumbnail(rebuilt) is not None

    def test_rebuild_invalid_jpeg_raises(self):
        with pytest.raises(ValueError, match="SOI marker"):
            rebuild_jpeg_thumbnail(b"CORRUPTED_BYTES")


# ── Unit Tests: clear_windows_thumbnail_cache ─────────────────────────────────

class TestClearWindowsThumbnailCache:
    def test_non_windows_skips_safely(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "linux")
        res = clear_windows_thumbnail_cache()
        assert res["success"] is True
        assert "skipped_non_windows" in res["actions"]

    def test_windows_cache_reset_execution(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(sys, "platform", "win32")

        # Mock shell32.SHChangeNotify
        mock_shell32 = MagicMock()
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32 = mock_shell32
        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)

        # Mock ie4uinit subprocess
        mock_subprocess = MagicMock()
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        monkeypatch.setattr("photo_healer.core.thumbnail.subprocess.run", mock_subprocess.run)

        # Mock Explorer thumbcache folder
        appdata = tmp_path / "AppData" / "Local"
        explorer_dir = appdata / "Microsoft" / "Windows" / "Explorer"
        explorer_dir.mkdir(parents=True)
        f1 = explorer_dir / "thumbcache_32.db"
        f2 = explorer_dir / "thumbcache_idx.db"
        f1.write_bytes(b"dummy1")
        f2.write_bytes(b"dummy2")
        monkeypatch.setenv("LOCALAPPDATA", str(appdata))

        res = clear_windows_thumbnail_cache()
        assert res["success"] is True
        assert "sh_change_notify" in res["actions"]
        assert "ie4uinit_show" in res["actions"]
        assert res["files_cleared"] == 2
        assert not f1.exists()
        assert not f2.exists()

    def test_windows_locked_files_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "ctypes", MagicMock())
        monkeypatch.setattr("photo_healer.core.thumbnail.subprocess.run", MagicMock())

        appdata = tmp_path / "AppData" / "Local"
        explorer_dir = appdata / "Microsoft" / "Windows" / "Explorer"
        explorer_dir.mkdir(parents=True)
        locked_file = explorer_dir / "thumbcache_locked.db"
        locked_file.write_bytes(b"locked")
        monkeypatch.setenv("LOCALAPPDATA", str(appdata))

        with patch.object(Path, "unlink", side_effect=PermissionError("File locked by explorer.exe")):
            res = clear_windows_thumbnail_cache()
            assert res["success"] is True
            assert res["files_cleared"] == 0


# ── Unit Tests: process_file & process_directory ──────────────────────────────

class TestProcessFileAndDirectory:
    def test_process_file_strip_mode(self, tmp_path: Path, sample_jpeg_with_thumbs):
        p = tmp_path / "photo.jpg"
        p.write_bytes(sample_jpeg_with_thumbs)

        res = process_file(p, mode="strip")
        assert res.status == "stripped"
        assert res.bytes_freed > 0
        assert p.stat().st_size < len(sample_jpeg_with_thumbs)
        assert ThumbnailCarver.extract_mpf_preview(p) is None

    def test_process_file_rebuild_mode(self, tmp_path: Path, clean_jpeg):
        p = tmp_path / "photo.jpg"
        p.write_bytes(clean_jpeg)

        res = process_file(p, mode="rebuild", size=(160, 120))
        assert res.status == "rebuilt"
        assert ThumbnailCarver.extract_exif_thumbnail(p) is not None

    def test_process_file_dry_run_leaves_file_untouched(self, tmp_path: Path, sample_jpeg_with_thumbs):
        p = tmp_path / "photo.jpg"
        p.write_bytes(sample_jpeg_with_thumbs)

        res = process_file(p, mode="strip", dry_run=True)
        assert res.status == "stripped"
        assert p.read_bytes() == sample_jpeg_with_thumbs  # 100% unchanged

    def test_process_file_backup_creation_and_force(self, tmp_path: Path, sample_jpeg_with_thumbs):
        p = tmp_path / "photo.jpg"
        p.write_bytes(sample_jpeg_with_thumbs)

        res = process_file(p, mode="strip", backup=True)
        assert res.status == "stripped"
        bak = tmp_path / "photo.jpg.bak"
        assert bak.exists()
        assert bak.read_bytes() == sample_jpeg_with_thumbs

        # Second run without force: skips
        res_skip = process_file(p, mode="strip", backup=True, force=False)
        assert res_skip.status == "skipped"

        # Second run with force: overwrites
        res_force = process_file(p, mode="strip", backup=True, force=True)
        assert res_force.status == "stripped"

    def test_process_directory_recursive_and_callback(self, test_photo_tree):
        cb_counts = []

        def on_progress(cur, tot, name):
            cb_counts.append((cur, tot, name))

        results = process_directory(test_photo_tree, mode="strip", progress_cb=on_progress)
        # Should process img_01.jpg, img_02.jpg, clean.jpg (3 files)
        assert len(results) == 3
        assert len(cb_counts) == 3
        assert cb_counts[-1][0] == cb_counts[-1][1] == 3


# ── Integration Tests: CLI fix-previews ───────────────────────────────────────

class TestCliFixPreviews:
    def test_fix_previews_strip_default(self, test_photo_tree, capsys):
        code = main(["fix-previews", str(test_photo_tree), "--no-banner"])
        assert code == 0

        # Check files were stripped
        assert ThumbnailCarver.extract_mpf_preview(test_photo_tree / "img_01.jpg") is None
        assert ThumbnailCarver.extract_mpf_preview(test_photo_tree / "vacation" / "img_02.jpg") is None

        captured = capsys.readouterr()
        assert "PREVIEWS" in captured.out or "SUMMARY" in captured.out or "scanned" in captured.out

    def test_fix_previews_rebuild_mode(self, test_photo_tree):
        code = main(["fix-previews", str(test_photo_tree), "--mode", "rebuild", "--size", "160x120", "--no-banner"])
        assert code == 0

        # Check new thumbnails are present
        assert ThumbnailCarver.extract_exif_thumbnail(test_photo_tree / "img_01.jpg") is not None
        assert ThumbnailCarver.extract_exif_thumbnail(test_photo_tree / "vacation" / "img_02.jpg") is not None

    def test_fix_previews_dry_run(self, test_photo_tree):
        orig_img1 = (test_photo_tree / "img_01.jpg").read_bytes()
        code = main(["fix-previews", str(test_photo_tree), "--dry-run", "--no-banner"])
        assert code == 0
        assert (test_photo_tree / "img_01.jpg").read_bytes() == orig_img1

    def test_fix_previews_backup(self, test_photo_tree):
        code = main(["fix-previews", str(test_photo_tree), "--backup", "--no-banner"])
        assert code == 0
        assert (test_photo_tree / "img_01.jpg.bak").exists()
        assert (test_photo_tree / "vacation" / "img_02.jpg.bak").exists()

    def test_fix_previews_clear_cache_flag(self, test_photo_tree, monkeypatch: pytest.MonkeyPatch):
        mock_clear = MagicMock(return_value={"success": True, "actions": ["sh_change_notify"], "files_cleared": 1, "errors": []})
        cli_mod = sys.modules.get("photo_healer.cli.main")
        if cli_mod:
            monkeypatch.setattr(cli_mod, "clear_windows_thumbnail_cache", mock_clear)
        monkeypatch.setattr("photo_healer.core.thumbnail.clear_windows_thumbnail_cache", mock_clear)

        code = main(["fix-previews", str(test_photo_tree), "--clear-cache", "--no-banner"])
        assert code == 0
        assert mock_clear.called

    def test_fix_previews_invalid_path_fails(self, tmp_path: Path):
        code = main(["fix-previews", str(tmp_path / "non_existent"), "--no-banner"])
        assert code == 1

    def test_fix_previews_invalid_size_format(self, test_photo_tree):
        code = main(["fix-previews", str(test_photo_tree), "--mode", "rebuild", "--size", "invalid_size", "--no-banner"])
        assert code == 1

    def test_fix_previews_quiet_mode(self, test_photo_tree, capsys):
        code = main(["fix-previews", str(test_photo_tree), "--quiet", "--no-banner"])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
