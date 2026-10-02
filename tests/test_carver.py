"""Tests for ThumbnailCarver - EXIF, MPF, and raw stream carving with automatic fallback."""

import io
import struct
from pathlib import Path
import pytest
from PIL import Image

from tests.helpers import JPEGTestKit
from photo_healer.core.carver import ThumbnailCarver, CarvedPreview, RescueResult
from photo_healer.core.validator import JpegValidator


def build_synthetic_jpeg(width: int = 640, height: int = 480) -> bytes:
    """Builds a minimal synthetic valid JPEG byte stream."""
    return JPEGTestKit.minimal_donor_header(width=width, height=height) + b"\x00" * 32 + JPEGTestKit.EOI


def build_exif_app1(
    thumbnail_bytes: bytes,
    endian: str = "<",
    thumb_in_ifd1: bool = True,
) -> bytes:
    """Constructs a compliant EXIF APP1 segment containing an embedded thumbnail."""
    byte_order_mark = b"II" if endian == "<" else b"MM"
    tiff_header = byte_order_mark + struct.pack(endian + "HI", 42, 8)  # IFD0 at offset 8

    # IFD0 entry: ImageDescription tag 0x010E
    num_entries_ifd0 = 1
    ifd0_entry1 = struct.pack(endian + "HHII", 0x010E, 2, 4, 0x41424300)

    if thumb_in_ifd1:
        # IFD1 offset immediately following IFD0
        ifd1_offset = 8 + 2 + 12 + 4
        next_ifd_ptr = struct.pack(endian + "I", ifd1_offset)

        num_entries_ifd1 = 2
        # Offset to thumbnail data from TIFF header start:
        # IFD1 starts at ifd1_offset, length is 2 + 2*12 + 4 = 30 bytes
        thumb_offset = ifd1_offset + 30
        thumb_len = len(thumbnail_bytes)

        tag_0201 = struct.pack(endian + "HHII", 0x0201, 4, 1, thumb_offset)
        tag_0202 = struct.pack(endian + "HHII", 0x0202, 4, 1, thumb_len)
        ifd1_next = struct.pack(endian + "I", 0)

        tiff_body = (
            tiff_header
            + struct.pack(endian + "H", num_entries_ifd0)
            + ifd0_entry1
            + next_ifd_ptr
            + struct.pack(endian + "H", num_entries_ifd1)
            + tag_0201
            + tag_0202
            + ifd1_next
            + thumbnail_bytes
        )
    else:
        # Thumbnail tags directly inside IFD0
        thumb_offset = 8 + 2 + (3 * 12) + 4
        thumb_len = len(thumbnail_bytes)
        num_entries_ifd0 = 3
        tag_0201 = struct.pack(endian + "HHII", 0x0201, 4, 1, thumb_offset)
        tag_0202 = struct.pack(endian + "HHII", 0x0202, 4, 1, thumb_len)
        next_ifd_ptr = struct.pack(endian + "I", 0)

        tiff_body = (
            tiff_header
            + struct.pack(endian + "H", num_entries_ifd0)
            + ifd0_entry1
            + tag_0201
            + tag_0202
            + next_ifd_ptr
            + thumbnail_bytes
        )

    exif_payload = b"Exif\x00\x00" + tiff_body
    seg_len = len(exif_payload) + 2
    return b"\xff\xe1" + struct.pack(">H", seg_len) + exif_payload


def build_mpf_app2(
    preview_bytes_list: list[bytes],
    primary_len: int,
    endian: str = "<",
    mpf_header_file_offset: int = 0,
) -> bytes:
    """Constructs a compliant CIPA DC-007 MPF APP2 segment."""
    byte_order_mark = b"II" if endian == "<" else b"MM"
    mp_header = byte_order_mark + struct.pack(endian + "HI", 42, 8)  # Index IFD at offset 8

    num_images = 1 + len(preview_bytes_list)
    # Tags:
    # 0xB000: MP Format Version (UNDEFINED, 4 bytes, "0100")
    # 0xB001: NumberOfImages (LONG, 4 bytes)
    # 0xB002: MPImageList (UNDEFINED, 16 * num_images bytes)
    version_tag = struct.pack(endian + "HH", 0xB000, 7) + struct.pack(endian + "I", 4) + b"0100"
    num_images_tag = struct.pack(endian + "HHII", 0xB001, 4, 1, num_images)

    # Offset to MP Entry Table from MP Header start:
    # MP Header (8 bytes) + num_entries (2) + 3 tags (36) + next_ifd (4) = 50 bytes
    entry_table_offset = 50
    image_list_tag = struct.pack(endian + "HHII", 0xB002, 7, 16 * num_images, entry_table_offset)
    next_ifd = struct.pack(endian + "I", 0)

    # Build MP Entry records (16 bytes each)
    # Entry 0: Primary Image
    entries = [struct.pack(endian + "IIIHH", 0x030000, primary_len, 0, 0, 0)]

    # Running offset for secondary images from MP Header start
    current_rel_offset = max(0, primary_len - mpf_header_file_offset)
    for prev_bytes in preview_bytes_list:
        p_len = len(prev_bytes)
        entries.append(struct.pack(endian + "IIIHH", 0x010002, p_len, current_rel_offset, 0, 0))
        current_rel_offset += p_len

    mp_body = (
        mp_header
        + struct.pack(endian + "H", 3)
        + version_tag
        + num_images_tag
        + image_list_tag
        + next_ifd
        + b"".join(entries)
    )

    seg_len = len(mp_body) + 6  # +2 len, +4 'MPF\x00'
    return b"\xff\xe2" + struct.pack(">H", seg_len) + b"MPF\x00" + mp_body


class TestThumbnailCarverExif:
    """Tests for extracting EXIF thumbnails from APP1 segments."""

    def test_extract_exif_thumbnail_little_endian(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=True)
        primary = JPEGTestKit.SOI + app1 + build_synthetic_jpeg(width=640, height=480)[2:]

        carver = ThumbnailCarver()
        extracted = carver.extract_exif_thumbnail(primary)

        assert extracted is not None
        assert extracted == thumb_bytes
        val = JpegValidator.validate(extracted)
        assert val.is_valid
        assert val.width == 160
        assert val.height == 120

        # Verify decoding with Pillow
        with Image.open(io.BytesIO(extracted)) as img:
            img.verify()

    def test_extract_exif_thumbnail_big_endian(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian=">", thumb_in_ifd1=True)
        primary = JPEGTestKit.SOI + app1 + build_synthetic_jpeg(width=640, height=480)[2:]

        extracted = ThumbnailCarver.extract_exif_thumbnail(primary)
        assert extracted is not None
        assert extracted == thumb_bytes
        val = JpegValidator.validate(extracted)
        assert val.width == 160
        assert val.height == 120

    def test_extract_exif_thumbnail_in_ifd0_fallback(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=False)
        primary = JPEGTestKit.SOI + app1 + build_synthetic_jpeg(width=800, height=600)[2:]

        extracted = ThumbnailCarver.extract_exif_thumbnail(primary)
        assert extracted is not None
        assert extracted == thumb_bytes

    def test_extract_exif_thumbnail_in_corrupted_header(self):
        """Even if the primary JPEG header is damaged, surviving APP1 yields the thumbnail."""
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<")
        # Prepend 1024 zero bytes (damaged header) and append random noise
        damaged = b"\x00" * 1024 + app1 + b"\x55" * 2048

        extracted = ThumbnailCarver.extract_exif_thumbnail(damaged)
        assert extracted is not None
        assert extracted == thumb_bytes

    def test_extract_exif_thumbnail_missing_returns_none(self):
        clean_jpeg = build_synthetic_jpeg(640, 480)
        assert ThumbnailCarver.extract_exif_thumbnail(clean_jpeg) is None


class TestThumbnailCarverMpf:
    """Tests for extracting Multi-Picture Format (MPF) full-resolution previews from APP2."""

    def test_extract_mpf_preview_little_endian(self):
        preview_bytes = build_synthetic_jpeg(width=1920, height=1080)

        # Build primary JPEG with MPF APP2
        base_header = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(0)
            + JPEGTestKit.sof0(3264, 2448)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
        )
        dummy_scan = b"\x12\x34" * 100 + JPEGTestKit.EOI

        # Calculate exact offsets
        # SOI (2 bytes) + APP2 marker
        mpf_header_file_offset = 2 + 4 + 6  # SOI (2) + APP2 marker & len (4) + 'MPF\x00' (4) -> start of MP Header = 2 + 4 + 4 = 10
        # Let's compute primary total length before preview is appended
        temp_app2 = build_mpf_app2([preview_bytes], primary_len=0, endian="<", mpf_header_file_offset=10)
        primary_len = len(JPEGTestKit.SOI + temp_app2 + base_header[2:] + dummy_scan)
        # Now generate exact APP2 with known primary_len
        app2 = build_mpf_app2([preview_bytes], primary_len=primary_len, endian="<", mpf_header_file_offset=10)
        full_file = JPEGTestKit.SOI + app2 + base_header[2:] + dummy_scan + preview_bytes

        carver = ThumbnailCarver()
        extracted = carver.extract_mpf_preview(full_file)

        assert extracted is not None
        assert extracted == preview_bytes
        val = JpegValidator.validate(extracted)
        assert val.is_valid
        assert val.width == 1920
        assert val.height == 1080

        # Verify decoding with Pillow
        with Image.open(io.BytesIO(extracted)) as img:
            img.verify()

    def test_extract_mpf_preview_big_endian(self):
        preview_bytes = build_synthetic_jpeg(width=1920, height=1080)
        base = build_synthetic_jpeg(3264, 2448)
        mpf_offset = 10
        temp_app2 = build_mpf_app2([preview_bytes], primary_len=0, endian=">", mpf_header_file_offset=mpf_offset)
        primary_len = len(JPEGTestKit.SOI + temp_app2 + base[2:])
        app2 = build_mpf_app2([preview_bytes], primary_len=primary_len, endian=">", mpf_header_file_offset=mpf_offset)
        full_file = JPEGTestKit.SOI + app2 + base[2:] + preview_bytes

        extracted = ThumbnailCarver.extract_mpf_preview(full_file)
        assert extracted is not None
        assert extracted == preview_bytes

    def test_extract_mpf_preview_selects_largest_resolution(self):
        small_preview = build_synthetic_jpeg(width=640, height=480)
        large_preview = build_synthetic_jpeg(width=1920, height=1080)

        base = build_synthetic_jpeg(3264, 2448)
        mpf_offset = 10
        temp_app2 = build_mpf_app2([small_preview, large_preview], primary_len=0, endian="<", mpf_header_file_offset=mpf_offset)
        primary_len = len(JPEGTestKit.SOI + temp_app2 + base[2:])
        app2 = build_mpf_app2([small_preview, large_preview], primary_len=primary_len, endian="<", mpf_header_file_offset=mpf_offset)
        full_file = JPEGTestKit.SOI + app2 + base[2:] + small_preview + large_preview

        extracted = ThumbnailCarver.extract_mpf_preview(full_file)
        assert extracted is not None
        # Should choose 1920x1080
        val = JpegValidator.validate(extracted)
        assert val.width == 1920
        assert val.height == 1080
        assert extracted == large_preview

    def test_extract_mpf_preview_missing_returns_none(self):
        clean_jpeg = build_synthetic_jpeg(640, 480)
        assert ThumbnailCarver.extract_mpf_preview(clean_jpeg) is None


class TestThumbnailCarverRawStream:
    """Tests for raw byte stream carving of independent FF D8 FF ... FF D9 streams."""

    def test_raw_stream_carve_locates_independent_jpegs(self):
        img1 = build_synthetic_jpeg(width=160, height=120)
        img2 = build_synthetic_jpeg(width=1920, height=1080)
        stream_data = b"\x00" * 512 + img1 + b"\xaa\xbb\xcc" * 200 + img2 + b"\x00" * 1024

        carver = ThumbnailCarver()
        carved = carver.raw_stream_carve(stream_data)

        assert len(carved) == 2
        assert img1 in carved
        assert img2 in carved
        assert JpegValidator.validate(carved[0]).is_valid
        assert JpegValidator.validate(carved[1]).is_valid

    def test_raw_stream_carve_handles_byte_stuffing_and_rst_markers(self):
        # Build JPEG with scan containing byte stuffing 0xFF 0x00 and RST 0xFF 0xD0
        header = JPEGTestKit.minimal_donor_header(width=320, height=240)
        scan = b"\x11\x22" + b"\xff\x00" + b"\x33\x44" + b"\xff\xd0" + b"\x55\x66" + JPEGTestKit.EOI
        img = header + scan
        garbage_wrapped = b"\x00" * 100 + img + b"\x00" * 100

        carved = ThumbnailCarver.raw_stream_carve(garbage_wrapped)
        assert len(carved) == 1
        assert carved[0] == img

    def test_raw_stream_carve_pure_zeros_returns_empty(self):
        assert ThumbnailCarver.raw_stream_carve(b"\x00" * 65536) == []


class TestThumbnailCarverFallbackAndRescue:
    """Tests for automatic fallback mode when donor transplantation fails or entropy is dead."""

    def test_extract_best_preview_prefers_mpf_over_exif(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        preview_bytes = build_synthetic_jpeg(width=1920, height=1080)

        app1 = build_exif_app1(thumb_bytes, endian="<")
        # Generate full structure
        base_header = (
            JPEGTestKit.SOI
            + app1
            + JPEGTestKit.dqt(0)
            + JPEGTestKit.sof0(3264, 2448)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
        )
        dummy_scan = b"\x12\x34" * 10 + JPEGTestKit.EOI
        mpf_offset = 2 + len(app1) + 4 + 4  # SOI + APP1 + APP2 tag + 'MPF\x00'
        temp_app2 = build_mpf_app2([preview_bytes], primary_len=0, endian="<", mpf_header_file_offset=mpf_offset)
        primary_len = len(JPEGTestKit.SOI + app1 + temp_app2 + base_header[2 + len(app1):] + dummy_scan)
        app2 = build_mpf_app2([preview_bytes], primary_len=primary_len, endian="<", mpf_header_file_offset=mpf_offset)

        full_file = JPEGTestKit.SOI + app1 + app2 + base_header[2 + len(app1):] + dummy_scan + preview_bytes

        carver = ThumbnailCarver()
        best = carver.extract_best_preview(full_file)

        assert best is not None
        assert best.preview_type == "mpf"
        assert best.width == 1920
        assert best.height == 1080
        assert best.data == preview_bytes

    def test_extract_best_preview_falls_back_to_exif(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<")
        full_file = JPEGTestKit.SOI + app1 + build_synthetic_jpeg(640, 480)[2:]

        best = ThumbnailCarver.extract_best_preview(full_file)
        assert best is not None
        assert best.preview_type == "exif_thumb"
        assert best.width == 160
        assert best.height == 120
        assert best.data == thumb_bytes

    def test_fallback_rescue_transplants_healthy_target(self):
        donor = JPEGTestKit.minimal_donor_header(640, 480)
        # Target with high entropy stream
        import os
        random_entropy = os.urandom(20000)
        target = b"\x00" * 65536 + random_entropy + JPEGTestKit.EOI

        carver = ThumbnailCarver()
        result = carver.fallback_rescue(target, donor_header=donor)

        assert result.status == "transplanted"
        assert result.data is not None
        assert result.data.startswith(JPEGTestKit.SOI)

    def test_fallback_rescue_falls_back_to_preview_on_dead_entropy(self, tmp_path):
        """When target entropy is 100% zeroes, transplantation fails and carver rescues thumbnail."""
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<")

        # Corrupted target: APP1 survived, but entropy stream is completely zeroed out
        damaged_target = b"\x00" * 512 + app1 + b"\x00" * 65536
        target_file = tmp_path / "corrupt_camera_photo.jpg"
        target_file.write_bytes(damaged_target)

        donor = JPEGTestKit.minimal_donor_header(3264, 2448)

        carver = ThumbnailCarver()
        result = carver.fallback_rescue(target_file, donor_header=donor, save=True)

        assert result.status == "recovered_exif_thumb"
        assert result.data == thumb_bytes
        assert result.saved_path is not None
        assert result.saved_path.exists()
        assert result.saved_path.name == "corrupt_camera_photo_thumb.jpg"
        assert result.saved_path.parent.name == "_Previews"

        # Verify the saved image decodes with Pillow
        with Image.open(result.saved_path) as img:
            img.verify()


class TestThumbnailCarverSafeSaving:
    """Tests for safe saving of carved previews in _Previews/ according to careful rules."""

    def test_save_preview_creates_subfolder_and_preserves_original(self, tmp_path):
        original_file = tmp_path / "IMG_20261002_001.jpg"
        original_content = b"ORIGINAL_CORRUPT_BYTES_NEVER_MODIFY"
        original_file.write_bytes(original_content)

        preview_data = build_synthetic_jpeg(1920, 1080)

        carver = ThumbnailCarver()
        saved_path = carver.save_preview(original_file, preview_data, preview_type="preview")

        # 1. Verify original file is 100% untouched
        assert original_file.read_bytes() == original_content

        # 2. Verify destination file
        assert saved_path.exists()
        assert saved_path.parent == tmp_path / "_Previews"
        assert saved_path.name == "IMG_20261002_001_preview.jpg"
        assert saved_path.read_bytes() == preview_data

        # 3. Test thumbnail suffix
        thumb_data = build_synthetic_jpeg(160, 120)
        saved_thumb = carver.save_preview(original_file, thumb_data, preview_type="thumb")
        assert saved_thumb.name == "IMG_20261002_001_thumb.jpg"
        assert saved_thumb.read_bytes() == thumb_data

    def test_invalid_input_type_raises_type_error(self):
        with pytest.raises(TypeError):
            ThumbnailCarver.extract_exif_thumbnail(12345)
        with pytest.raises(TypeError):
            ThumbnailCarver.extract_mpf_preview(["invalid"])
        with pytest.raises(TypeError):
            ThumbnailCarver.raw_stream_carve(None)
