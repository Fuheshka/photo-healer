"""Tests for photo_healer.core.splicer HeaderSplicer."""

import io
import struct
import pytest
from photo_healer.core.splicer import HeaderSplicer, SpliceResult
from photo_healer.core.validator import JpegValidator
from photo_healer.core.carver import ThumbnailCarver
from tests.helpers import JPEGTestKit
from tests.test_carver import build_exif_app1, build_mpf_app2, build_synthetic_jpeg


class TestHeaderSplicer:
    @pytest.fixture
    def donor_header(self):
        return JPEGTestKit.minimal_donor_header(640, 480)

    def test_splice_appends_missing_eoi(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        live_data = b"\x12\x34\x56\x78"

        result = splicer.splice_bytes(live_data)
        assert isinstance(result, SpliceResult)
        assert result.data.startswith(donor_header)
        assert result.data.endswith(b"\xff\xd9")
        assert result.data == donor_header + live_data + b"\xff\xd9"
        assert result.eoi_appended is True

    def test_splice_prevents_double_eoi(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        live_data = b"\x12\x34\x56\x78\xff\xd9"

        result = splicer.splice_bytes(live_data)
        assert result.data.endswith(b"\xff\xd9")
        assert not result.data.endswith(b"\xff\xd9\xff\xd9")
        assert result.data == donor_header + live_data
        assert result.eoi_appended is False

    def test_splice_with_offset_from_damaged_bytes(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        damaged = (b"\x00" * 4096) + b"\xde\xad\xbe\xef" + b"\xff\xd9"

        result = splicer.splice_target(damaged, entropy_offset=4096)
        assert result.data == donor_header + b"\xde\xad\xbe\xef\xff\xd9"
        assert result.entropy_offset == 4096

    def test_splice_with_auto_detected_entropy_offset(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        high_entropy_data = bytes((i * 137 + 23) % 256 for i in range(16384)) + b"\xff\xd9"
        damaged = (b"\x00" * 65536) + high_entropy_data

        result = splicer.splice_target(damaged, entropy_offset=None)
        assert result.entropy_offset == 65536
        assert result.data == donor_header + high_entropy_data

    def test_stream_and_bytes_parity(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        live_payload = bytes((i * 19 + 7) % 256 for i in range(32768)) + b"\xff\xd9"
        target_bytes = (b"\x00" * 65536) + live_payload

        # In-memory splice
        mem_result = splicer.splice_target(target_bytes, entropy_offset=65536)

        # Stream-based splice
        target_stream = io.BytesIO(target_bytes)
        out_stream = io.BytesIO()
        stream_result = splicer.splice_stream(target_stream, out_stream, entropy_offset=65536, chunk_size=4096)

        assert out_stream.getvalue() == mem_result.data
        assert stream_result.total_bytes == len(mem_result.data)

    def test_invalid_donor_header_raises_error(self):
        with pytest.raises(ValueError, match="SOI"):
            HeaderSplicer(b"NOT_JPEG_HEADER")

    def test_pure_zeros_target_raises_error(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        zeros = b"\x00" * 65536
        with pytest.raises(ValueError, match="No live entropy stream"):
            splicer.splice_target(zeros, entropy_offset=None)

    def test_splice_target_from_file_path(self, donor_header, tmp_path):
        splicer = HeaderSplicer(donor_header)
        payload = (b"\x00" * 512) + b"\x11\x22\x33\xff\xd9"
        p = tmp_path / "damaged.jpg"
        p.write_bytes(payload)

        result = splicer.splice_target(p, entropy_offset=512)
        assert result.data == donor_header + b"\x11\x22\x33\xff\xd9"

    def test_invalid_target_type_raises_type_error(self, donor_header):
        splicer = HeaderSplicer(donor_header)
        with pytest.raises(TypeError, match="Unsupported target type"):
            splicer.splice_target(12345)

    def test_non_seekable_stream_requires_entropy_offset(self, donor_header):
        class NonSeekableStream(io.BytesIO):
            def seekable(self):
                return False

        splicer = HeaderSplicer(donor_header)
        ns = NonSeekableStream(b"\x00" * 512 + b"\x11" * 512)
        out = io.BytesIO()
        with pytest.raises(ValueError, match="must be seekable"):
            splicer.splice_stream(ns, out, entropy_offset=None)

    def test_strip_donor_thumbnail_exif_ifd1_default(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=True)
        donor_header = JPEGTestKit.SOI + app1 + JPEGTestKit.minimal_donor_header(640, 480)[2:]
        live_data = b"\x12\x34\x56\x78\xff\xd9"

        # By default, HeaderSplicer must strip donor thumbnail
        splicer = HeaderSplicer(donor_header)
        res = splicer.splice_bytes(live_data)

        # 1. Structure must be a valid JPEG
        val = JpegValidator.validate(res.data)
        assert val.is_valid
        assert val.width == 640
        assert val.height == 480

        # 2. ThumbnailCarver must NOT find the donor's thumbnail
        carver = ThumbnailCarver()
        assert carver.extract_exif_thumbnail(res.data) is None

    def test_strip_donor_thumbnail_exif_ifd1_big_endian(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian=">", thumb_in_ifd1=True)
        donor_header = JPEGTestKit.SOI + app1 + JPEGTestKit.minimal_donor_header(640, 480)[2:]
        live_data = b"\x12\x34\x56\x78\xff\xd9"

        splicer = HeaderSplicer(donor_header)
        res = splicer.splice_bytes(live_data)

        val = JpegValidator.validate(res.data)
        assert val.is_valid
        carver = ThumbnailCarver()
        assert carver.extract_exif_thumbnail(res.data) is None

    def test_keep_donor_thumbnail_flag(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=True)
        donor_header = JPEGTestKit.SOI + app1 + JPEGTestKit.minimal_donor_header(640, 480)[2:]
        live_data = b"\x12\x34\x56\x78\xff\xd9"

        # When strip_thumbnail=False, donor thumbnail must be preserved
        splicer = HeaderSplicer(donor_header, strip_thumbnail=False)
        res = splicer.splice_bytes(live_data)

        val = JpegValidator.validate(res.data)
        assert val.is_valid
        carver = ThumbnailCarver()
        extracted = carver.extract_exif_thumbnail(res.data)
        assert extracted == thumb_bytes

    def test_strip_donor_thumbnail_mpf_app2(self):
        preview_bytes = build_synthetic_jpeg(width=1920, height=1080)
        base = build_synthetic_jpeg(640, 480)
        app2_mpf = build_mpf_app2([preview_bytes], primary_len=len(base), endian="<", mpf_header_file_offset=10)
        # Synthetic ICC profile in APP2 to verify non-MPF APP2 is preserved
        icc_payload = b"ICC_PROFILE\x00\x01\x00" + b"\x00" * 20
        app2_icc = b"\xff\xe2" + struct.pack(">H", len(icc_payload) + 2) + icc_payload

        donor_header = JPEGTestKit.SOI + app2_mpf + app2_icc + base[2:]
        live_data = b"\xaa\xbb\xcc\xdd\xff\xd9"

        splicer = HeaderSplicer(donor_header)
        res = splicer.splice_bytes(live_data)

        val = JpegValidator.validate(res.data)
        assert val.is_valid

        # MPF must be stripped
        carver = ThumbnailCarver()
        assert carver.extract_mpf_preview(res.data) is None
        assert b"MPF\x00" not in res.data[:res.donor_header_len]

        # ICC profile APP2 must be preserved
        assert b"ICC_PROFILE\x00" in res.data[:res.donor_header_len]

    def test_strip_donor_thumbnail_ifd0_direct(self):
        thumb_bytes = build_synthetic_jpeg(width=160, height=120)
        app1 = build_exif_app1(thumb_bytes, endian="<", thumb_in_ifd1=False)
        donor_header = JPEGTestKit.SOI + app1 + JPEGTestKit.minimal_donor_header(640, 480)[2:]
        live_data = b"\x12\x34\x56\x78\xff\xd9"

        splicer = HeaderSplicer(donor_header)
        res = splicer.splice_bytes(live_data)

        val = JpegValidator.validate(res.data)
        assert val.is_valid
        carver = ThumbnailCarver()
        assert carver.extract_exif_thumbnail(res.data) is None


