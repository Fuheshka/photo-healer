"""Unit tests for StreamResync - entropy stream analysis and restart marker recovery."""

from __future__ import annotations
import io
import struct
from pathlib import Path
import pytest

from photo_healer.core.resync import (
    StreamResync,
    RestartMarker,
    RestartCadence,
    ResyncResult,
)
from photo_healer.core.parser import JpegParser
from photo_healer.core.validator import JpegValidator
from tests.helpers import JPEGTestKit


class TestRestartMarkerScanner:
    """Tests for low-level RST marker scanning and byte-stuffing isolation."""

    def test_scan_clean_rst_sequence(self):
        # 8 intervals separated by RST0..RST6
        scan = bytearray()
        expected_markers = []
        for i in range(7):
            scan.extend(JPEGTestKit.stuffed_entropy_payload(length=32))
            expected_markers.append(i)
            scan.extend(JPEGTestKit.rst_marker(i))
        scan.extend(JPEGTestKit.stuffed_entropy_payload(length=32))

        markers = StreamResync.scan_restart_markers(bytes(scan))
        assert len(markers) == 7
        for idx, m in enumerate(markers):
            assert m.index == expected_markers[idx]
            assert m.code == 0xD0 + expected_markers[idx]
            assert m.payload_offset > m.offset

    def test_scan_skips_byte_stuffing_ff00(self):
        # Scan data containing multiple literal 0xFF bytes stuffed as 0xFF 0x00
        scan = b"\x12\x34\xff\x00\x56\x78\xff\x00\xff\x00\xab\xcd" + JPEGTestKit.rst_marker(0) + b"\xef\xff\x00\x01"
        markers = StreamResync.scan_restart_markers(scan)
        assert len(markers) == 1
        assert markers[0].index == 0
        assert markers[0].code == 0xD0
        assert markers[0].offset == 12

    def test_scan_handles_consecutive_fill_bytes_ffff(self):
        # JPEG standard allows fill bytes: 0xFF 0xFF 0xD2 -> valid RST2
        scan = b"\x01\x02\xff\xff\xff\xd2\x03\x04"
        markers = StreamResync.scan_restart_markers(scan)
        assert len(markers) == 1
        assert markers[0].index == 2
        assert markers[0].code == 0xD2
        assert markers[0].offset == 4  # Offset of the final 0xFF prefix

    def test_scan_ignores_corrupted_ff_non_marker(self):
        # Corrupt data with 0xFF followed by invalid marker codes in noise
        noise = b"\x00\x11\xff\x42\x33\xff\x01\x44\xff\xd8\x55" + JPEGTestKit.rst_marker(3) + b"\x66\x77"
        markers = StreamResync.scan_restart_markers(noise)
        assert len(markers) == 1
        assert markers[0].index == 3
        assert markers[0].code == 0xD3


class TestSequenceCadenceValidator:
    """Tests for cadence validation and false-positive RST filtering."""

    def test_valid_cadence_modulo_8(self):
        markers = [
            RestartMarker(index=i % 8, code=0xD0 + (i % 8), offset=i * 100, payload_offset=i * 100 + 2)
            for i in range(12)  # Goes past 7: 0..7, 0..3
        ]
        cadence = StreamResync.validate_cadence(markers)
        assert cadence.is_valid is True
        assert cadence.confidence == 1.0
        assert len(cadence.markers) == 12
        assert cadence.first_marker.index == 0

    def test_cadence_rejects_isolated_false_rst_in_noise(self):
        # Noise contains isolated RST5 at offset 50, then clean sequence RST1 -> RST2 -> RST3 -> RST4
        markers = [
            RestartMarker(index=5, code=0xD5, offset=50, payload_offset=52),  # False alarm
            RestartMarker(index=1, code=0xD1, offset=500, payload_offset=502),
            RestartMarker(index=2, code=0xD2, offset=600, payload_offset=602),
            RestartMarker(index=3, code=0xD3, offset=700, payload_offset=702),
            RestartMarker(index=4, code=0xD4, offset=800, payload_offset=802),
        ]
        cadence = StreamResync.validate_cadence(markers, min_run=3)
        assert cadence.is_valid is True
        assert len(cadence.markers) == 4
        assert cadence.first_marker.index == 1
        assert cadence.first_marker.offset == 500

    def test_cadence_detects_gap_and_lost_intervals(self):
        # Sequence with a jump: RST0 -> RST1 -> [gap of 2 intervals] -> RST4 -> RST5
        markers = [
            RestartMarker(index=0, code=0xD0, offset=100, payload_offset=102),
            RestartMarker(index=1, code=0xD1, offset=200, payload_offset=202),
            RestartMarker(index=4, code=0xD4, offset=500, payload_offset=502),
            RestartMarker(index=5, code=0xD5, offset=600, payload_offset=602),
        ]
        cadence = StreamResync.validate_cadence(markers, min_run=2)
        assert cadence.is_valid is True
        assert any("gap" in anom.lower() for anom in cadence.anomalies)

    def test_cadence_fails_when_below_min_run(self):
        # Only isolated random markers
        markers = [
            RestartMarker(index=0, code=0xD0, offset=100, payload_offset=102),
            RestartMarker(index=5, code=0xD5, offset=200, payload_offset=202),
            RestartMarker(index=2, code=0xD2, offset=300, payload_offset=302),
        ]
        cadence = StreamResync.validate_cadence(markers, min_run=3)
        assert cadence.is_valid is False
        assert cadence.confidence == 0.0


class TestDriAndExpectedMarkers:
    """Tests for DRI extraction and mathematical spatial coordinate calculation."""

    def test_extract_dri_from_donor(self):
        donor = (
            JPEGTestKit.SOI
            + JPEGTestKit.dqt(0)
            + JPEGTestKit.dri(restart_interval=32)
            + JPEGTestKit.sof0(640, 480)
            + JPEGTestKit.dht(0, 0)
            + JPEGTestKit.sos()
            + JPEGTestKit.EOI
        )
        dri = StreamResync.extract_dri(donor)
        assert dri == 32

    def test_extract_dri_returns_none_when_absent(self):
        donor = JPEGTestKit.minimal_donor_header(640, 480) + JPEGTestKit.EOI
        assert StreamResync.extract_dri(donor) is None

    def test_calculate_expected_markers(self):
        # 640x480 image with 16x16 MCUs = 40x30 = 1200 MCUs.
        # With restart interval 60: total intervals = 1200 / 60 = 20.
        # Expected RST markers = 20 - 1 = 19.
        expected = StreamResync.calculate_expected_markers(
            width=640, height=480, restart_interval=60, mcu_width=16, mcu_height=16
        )
        assert expected == 19

    def test_calculate_marker_spatial_offset(self):
        # 640 width = 40 MCUs per row.
        # Interval = 40 MCUs (exactly 1 row of MCUs = 16 pixels high).
        # Interval index 5 should start at MCU (0, 5), pixel Y = 5 * 16 = 80 px.
        mcu_x, mcu_y, pixel_y = StreamResync.calculate_marker_spatial_offset(
            interval_index=5, restart_interval=40, width=640, height=480, mcu_width=16, mcu_height=16
        )
        assert mcu_x == 0
        assert mcu_y == 5
        assert pixel_y == 80


class TestCorruptedPrefixResynchronization:
    """Tests for cutting corrupted prefix and syncing to first valid restart marker."""

    def test_resync_trim_zeros_prefix_64kb(self):
        # Generate complete JPEG with 10 intervals
        full_jpeg = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=10, interval_payload_len=100
        )
        # Apply 64KB TRIM zeros prefix, erasing header and initial intervals
        trim_zeros_data = JPEGTestKit.inject_trim_zeros_prefix(full_jpeg, zeros_len=200)
        donor_header = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(
            source=trim_zeros_data,
            donor=donor_header,
            pad_geometry=False,
        )

        assert result.status == "resynced"
        assert result.first_marker is not None
        assert result.first_marker.index >= 0
        assert result.cut_bytes_count > 0
        assert result.data.startswith(b"\xff\xd8")
        assert result.data.endswith(b"\xff\xd9")

    def test_resync_noise_prefix(self):
        # Bitstream preceded by 1KB of noise with false RST markers
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=8, interval_payload_len=80
        )
        damaged = JPEGTestKit.inject_noise_prefix(bitstream, noise_len=512, inject_false_rst=True)
        donor = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(
            source=damaged,
            donor=donor,
            pad_geometry=False,
            min_cadence_run=3,
        )

        assert result.status == "resynced"
        assert result.first_marker is not None
        # Must have skipped the false RST5 in the noise
        assert result.total_markers_found >= 3


class TestDummyIntervalPadding:
    """Tests for MCU geometry preservation using dummy restart intervals."""

    def test_pad_dummy_intervals_for_lost_prefix(self):
        # When first surviving marker is RST3, intervals 0, 1, 2 were lost.
        # With pad_geometry=True, 3 dummy intervals should be prepended.
        full_jpeg = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=12, interval_payload_len=64
        )
        # Cut after RST2 (so stream starts at RST2 -> interval 3 with marker RST3)
        parser = JpegParser(full_jpeg)
        header_len = len(parser.get_header_bytes())
        scan = full_jpeg[header_len:]
        markers = StreamResync.scan_restart_markers(scan)

        # Slice right before marker RST2 so first marker found is RST2
        cut_offset = markers[2].offset
        damaged = scan[cut_offset:]
        donor = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(
            source=damaged,
            donor=donor,
            pad_geometry=True,
        )

        assert result.status == "resynced"
        assert result.padded_intervals_count == 2  # Padded intervals 0 and 1 before RST2


class TestTruncatedTailAndEoiGuards:
    """Tests for damaged and truncated stream tails."""

    def test_resync_truncated_mid_interval(self):
        # Truncate file mid-payload without EOI
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=6, interval_payload_len=64
        )
        truncated = JPEGTestKit.inject_truncated_tail(bitstream, truncate_bytes=30, drop_eoi=True)
        donor = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(
            source=truncated,
            donor=donor,
        )

        assert result.status == "resynced"
        assert result.eoi_appended is True
        assert result.data.endswith(b"\xff\xd9")

    def test_resync_prevents_double_eoi(self):
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=6, interval_payload_len=64, terminal_eoi=True
        )
        donor = JPEGTestKit.minimal_donor_header(640, 480)
        result = StreamResync.resync_stream(source=bitstream, donor=donor)

        assert result.data.endswith(b"\xff\xd9")
        assert not result.data.endswith(b"\xff\xd9\xff\xd9")


class TestEndToEndResyncWorkflow:
    """Integration and file management tests."""

    def test_resync_injects_dri_into_donor_if_missing(self):
        # Bitstream has RST markers, but donor header has NO DRI.
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=8, num_intervals=6, include_dri=True
        )
        # Donor header has NO DRI
        donor_no_dri = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(
            source=bitstream,
            donor=donor_no_dri,
            restart_interval=8,
        )

        assert result.status == "resynced"
        # Repaired file must have DRI marker present in its header!
        parser = JpegParser(result.data)
        markers = parser.parse_markers()
        assert any(m.name == "DRI" and m.code == 0xDD for m in markers)

    def test_resync_save_to_resync_folder(self, tmp_path):
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=6, interval_payload_len=64
        )
        src_file = tmp_path / "corrupted_shot.jpg"
        src_file.write_bytes(bitstream)
        donor = JPEGTestKit.minimal_donor_header(640, 480)

        result = StreamResync.resync_stream(source=src_file, donor=donor)
        saved_path = StreamResync.save_resynced(result, source_path=src_file, output_dir=tmp_path / "_Resync")

        assert saved_path.exists()
        assert saved_path.name == "corrupted_shot_resynced.jpg"
        assert saved_path.read_bytes() == result.data
        # Source must remain untouched
        assert src_file.read_bytes() == bitstream

    def test_resync_polymorphic_sources(self):
        bitstream = JPEGTestKit.build_resync_bitstream(
            restart_interval=4, num_intervals=6, interval_payload_len=64
        )
        donor = JPEGTestKit.minimal_donor_header(640, 480)

        res_bytes = StreamResync.resync_stream(source=bitstream, donor=donor)
        res_stream = StreamResync.resync_stream(source=io.BytesIO(bitstream), donor=donor)

        assert res_bytes.data == res_stream.data
        assert res_bytes.total_markers_found == res_stream.total_markers_found
