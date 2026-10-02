"""JPEG header splicer and entropy stream transplant engine.

Combines extracted donor headers with damaged entropy streams while maintaining single-EOI
invariants and stream-level memory efficiency.
"""

from __future__ import annotations
import io
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

    def __init__(self, donor_header: bytes):
        if not donor_header.startswith(b"\xff\xd8"):
            raise ValueError("Donor header must begin with JPEG SOI marker (0xFF 0xD8)")
        self.donor_header = donor_header

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
