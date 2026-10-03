"""JPEG header splicer and entropy stream transplant engine.

Combines extracted donor headers with damaged entropy streams while maintaining single-EOI
invariants and stream-level memory efficiency.
"""

from __future__ import annotations
import io
import struct
from pathlib import Path
from dataclasses import dataclass
from photo_healer.core.entropy import EntropyAnalyzer


@dataclass(frozen=True)
class SpliceResult:
    data: bytes
    donor_header_len: int
    entropy_offset: int
    eoi_appended: bool
    total_bytes: int


class HeaderSplicer:
    """Splices donor JPEG headers with recovered entropy bitstreams."""

    def __init__(self, donor_header: bytes, strip_thumbnail: bool = True):
        if not donor_header.startswith(b"\xff\xd8"):
            raise ValueError("Donor header must begin with JPEG SOI marker (0xFF 0xD8)")
        self.raw_donor_header = donor_header
        self.strip_thumbnail = strip_thumbnail
        if strip_thumbnail:
            self.donor_header = self.strip_donor_thumbnails(donor_header)
        else:
            self.donor_header = donor_header

    @classmethod
    def strip_donor_thumbnails(cls, donor_header: bytes) -> bytes:
        """Strips inherited donor thumbnails (IFD1 in EXIF APP1, MPF APP2)."""
        if not donor_header.startswith(b"\xff\xd8"):
            return donor_header

        n = len(donor_header)
        res = bytearray(b"\xff\xd8")
        i = 2

        while i < n:
            if donor_header[i] != 0xFF:
                res.append(donor_header[i])
                i += 1
                continue

            # Skip padding 0xFF bytes
            while i < n and donor_header[i] == 0xFF:
                i += 1
            if i >= n:
                res.append(0xFF)
                break

            marker_code = donor_header[i]
            i += 1

            if marker_code in (0x00, 0xFF):
                res.extend(b"\xff" + bytes([marker_code]))
                continue

            # Standalone markers
            if marker_code in (0xD8, 0xD9) or (0xD0 <= marker_code <= 0xD7) or marker_code == 0x01:
                res.extend(b"\xff" + bytes([marker_code]))
                if marker_code == 0xD9:
                    break
                continue

            # Variable-length markers
            if i + 2 > n:
                res.extend(b"\xff" + bytes([marker_code]) + donor_header[i:])
                break

            seg_len = struct.unpack(">H", donor_header[i : i + 2])[0]
            if seg_len < 2 or i + seg_len > n:
                res.extend(b"\xff" + bytes([marker_code]) + donor_header[i:])
                break

            payload = donor_header[i + 2 : i + seg_len]
            i += seg_len

            # 1. APP2 (0xFF 0xE2) - Check for MPF container
            if marker_code == 0xE2 and payload.startswith(b"MPF\x00"):
                # Omit donor MPF container segment completely
                continue

            # 2. APP1 (0xFF 0xE1) - Check for EXIF container
            if marker_code == 0xE1 and payload.startswith(b"Exif\x00\x00"):
                cleaned_payload = cls._clean_exif_payload(payload)
                new_len = len(cleaned_payload) + 2
                res.extend(b"\xff\xe1" + struct.pack(">H", new_len) + cleaned_payload)
                continue

            # 3. Normal marker segment
            res.extend(b"\xff" + bytes([marker_code]) + struct.pack(">H", seg_len) + payload)

            if marker_code == 0xDA:
                if i < n:
                    res.extend(donor_header[i:])
                break

        return bytes(res)

    @staticmethod
    def _clean_exif_payload(payload: bytes) -> bytes:
        """Cleans thumbnail references and data from EXIF APP1 payload."""
        tiff_start = 6
        if len(payload) < tiff_start + 8:
            return payload

        bom = payload[tiff_start : tiff_start + 2]
        if bom == b"II":
            order = "<"
        elif bom == b"MM":
            order = ">"
        else:
            return payload

        magic = struct.unpack_from(order + "H", payload, tiff_start + 2)[0]
        if magic != 42:
            return payload

        ifd0_offset = struct.unpack_from(order + "I", payload, tiff_start + 4)[0]
        if tiff_start + ifd0_offset + 2 > len(payload):
            return payload

        num_ifd0 = struct.unpack_from(order + "H", payload, tiff_start + ifd0_offset)[0]
        ifd0_entries_start = tiff_start + ifd0_offset + 2
        ifd0_entries_end = ifd0_entries_start + num_ifd0 * 12
        if ifd0_entries_end + 4 > len(payload):
            return payload

        payload_ba = bytearray(payload)
        ifd1_offset_pos = ifd0_entries_end
        ifd1_offset = struct.unpack_from(order + "I", payload_ba, ifd1_offset_pos)[0]

        thumb_offset: int | None = None
        thumb_len: int | None = None

        # A. Clean IFD1 if present
        if ifd1_offset != 0 and tiff_start + ifd1_offset + 2 <= len(payload_ba):
            ifd1_start = tiff_start + ifd1_offset
            num_ifd1 = struct.unpack_from(order + "H", payload_ba, ifd1_start)[0]
            ifd1_entries_start = ifd1_start + 2
            for k in range(num_ifd1):
                entry_pos = ifd1_entries_start + k * 12
                if entry_pos + 12 > len(payload_ba):
                    break
                tag, _type, _count, val = struct.unpack_from(order + "HHII", payload_ba, entry_pos)
                if tag == 0x0201:  # JPEGInterchangeFormat
                    thumb_offset = val
                    struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)
                elif tag == 0x0202:  # JPEGInterchangeFormatLength
                    thumb_len = val
                    struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)
                elif tag in (0x0111, 0x0117):  # StripOffsets, StripByteCounts
                    struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)

            # Unlink IFD1 from IFD0 by zeroing the next-IFD pointer in IFD0
            struct.pack_into(order + "I", payload_ba, ifd1_offset_pos, 0)

        # B. Clean IFD0 in case thumbnail tags are placed directly in IFD0
        for k in range(num_ifd0):
            entry_pos = ifd0_entries_start + k * 12
            tag, _type, _count, val = struct.unpack_from(order + "HHII", payload_ba, entry_pos)
            if tag == 0x0201:
                if thumb_offset is None:
                    thumb_offset = val
                struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)
            elif tag == 0x0202:
                if thumb_len is None:
                    thumb_len = val
                struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)
            elif tag in (0x0111, 0x0117):
                struct.pack_into(order + "I", payload_ba, entry_pos + 8, 0)

        # C. Zero out the thumbnail payload bytes to eliminate phantom JPEGs
        if thumb_offset is not None and thumb_len is not None and thumb_len > 0:
            candidates = [
                tiff_start + thumb_offset,
                thumb_offset,
            ]
            target_pos = None
            for c_pos in candidates:
                if 0 <= c_pos < len(payload_ba) and payload_ba[c_pos : c_pos + 2] == b"\xff\xd8":
                    target_pos = c_pos
                    break

            if target_pos is None and 0 <= tiff_start + thumb_offset < len(payload_ba):
                target_pos = tiff_start + thumb_offset

            if target_pos is not None:
                wipe_end = min(target_pos + thumb_len, len(payload_ba))
                if wipe_end > target_pos:
                    payload_ba[target_pos:wipe_end] = b"\x00" * (wipe_end - target_pos)

        return bytes(payload_ba)

    def splice_bytes(self, live_data: bytes, entropy_offset: int = 0) -> SpliceResult:
        """Splices donor header directly with live entropy bytes."""
        if live_data.endswith(b"\xff\xd9"):
            payload = live_data
            eoi_appended = False
        else:
            payload = live_data + b"\xff\xd9"
            eoi_appended = True

        result_data = self.donor_header + payload
        return SpliceResult(
            data=result_data,
            donor_header_len=len(self.donor_header),
            entropy_offset=entropy_offset,
            eoi_appended=eoi_appended,
            total_bytes=len(result_data),
        )

    def splice_target(
        self,
        target: bytes | io.BufferedIOBase | Path | str,
        entropy_offset: int | None = None,
    ) -> SpliceResult:
        """Transplants donor header onto target file or buffer.

        If entropy_offset is None, automatically detects offset via EntropyAnalyzer.
        """
        if isinstance(target, (str, Path)):
            target_bytes = Path(target).read_bytes()
        elif isinstance(target, (io.BufferedIOBase, io.RawIOBase)):
            target_bytes = target.read()
        elif isinstance(target, (bytes, bytearray)):
            target_bytes = bytes(target)
        else:
            raise TypeError(f"Unsupported target type: {type(target)}")

        if entropy_offset is None:
            detected = EntropyAnalyzer.detect_entropy_start(target_bytes)
            if detected is None:
                raise ValueError("No live entropy stream found in target")
            offset = detected
        else:
            offset = entropy_offset

        live_data = target_bytes[offset:]
        return self.splice_bytes(live_data, entropy_offset=offset)

    def splice_stream(
        self,
        target_stream: io.BufferedIOBase | io.RawIOBase,
        out_stream: io.BufferedIOBase | io.RawIOBase,
        entropy_offset: int | None = None,
        chunk_size: int = 65536,
    ) -> SpliceResult:
        """Streams donor header and target entropy to out_stream in fixed chunks."""
        if entropy_offset is None:
            if not target_stream.seekable():
                raise ValueError("target_stream must be seekable for automatic entropy detection")
            detected = EntropyAnalyzer.detect_entropy_start(target_stream)
            if detected is None:
                raise ValueError("No live entropy stream found in target")
            offset = detected
        else:
            offset = entropy_offset

        if target_stream.seekable():
            target_stream.seek(offset)

        # 1. Write donor header
        out_stream.write(self.donor_header)
        bytes_written = len(self.donor_header)

        # 2. Stream live data
        last_two = b""
        while True:
            chunk = target_stream.read(chunk_size)
            if not chunk:
                break
            out_stream.write(chunk)
            bytes_written += len(chunk)
            if len(chunk) >= 2:
                last_two = chunk[-2:]
            else:
                last_two = (last_two + chunk)[-2:]

        # 3. Guard against double EOI
        if last_two != b"\xff\xd9":
            out_stream.write(b"\xff\xd9")
            bytes_written += 2
            eoi_appended = True
        else:
            eoi_appended = False

        return SpliceResult(
            data=b"",
            donor_header_len=len(self.donor_header),
            entropy_offset=offset,
            eoi_appended=eoi_appended,
            total_bytes=bytes_written,
        )
