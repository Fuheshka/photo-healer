"""JPEG structure and decoding validator.

Validates marker integrity, required quantization/frame/scan headers, image geometry,
and terminal markers according to ITU-T T.81 standards.
"""

from __future__ import annotations
import io
import struct
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    is_valid: bool
    width: int | None = None
    height: int | None = None
    components: int | None = None
    has_dqt: bool = False
    has_dht: bool = False
    has_sof: bool = False
    has_sos: bool = False
    has_eoi: bool = False
    decoder_verified: bool = False
    errors: list[str] = field(default_factory=list)


class JpegValidator:
    """Validates JPEG marker structures and image integrity."""

    @classmethod
    def validate(cls, source: bytes | io.BufferedIOBase | Path | str) -> ValidationResult:
        if isinstance(source, (str, Path)):
            data = Path(source).read_bytes()
        elif isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
            data = source.read()
        elif isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")

        errors: list[str] = []
        n = len(data)

        if n < 4 or data[:2] != b"\xff\xd8":
            return ValidationResult(
                is_valid=False,
                errors=["Missing or invalid SOI (Start of Image) marker: expected 0xFF 0xD8"],
            )

        has_dqt = False
        has_dht = False
        has_sof = False
        has_sos = False
        width: int | None = None
        height: int | None = None
        components: int | None = None

        i = 2
        while i < n:
            if data[i] != 0xFF:
                errors.append(f"Expected 0xFF marker prefix at offset {i}, got 0x{data[i]:02X}")
                break

            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                break

            code = data[i]
            i += 1

            if code in (0x00, 0xFF):
                continue

            if code == 0xD8:
                continue

            if code == 0xD9:
                break

            if 0xD0 <= code <= 0xD7 or code == 0x01:
                continue

            if i + 2 > n:
                errors.append(f"Truncated marker 0x{code:02X} length field")
                break

            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2 or i + seg_len > n:
                errors.append(f"Invalid or out-of-bounds segment length {seg_len} for marker 0x{code:02X}")
                break

            payload = data[i + 2 : i + seg_len]

            if code == 0xDB:
                has_dqt = True
            elif code == 0xC4:
                has_dht = True
            elif code in (0xC0, 0xC1, 0xC2):  # SOF0, SOF1, SOF2
                has_sof = True
                if len(payload) >= 6:
                    _precision, h, w, comp = struct.unpack(">BHHB", payload[:6])
                    height = h
                    width = w
                    components = comp
            elif code == 0xDA:
                has_sos = True
                i += seg_len
                break

            i += seg_len

        # Check required components
        if not has_dqt:
            errors.append("Missing required DQT (Quantization Table) segment")
        if not has_sof:
            errors.append("Missing required SOF (Start of Frame) segment")
        if not has_sos:
            errors.append("Missing required SOS (Start of Scan) segment")

        # Validate geometry
        if has_sof and (width is None or height is None or width <= 0 or height <= 0):
            errors.append(f"Invalid image dimensions: {width}x{height}")

        # Check terminal EOI (allowing trailing null padding from sector-carved images)
        has_eoi = data.rstrip(b"\x00").endswith(b"\xff\xd9")
        if not has_eoi:
            errors.append("Missing terminal EOI (0xFF 0xD9) marker at end of file")

        decoder_verified = False
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            decoder_verified = True
        except ImportError:
            # PIL not installed in environment, structural validation passes
            pass
        except Exception as e:
            errors.append(f"Decoder verification failed: {e}")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            width=width,
            height=height,
            components=components,
            has_dqt=has_dqt,
            has_dht=has_dht,
            has_sof=has_sof,
            has_sos=has_sos,
            has_eoi=has_eoi,
            decoder_verified=decoder_verified,
            errors=errors,
        )
