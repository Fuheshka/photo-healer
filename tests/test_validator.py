"""Tests for photo_healer.core.validator JpegValidator."""

import pytest
from photo_healer.core.validator import JpegValidator, ValidationResult
from tests.helpers import JPEGTestKit


class TestJpegValidator:
    def test_validate_healthy_synthetic_jpeg(self):
        header = JPEGTestKit.minimal_donor_header(width=800, height=600)
        entropy_data = b"\x01\x02\x03\x04"
        jpeg_data = header + entropy_data + JPEGTestKit.EOI

        result = JpegValidator.validate(jpeg_data)
        assert isinstance(result, ValidationResult)
        assert result.is_valid is True
        assert result.width == 800
        assert result.height == 600
        assert result.components == 3
        assert len(result.errors) == 0

    def test_validate_rejects_non_jpeg(self):
        result = JpegValidator.validate(b"\x00" * 1024)
        assert result.is_valid is False
        assert any("SOI" in err for err in result.errors)

    def test_validate_rejects_missing_quantization_table(self):
        # Header without DQT
        header_no_dqt = (
            JPEGTestKit.SOI
            + JPEGTestKit.sof0(640, 480)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
        )
        jpeg_data = header_no_dqt + b"\x12\x34" + JPEGTestKit.EOI

        result = JpegValidator.validate(jpeg_data)
        assert result.is_valid is False
        assert any("DQT" in err for err in result.errors)

    def test_validate_rejects_missing_sof(self):
        header_no_sof = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(0)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
        )
        jpeg_data = header_no_sof + b"\x12\x34" + JPEGTestKit.EOI

        result = JpegValidator.validate(jpeg_data)
        assert result.is_valid is False
        assert any("SOF" in err for err in result.errors)

    def test_validate_detects_missing_eoi(self):
        header = JPEGTestKit.minimal_donor_header(640, 480)
        jpeg_data = header + b"\x12\x34\x56\x78"

        result = JpegValidator.validate(jpeg_data)
        assert result.is_valid is False
        assert any("EOI" in err for err in result.errors)

    def test_validate_grayscale_jpeg(self):
        header = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(0)
            + JPEGTestKit.sof0(320, 240, components=1)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos(components=1)
        )
        jpeg_data = header + b"\x99" + JPEGTestKit.EOI
        result = JpegValidator.validate(jpeg_data)
        assert result.is_valid is True
        assert result.components == 1
        assert result.width == 320
        assert result.height == 240

    def test_validate_from_path_and_stream(self, tmp_path):
        header = JPEGTestKit.minimal_donor_header(640, 480)
        jpeg_data = header + b"\x01" + JPEGTestKit.EOI

        p = tmp_path / "valid.jpg"
        p.write_bytes(jpeg_data)

        # From Path
        res_path = JpegValidator.validate(p)
        assert res_path.is_valid is True

        # From Stream
        import io
        stream = io.BytesIO(jpeg_data)
        res_stream = JpegValidator.validate(stream)
        assert res_stream.is_valid is True

    def test_invalid_source_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Unsupported source type"):
            JpegValidator.validate(12345)

    def test_validate_allows_trailing_sector_padding(self):
        header = JPEGTestKit.minimal_donor_header(640, 480)
        # Spliced/recovered file from disk block often ends with sector padding 0x00 bytes
        jpeg_data = header + b"\x01\x02" + JPEGTestKit.EOI + b"\x00" * 256
        res = JpegValidator.validate(jpeg_data)
        assert res.is_valid is True
        assert res.has_eoi is True


