"""Tests for photo_healer.core.splicer HeaderSplicer."""

import io
import pytest
from photo_healer.core.splicer import HeaderSplicer, SpliceResult
from tests.helpers import JPEGTestKit


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


