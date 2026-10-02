"""JPEG marker parser and donor header extractor.

Complies with ITU-T T.81 / ISO/IEC 10918-1 JPEG standards with strict marker-boundary
enforcement, protecting against false SOS markers inside EXIF APP1 payloads.
"""

from __future__ import annotations
import io
import struct
from pathlib import Path
from dataclasses import dataclass

MARKER_NAMES: dict[int, str] = {
    0xD8: "SOI",
    0xD9: "EOI",
    0xDA: "SOS",
    0xDB: "DQT",
    0xC4: "DHT",
    0xC0: "SOF0",
    0xC1: "SOF1",
    0xC2: "SOF2",
    0xC3: "SOF3",
    0xDD: "DRI",
    0xFE: "COM",
}
for i in range(16):
    MARKER_NAMES[0xE0 + i] = f"APP{i}"
for i in range(8):
    MARKER_NAMES[0xD0 + i] = f"RST{i}"


@dataclass(frozen=True)
class Marker:
    code: int
    name: str
    offset: int
    length: int | None = None


class JpegParser:
    """Parses JPEG stream markers and safely extracts donor headers."""

    def __init__(self, source: bytes | io.BufferedIOBase | Path | str):
        if isinstance(source, (str, Path)):
            self._data = Path(source).read_bytes()
        elif isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
            pos = source.tell() if source.seekable() else 0
            self._data = source.read()
            if source.seekable():
                source.seek(pos)
        elif isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")
        self._markers: list[Marker] | None = None

    @property
    def data(self) -> bytes:
        return self._data

    def parse_markers(self) -> list[Marker]:
        """Parses JPEG markers sequentially until SOS or EOI.

        Skips variable-length segment payloads (including EXIF APP1) using
        their declared length fields to prevent false marker detection.
        """
        if self._markers is not None:
            return self._markers

        data = self._data
        n = len(data)
        if n < 2 or data[0] != 0xFF or data[1] != 0xD8:
            raise ValueError("Invalid SOI / Not a valid JPEG: missing 0xFF 0xD8 header")

        markers: list[Marker] = [Marker(code=0xD8, name="SOI", offset=0, length=None)]
        i = 2

        while i < n:
            if data[i] != 0xFF:
                raise ValueError(f"Expected 0xFF marker prefix at offset {i}, got 0x{data[i]:02X}")

            # Skip any fill/padding 0xFF bytes
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                break

            marker_code = data[i]
            marker_offset = i - 1  # 0xFF was at i - 1
            i += 1

            if marker_code in (0x00, 0xFF):
                # Byte stuffing or continued fill
                continue

            name = MARKER_NAMES.get(marker_code, f"0x{marker_code:02X}")

            # Standalone markers without length field
            if marker_code in (0xD8, 0xD9) or (0xD0 <= marker_code <= 0xD7) or marker_code == 0x01:
                markers.append(Marker(code=marker_code, name=name, offset=marker_offset, length=None))
                if marker_code == 0xD9:
                    break
                continue

            # Variable-length marker segment: read 16-bit big-endian length
            if i + 2 > n:
                raise ValueError(f"Truncated marker 0x{marker_code:02X} length at offset {i}")

            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2:
                raise ValueError(f"Invalid segment length {seg_len} for marker 0x{marker_code:02X}")

            if i + seg_len > n:
                raise ValueError(
                    f"Segment length {seg_len} exceeds data buffer (available: {n - i}) for marker 0x{marker_code:02X}"
                )

            markers.append(Marker(code=marker_code, name=name, offset=marker_offset, length=seg_len))

            # Advance past segment payload
            i += seg_len

            # When SOS is encountered, header ends and compressed bitstream begins
            if marker_code == 0xDA:
                break

        self._markers = markers
        return markers

    def find_sos_offset(self) -> int:
        """Finds the byte offset of the true SOS (0xFF 0xDA) marker."""
        markers = self.parse_markers()
        for m in markers:
            if m.code == 0xDA:
                return m.offset
        raise ValueError("SOS marker not found in donor — not a valid JPEG?")

    def get_header_bytes(self) -> bytes:
        """Extracts complete header bytes from SOI through the end of the SOS segment."""
        markers = self.parse_markers()
        sos_marker: Marker | None = None
        for m in markers:
            if m.code == 0xDA:
                sos_marker = m
                break

        if sos_marker is None or sos_marker.length is None:
            raise ValueError("SOS marker not found in donor — not a valid JPEG?")

        header_end = sos_marker.offset + 2 + sos_marker.length
        return self._data[:header_end]
