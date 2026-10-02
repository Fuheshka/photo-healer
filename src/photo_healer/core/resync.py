"""JPEG entropy stream resynchronization and restart marker recovery engine.

Analyzes Huffman entropy bitstreams, discovers valid sequences of restart markers
(RST0-RST7: 0xFF 0xD0 - 0xFF 0xD7) while accounting for byte stuffing (0xFF 0x00),
calculates restart intervals (DRI: 0xFF 0xDD), cuts corrupted prefix porridge,
preserves MCU spatial geometry with dummy intervals, and guards against broken tails.
Complies with ITU-T T.81 / ISO/IEC 10918-1 standards.
"""

from __future__ import annotations
import io
import math
import struct
from pathlib import Path
from dataclasses import dataclass, field
from typing import Sequence

from photo_healer.core.parser import JpegParser, Marker


@dataclass(frozen=True)
class RestartMarker:
    """Represents a discovered JPEG restart marker."""
    index: int  # 0 to 7 (from 0xD0..0xD7)
    code: int   # 0xD0 to 0xD7
    offset: int  # Byte offset of the 0xFF marker prefix
    payload_offset: int  # Byte offset immediately following the marker


@dataclass(frozen=True)
class RestartCadence:
    """Evaluated restart marker sequence and cadence metrics."""
    markers: list[RestartMarker]
    is_valid: bool
    stride: int = 1
    confidence: float = 0.0
    first_marker: RestartMarker | None = None
    anomalies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResyncResult:
    """Result of stream resynchronization."""
    data: bytes
    status: str  # "resynced", "unchanged", "no_markers_found", "failed"
    restart_interval: int | None
    first_marker: RestartMarker | None
    total_markers_found: int
    cut_bytes_count: int
    padded_intervals_count: int
    eoi_appended: bool
    saved_path: Path | None = None
    details: str = ""


class StreamResync:
    """Entropy stream resynchronization and restart marker alignment engine."""

    @staticmethod
    def _to_bytes(source: bytes | io.BufferedIOBase | io.RawIOBase | Path | str) -> bytes:
        if isinstance(source, (str, Path)):
            return Path(source).read_bytes()
        elif isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
            pos = source.tell() if source.seekable() else 0
            data = source.read()
            if source.seekable():
                source.seek(pos)
            return data
        elif isinstance(source, (bytes, bytearray)):
            return bytes(source)
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")

    @classmethod
    def scan_restart_markers(cls, data: bytes, base_offset: int = 0) -> list[RestartMarker]:
        """Scans entropy bitstream for valid RST0..RST7 markers.

        Accounts for byte stuffing (0xFF 0x00) and consecutive fill bytes (0xFF 0xFF...).
        """
        markers: list[RestartMarker] = []
        n = len(data)
        i = 0

        while i < n:
            b = data[i]
            if b != 0xFF:
                i += 1
                continue

            # Found 0xFF prefix: skip consecutive 0xFF fill bytes
            while i < n and data[i] == 0xFF:
                i += 1

            if i >= n:
                break

            marker_start = i - 1  # 0xFF immediately preceding the code
            code = data[i]
            payload_start = i + 1
            i += 1

            # 0x00 is byte-stuffed 0xFF; 0xFF was fill byte
            if code in (0x00, 0xFF):
                continue

            # Check if code is in RST0..RST7 range (0xD0..0xD7)
            if 0xD0 <= code <= 0xD7:
                markers.append(
                    RestartMarker(
                        index=code - 0xD0,
                        code=code,
                        offset=base_offset + marker_start,
                        payload_offset=base_offset + payload_start,
                    )
                )

        return markers

    @classmethod
    def validate_cadence(cls, markers: Sequence[RestartMarker], min_run: int = 2) -> RestartCadence:
        """Validates sequential cadence of restart markers modulo 8.

        Filters out isolated false-positive markers in random noise and identifies
        continuous runs: (m[k+1].index - m[k].index) % 8 == 1.
        """
        if not markers:
            return RestartCadence(markers=[], is_valid=False, confidence=0.0, first_marker=None)

        if len(markers) < min_run:
            # Not enough markers to confirm cadence
            return RestartCadence(
                markers=list(markers),
                is_valid=False,
                confidence=0.0,
                first_marker=markers[0] if markers else None,
                anomalies=["Insufficient markers to establish cadence threshold"],
            )

        # Find continuous chains of valid progression (modulo 8)
        chains: list[list[RestartMarker]] = []
        current_chain: list[RestartMarker] = [markers[0]]
        anomalies: list[str] = []

        for i in range(1, len(markers)):
            prev = markers[i - 1]
            curr = markers[i]
            step = (curr.index - prev.index) % 8

            if step == 1:
                # Perfect consecutive progression
                current_chain.append(curr)
            elif 1 < step <= 4 and len(current_chain) >= 2:
                # Plausible gap: only accepted within an already established sequence
                lost = step - 1
                anomalies.append(
                    f"Gap detected between RST{prev.index} (offset {prev.offset}) "
                    f"and RST{curr.index} (offset {curr.offset}): {lost} lost interval(s)"
                )
                current_chain.append(curr)
            else:
                # Cadence break / noise disruption or isolated false marker
                if len(current_chain) >= min_run:
                    chains.append(current_chain)
                current_chain = [curr]


        if current_chain:
            chains.append(current_chain)

        # Select the longest / highest quality chain
        valid_chains = [c for c in chains if len(c) >= min_run]
        if not valid_chains:
            return RestartCadence(
                markers=list(markers),
                is_valid=False,
                confidence=0.0,
                first_marker=markers[0],
                anomalies=["No sequence met minimum cadence run threshold"],
            )

        best_chain = max(valid_chains, key=len)
        confidence = min(1.0, len(best_chain) / max(len(markers), 1))

        return RestartCadence(
            markers=best_chain,
            is_valid=True,
            stride=1,
            confidence=confidence,
            first_marker=best_chain[0],
            anomalies=anomalies,
        )

    @classmethod
    def extract_dri(cls, donor: bytes | io.BufferedIOBase | Path | str) -> int | None:
        """Extracts the restart interval (in MCUs) from a DRI (0xFF 0xDD) marker segment."""
        donor_bytes = cls._to_bytes(donor)
        n = len(donor_bytes)
        i = 0

        while i < n - 5:
            if donor_bytes[i] == 0xFF and donor_bytes[i + 1] == 0xDD:
                # Found DRI: read length
                seg_len = struct.unpack(">H", donor_bytes[i + 2 : i + 4])[0]
                if seg_len == 4:
                    interval = struct.unpack(">H", donor_bytes[i + 4 : i + 6])[0]
                    return interval if interval > 0 else None
                i += 2 + seg_len
                continue
            i += 1

        return None

    @classmethod
    def calculate_expected_markers(
        cls,
        width: int,
        height: int,
        restart_interval: int,
        mcu_width: int = 16,
        mcu_height: int = 16,
    ) -> int:
        """Calculates expected total number of RST markers in a standard JPEG image."""
        if restart_interval <= 0:
            return 0
        mcu_cols = math.ceil(width / mcu_width)
        mcu_rows = math.ceil(height / mcu_height)
        total_mcus = mcu_cols * mcu_rows
        total_intervals = math.ceil(total_mcus / restart_interval)
        return max(0, total_intervals - 1)

    @classmethod
    def calculate_marker_spatial_offset(
        cls,
        interval_index: int,
        restart_interval: int,
        width: int,
        height: int,
        mcu_width: int = 16,
        mcu_height: int = 16,
    ) -> tuple[int, int, int]:
        """Calculates (mcu_x, mcu_y, pixel_y) spatial location for a given interval index."""
        mcu_cols = math.ceil(width / mcu_width)
        mcu_offset = interval_index * restart_interval
        mcu_x = mcu_offset % mcu_cols
        mcu_y = mcu_offset // mcu_cols
        pixel_y = mcu_y * mcu_height
        return mcu_x, mcu_y, pixel_y

    @classmethod
    def create_dummy_restart_interval(cls, interval_index: int, restart_interval: int = 1) -> bytes:
        """Generates synthetic neutral gray dummy MCUs terminated by RST(interval_index % 8).

        In standard baseline JPEG luminance/chrominance tables:
        A DC=0, AC=0 MCU encodes as exactly 4 bytes (0x28 0xA2 0x8A 0x00).
        """
        dummy_mcu = b"\x28\xa2\x8a\x00"
        payload = dummy_mcu * max(1, restart_interval)
        rst = cls.build_rst_marker(interval_index)
        return payload + rst

    @staticmethod
    def build_rst_marker(index: int) -> bytes:
        """Returns raw 2-byte restart marker 0xFF 0xDm."""
        return b"\xff" + bytes([0xD0 + (index % 8)])

    @staticmethod
    def build_dri_marker(restart_interval: int) -> bytes:
        """Returns standard 6-byte DRI segment: 0xFF 0xDD 0x00 0x04 <interval>."""
        return b"\xff\xdd\x00\x04" + struct.pack(">H", restart_interval)

    @classmethod
    def inject_dri_into_header(cls, header: bytes, restart_interval: int) -> bytes:
        """Injects a DRI marker segment before the SOS (0xFF 0xDA) marker if not already present."""
        if b"\xff\xdd" in header:
            return header

        sos_idx = header.rfind(b"\xff\xda")
        if sos_idx == -1:
            return header

        dri = cls.build_dri_marker(restart_interval)
        return header[:sos_idx] + dri + header[sos_idx:]

    @classmethod
    def repair_tail(cls, data: bytes) -> tuple[bytes, bool]:
        """Ensures the entropy bitstream cleanly terminates with a single EOI (0xFF 0xD9) marker."""
        if data.endswith(b"\xff\xd9"):
            return data, False

        # If data ends with a solitary 0xFF prefix, strip it before appending EOI
        cleaned = data.rstrip(b"\xff")
        repaired = cleaned + b"\xff\xd9"
        return repaired, True

    @classmethod
    def resync_stream(
        cls,
        source: bytes | io.BufferedIOBase | Path | str,
        donor: bytes | io.BufferedIOBase | Path | str | None = None,
        restart_interval: int | None = None,
        pad_geometry: bool = False,
        min_cadence_run: int = 2,
    ) -> ResyncResult:
        """Resynchronizes corrupted JPEG entropy bitstream using restart markers.

        Args:
            source: Corrupted image bytes, path, or readable stream.
            donor: Donor header or donor JPEG source. If None, extracts from source.
            restart_interval: Known restart interval in MCUs. If None, reads from donor DRI.
            pad_geometry: If True, prepends dummy intervals for missing prefix intervals to preserve Y-geometry.
            min_cadence_run: Minimum consecutive markers needed to validate sequence.

        Returns:
            ResyncResult containing reconstructed JPEG data and recovery metrics.
        """
        source_bytes = cls._to_bytes(source)

        # Determine donor header
        if donor is not None:
            donor_bytes = cls._to_bytes(donor)
            parser = JpegParser(donor_bytes)
            donor_header = parser.get_header_bytes()
            if restart_interval is None:
                restart_interval = cls.extract_dri(donor_bytes)
        else:
            try:
                parser = JpegParser(source_bytes)
                donor_header = parser.get_header_bytes()
                if restart_interval is None:
                    restart_interval = cls.extract_dri(source_bytes)
            except Exception:
                raise ValueError("Source has no valid JPEG header; a donor header must be provided.")

        # Determine where entropy scan data starts in source
        scan_offset = 0
        try:
            src_parser = JpegParser(source_bytes)
            scan_offset = len(src_parser.get_header_bytes())
        except Exception:
            # Source header is damaged / TRIMed
            scan_offset = 0

        raw_scan = source_bytes[scan_offset:]

        # Scan for restart markers
        markers = cls.scan_restart_markers(raw_scan)
        cadence = cls.validate_cadence(markers, min_run=min_cadence_run)

        if not cadence.is_valid or not cadence.markers:
            # Fallback: no valid restart markers found
            repaired_scan, eoi_added = cls.repair_tail(raw_scan)
            full_data = donor_header + repaired_scan
            return ResyncResult(
                data=full_data,
                status="no_markers_found",
                restart_interval=restart_interval,
                first_marker=None,
                total_markers_found=len(markers),
                cut_bytes_count=0,
                padded_intervals_count=0,
                eoi_appended=eoi_added,
                details="No valid restart marker cadence found in entropy stream.",
            )

        first_marker = cadence.first_marker
        assert first_marker is not None

        # Cut corrupted porridge up to the first valid marker
        # We start the stream from the first valid restart marker (or its payload)
        cut_bytes = first_marker.offset
        surviving_scan = raw_scan[first_marker.offset :]

        # Pad dummy intervals if requested to preserve vertical frame positioning
        padded_count = 0
        prefix_stream = bytearray()

        if pad_geometry and first_marker.index > 0:
            padded_count = first_marker.index
            mcu_interval = restart_interval if restart_interval is not None else 1
            for idx in range(padded_count):
                prefix_stream.extend(cls.create_dummy_restart_interval(idx, restart_interval=mcu_interval))

        # Check and ensure DRI marker in donor header
        final_header = donor_header
        if restart_interval is not None and restart_interval > 0:
            final_header = cls.inject_dri_into_header(donor_header, restart_interval)

        # Assemble and repair tail
        combined_payload = bytes(prefix_stream) + surviving_scan
        final_payload, eoi_added = cls.repair_tail(combined_payload)
        repaired_jpeg = final_header + final_payload

        return ResyncResult(
            data=repaired_jpeg,
            status="resynced",
            restart_interval=restart_interval,
            first_marker=first_marker,
            total_markers_found=len(cadence.markers),
            cut_bytes_count=cut_bytes,
            padded_intervals_count=padded_count,
            eoi_appended=eoi_added,
            details=f"Synchronized on RST{first_marker.index} at offset {first_marker.offset}; "
            f"cut {cut_bytes} corrupted bytes; {len(cadence.markers)} markers validated.",
        )

    @classmethod
    def save_resynced(
        cls,
        result: ResyncResult,
        source_path: Path | str,
        output_dir: Path | str | None = None,
    ) -> Path:
        """Safely saves resynchronized JPEG image to _Resync folder."""
        src = Path(source_path)
        if output_dir is None:
            out_dir = src.parent / "_Resync"
        else:
            out_dir = Path(output_dir)

        out_dir.mkdir(parents=True, exist_ok=True)
        dest_filename = f"{src.stem}_resynced.jpg"
        dest_path = out_dir / dest_filename

        dest_path.write_bytes(result.data)
        return dest_path
