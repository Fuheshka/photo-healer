"""Tests for photo_healer.core.parser JpegParser."""

import pytest
from photo_healer.core.parser import JpegParser, Marker
from tests.helpers import JPEGTestKit


class TestJpegParser:
    def test_parse_markers_minimal_donor(self):
        donor_bytes = JPEGTestKit.minimal_donor_header(640, 480) + b"\x11\x22\x33" + JPEGTestKit.EOI
        parser = JpegParser(donor_bytes)
        markers = parser.parse_markers()

        marker_codes = [m.code for m in markers]
        assert marker_codes == [0xD8, 0xDB, 0xC0, 0xC4, 0xDA]
        assert markers[0].name == "SOI"
        assert markers[0].offset == 0
        assert markers[-1].name == "SOS"

    def test_get_header_bytes_extracts_up_to_sos_end(self):
        expected_header = JPEGTestKit.minimal_donor_header(640, 480)
        raw_donor = expected_header + b"\xaa\xbb\xcc\xdd" * 50 + JPEGTestKit.EOI

        parser = JpegParser(raw_donor)
        header = parser.get_header_bytes()

        assert header == expected_header
        assert header.startswith(b"\xff\xd8")
        assert header.endswith(JPEGTestKit.sos())
        assert b"\xaa\xbb\xcc\xdd" not in header
        assert not header.endswith(JPEGTestKit.EOI)

    def test_find_sos_offset(self):
        header_before_sos = JPEGTestKit.SOI + JPEGTestKit.dqt(0) + JPEGTestKit.sof0(640, 480) + JPEGTestKit.dht(0, 0)
        donor = header_before_sos + JPEGTestKit.sos() + b"payload" + JPEGTestKit.EOI

        parser = JpegParser(donor)
        sos_offset = parser.find_sos_offset()

        assert sos_offset == len(header_before_sos)

    def test_protection_against_false_sos_in_exif_app1(self):
        app1 = JPEGTestKit.app1_with_fake_sos()
        real_tail = (
            JPEGTestKit.dqt(0)
            + JPEGTestKit.sof0(1920, 1080)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
        )
        full_donor = JPEGTestKit.SOI + app1 + real_tail + b"\xff\x00bitstream" + JPEGTestKit.EOI

        parser = JpegParser(full_donor)
        sos_offset = parser.find_sos_offset()
        header = parser.get_header_bytes()

        # False SOS inside APP1 thumbnail should be ignored
        assert sos_offset == len(JPEGTestKit.SOI + app1 + JPEGTestKit.dqt(0) + JPEGTestKit.sof0(1920, 1080) + JPEGTestKit.dht(0, 0))
        assert header == JPEGTestKit.SOI + app1 + real_tail
        assert b"\xff\xc0" in header  # SOF0 is present
        assert len(header) == len(full_donor) - len(b"\xff\x00bitstream" + JPEGTestKit.EOI)

    def test_ff_fill_byte_padding_support(self):
        # JPEG allows fill bytes: 0xFF 0xFF 0xE0
        donor = (
            b"\xff\xd8"
            b"\xff\xff\xdb\x00\x43" + bytes(65)
            + JPEGTestKit.sos()
            + JPEGTestKit.EOI
        )
        parser = JpegParser(donor)
        markers = parser.parse_markers()
        assert [m.code for m in markers] == [0xD8, 0xDB, 0xDA]

    def test_missing_soi_raises_error(self):
        invalid_data = b"\x00\x00\x00\x00" + JPEGTestKit.sos()
        with pytest.raises(ValueError, match="SOI|Invalid JPEG"):
            JpegParser(invalid_data).parse_markers()

    def test_missing_sos_raises_error(self):
        data = JPEGTestKit.SOI + JPEGTestKit.dqt(0) + JPEGTestKit.EOI
        parser = JpegParser(data)
        with pytest.raises(ValueError, match="SOS marker not found"):
            parser.find_sos_offset()

    def test_truncated_marker_length_raises_error(self):
        # Marker present but only 1 byte of length
        data = JPEGTestKit.SOI + b"\xff\xe1\x00"
        parser = JpegParser(data)
        with pytest.raises(ValueError, match="Truncated|exceeds"):
            parser.parse_markers()

    def test_declared_length_exceeds_data_raises_error(self):
        # Declares 65535 bytes but only 4 bytes given
        data = JPEGTestKit.SOI + b"\xff\xdb\xff\xff" + b"\x00\x00"
        parser = JpegParser(data)
        with pytest.raises(ValueError, match="Truncated|exceeds"):
            parser.parse_markers()

    def test_parser_with_path_and_stream(self, tmp_path):
        donor = JPEGTestKit.minimal_donor_header(640, 480) + JPEGTestKit.EOI
        p = tmp_path / "test.jpg"
        p.write_bytes(donor)

        # From Path
        parser_path = JpegParser(p)
        assert parser_path.find_sos_offset() > 0

        # From Stream
        import io
        stream = io.BytesIO(donor)
        parser_stream = JpegParser(stream)
        assert parser_stream.find_sos_offset() > 0

    def test_parser_with_dri_marker(self):
        # DRI marker FF DD with length 4 (0x00 0x04) + 2 bytes restart interval
        dri = b"\xff\xdd\x00\x04\x00\x10"
        donor = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(0)
            + dri
            + JPEGTestKit.sof0(640, 480)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
            + JPEGTestKit.EOI
        )
        parser = JpegParser(donor)
        markers = parser.parse_markers()
        assert any(m.name == "DRI" and m.code == 0xDD for m in markers)

    def test_invalid_source_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Unsupported source type"):
            JpegParser(12345)

    def test_corrupt_marker_prefix_raises_error(self):
        # Starts with SOI, but next byte is not 0xFF
        corrupt = b"\xff\xd8\x42\x00\x00\x00"
        parser = JpegParser(corrupt)
        with pytest.raises(ValueError, match="Expected 0xFF marker prefix"):
            parser.parse_markers()


