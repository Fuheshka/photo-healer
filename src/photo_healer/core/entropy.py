"""Shannon entropy calculation and TRIM boundary detection.

Accurately detects transitions between TRIM-zeroed regions and live compressed JPEG bitstreams
across standard SSD sector sizes (512B, 4096B, 64KB) or arbitrary byte boundaries.
"""

from __future__ import annotations
import io
import math
from pathlib import Path


class EntropyAnalyzer:
    """Shannon entropy analyzer for forensic image recovery."""

    @staticmethod
    def shannon_entropy(data: bytes) -> float:
        """Calculates Shannon entropy in bits per byte (0.0 to 8.0)."""
        if not data:
            return 0.0
        n = len(data)
        counts = [0] * 256
        for b in data:
            counts[b] += 1

        entropy = 0.0
        for c in counts:
            if c:
                p = c / n
                entropy -= p * math.log2(p)
        return entropy

    @classmethod
    def detect_entropy_start(
        cls,
        source: bytes | io.BufferedIOBase | io.RawIOBase | Path | str,
        block_size: int = 512,
        entropy_threshold: float = 5.0,
        min_gradient: float = 3.0,
    ) -> int | None:
        """Detects the byte offset where high-entropy compressed data begins.

        Args:
            source: Raw bytes, file path, or readable stream.
            block_size: Sector/block evaluation unit (e.g. 512, 4096, 65536, or 1 for fine alignment).
            entropy_threshold: Minimum Shannon entropy (bits/byte) to consider as live bitstream.
            min_gradient: Reserved for gradient slope validation.

        Returns:
            Byte offset of the first live block/byte, or None if no high-entropy data found.
        """
        if isinstance(source, (str, Path)):
            stream = Path(source).open("rb")
            should_close = True
        elif isinstance(source, (bytes, bytearray)):
            stream = io.BytesIO(source)
            should_close = False
        elif isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
            stream = source
            if stream.seekable():
                stream.seek(0)
            should_close = False
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")

        try:
            eval_block = max(block_size, 512)
            prev_block = b""
            prev_ent = 0.0
            pos = 0

            while True:
                chunk = stream.read(eval_block)
                if not chunk:
                    break

                ent = cls.shannon_entropy(chunk)
                gradient = ent - prev_ent

                if ent >= entropy_threshold and (gradient >= min_gradient or prev_ent == 0.0):
                    if block_size == 1:
                        # Fine-grained detection: look for first dense non-zero window in (prev_block + chunk)
                        combined = prev_block + chunk
                        combined_start = pos - len(prev_block)
                        win_size = 16
                        if len(combined) >= win_size:
                            for idx in range(len(combined) - win_size + 1):
                                win = combined[idx : idx + win_size]
                                if sum(1 for b in win if b != 0) >= 12:
                                    first_nz = idx + next(i for i, b in enumerate(win) if b != 0)
                                    return combined_start + first_nz
                        return pos
                    else:
                        # Sector/block aligned
                        return pos

                prev_block = chunk
                prev_ent = ent
                pos += len(chunk)

            return None
        finally:
            if should_close:
                stream.close()
