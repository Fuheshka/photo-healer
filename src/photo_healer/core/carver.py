"""Embedded thumbnail carver, MPF preview extractor and raw stream carver.

Safe extraction of EXIF thumbnails (APP1 IFD1), Full HD previews from Multi-Picture
Format containers (APP2 CIPA DC-007), and raw stream carving with automatic fallback
for photos with irreparable entropy degradation.
"""

from __future__ import annotations
import io
import struct
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

from photo_healer.core.entropy import EntropyAnalyzer
from photo_healer.core.splicer import HeaderSplicer
from photo_healer.core.validator import JpegValidator


@dataclass(frozen=True)
class CarvedPreview:
    """Represents an extracted embedded thumbnail or preview."""

    data: bytes
    preview_type: str  # "mpf", "exif_thumb", "raw_carved"
    width: int | None = None
    height: int | None = None
    size: int = 0
    source_offset: int = 0


@dataclass(frozen=True)
class RescueResult:
    """Result of donor transplantation or automatic preview fallback rescue."""

    status: str  # "transplanted", "recovered_mpf", "recovered_exif_thumb", "recovered_raw_carved", "failed"
    data: bytes | None = None
    preview_type: str | None = None
    saved_path: Path | None = None
    width: int | None = None
    height: int | None = None
    details: str = ""


class _HybridMethod:
    """Descriptor enabling methods to be called on either class or instance seamlessly."""

    def __init__(self, func: Callable):
        self.func = func

    def __get__(self, instance: Any, owner: type | None = None) -> Callable:
        def wrapper(*args, **kwargs):
            if instance is not None:
                # If called on instance with 0 positional args, fall back to self._source
                if not args and hasattr(instance, "_source") and instance._source is not None:
                    return self.func(instance, instance._source, **kwargs)
                return self.func(instance, *args, **kwargs)
            # Called directly on class: ThumbnailCarver.method(...)
            return self.func(owner, *args, **kwargs)

        return wrapper


def _normalize_source_bytes(source: Any) -> bytes:
    """Normalizes polymorphic input sources into bytes."""
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
        pos = source.tell() if source.seekable() else 0
        data = source.read()
        if source.seekable():
            source.seek(pos)
        return data
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise TypeError(f"Unsupported source type: {type(source)}")


class ThumbnailCarver:
    """Extracts embedded EXIF thumbnails, MPF previews, and raw stream JPEGs."""

    def __init__(self, source: bytes | io.BufferedIOBase | Path | str | None = None):
        self._source = source

    @_HybridMethod
    def extract_exif_thumbnail(
        self_or_cls,
        source: bytes | io.BufferedIOBase | Path | str,
    ) -> bytes | None:
        """Parses IFD1 in EXIF APP1 segment (tags 0x0201 & 0x0202) to extract thumbnail."""
        data = _normalize_source_bytes(source)
        n = len(data)

        # Locate all 'Exif\x00\x00' occurrences in data
        exif_sig = b"Exif\x00\x00"
        search_idx = 0

        while True:
            idx = data.find(exif_sig, search_idx)
            if idx == -1:
                break
            search_idx = idx + 1

            tiff_start = idx + len(exif_sig)
            if tiff_start + 8 > n:
                continue

            bom = data[tiff_start : tiff_start + 2]
            if bom == b"II":
                order = "<"
            elif bom == b"MM":
                order = ">"
            else:
                continue

            magic = struct.unpack_from(order + "H", data, tiff_start + 2)[0]
            if magic != 42:
                continue

            ifd0_offset = struct.unpack_from(order + "I", data, tiff_start + 4)[0]
            if tiff_start + ifd0_offset + 2 > n:
                continue

            # Read IFD0 entries
            num_ifd0 = struct.unpack_from(order + "H", data, tiff_start + ifd0_offset)[0]
            ifd0_entries_start = tiff_start + ifd0_offset + 2
            if ifd0_entries_start + num_ifd0 * 12 + 4 > n:
                continue

            # Check next IFD pointer (IFD1 offset)
            ifd1_offset_pos = ifd0_entries_start + num_ifd0 * 12
            ifd1_offset = struct.unpack_from(order + "I", data, ifd1_offset_pos)[0]

            thumb_offset = None
            thumb_len = None

            # First priority: check IFD1
            if ifd1_offset != 0 and tiff_start + ifd1_offset + 2 <= n:
                num_ifd1 = struct.unpack_from(order + "H", data, tiff_start + ifd1_offset)[0]
                ifd1_entries_start = tiff_start + ifd1_offset + 2
                for k in range(num_ifd1):
                    entry_pos = ifd1_entries_start + k * 12
                    if entry_pos + 12 > n:
                        break
                    tag, _type, _count, val = struct.unpack_from(order + "HHII", data, entry_pos)
                    if tag == 0x0201:  # JPEGInterchangeFormat
                        thumb_offset = val
                    elif tag == 0x0202:  # JPEGInterchangeFormatLength
                        thumb_len = val

            # Fallback priority: check IFD0 directly
            if thumb_offset is None or thumb_len is None:
                for k in range(num_ifd0):
                    entry_pos = ifd0_entries_start + k * 12
                    if entry_pos + 12 > n:
                        break
                    tag, _type, _count, val = struct.unpack_from(order + "HHII", data, entry_pos)
                    if tag == 0x0201:
                        thumb_offset = val
                    elif tag == 0x0202:
                        thumb_len = val

            if thumb_offset is not None and thumb_len is not None and thumb_len > 4:
                # Check candidate locations: relative to TIFF header, file start, or APP1 payload
                candidates = [
                    data[tiff_start + thumb_offset : tiff_start + thumb_offset + thumb_len],
                    data[thumb_offset : thumb_offset + thumb_len],
                    data[idx + thumb_offset : idx + thumb_offset + thumb_len],
                ]
                for cand in candidates:
                    if cand.startswith(b"\xff\xd8"):
                        # Ensure terminal EOI
                        if not cand.endswith(b"\xff\xd9"):
                            cand = cand + b"\xff\xd9"
                        return cand

        return None

    @_HybridMethod
    def extract_mpf_preview(
        self_or_cls,
        source: bytes | io.BufferedIOBase | Path | str,
    ) -> bytes | None:
        """Searches APP2 Multi-Picture Format (MPF CIPA DC-007) containers for previews."""
        data = _normalize_source_bytes(source)
        n = len(data)

        mpf_sig = b"MPF\x00"
        search_idx = 0
        extracted_previews: list[bytes] = []

        while True:
            idx = data.find(mpf_sig, search_idx)
            if idx == -1:
                break
            search_idx = idx + 1

            mp_header_start = idx + len(mpf_sig)
            if mp_header_start + 8 > n:
                continue

            bom = data[mp_header_start : mp_header_start + 2]
            if bom == b"II":
                order = "<"
            elif bom == b"MM":
                order = ">"
            else:
                continue

            magic = struct.unpack_from(order + "H", data, mp_header_start + 2)[0]
            if magic != 42:
                continue

            index_ifd_offset = struct.unpack_from(order + "I", data, mp_header_start + 4)[0]
            if mp_header_start + index_ifd_offset + 2 > n:
                continue

            num_tags = struct.unpack_from(order + "H", data, mp_header_start + index_ifd_offset)[0]
            tags_start = mp_header_start + index_ifd_offset + 2

            num_images = None
            entry_list_offset = None

            for k in range(num_tags):
                tag_pos = tags_start + k * 12
                if tag_pos + 12 > n:
                    break
                tag, _type, _count, val = struct.unpack_from(order + "HHII", data, tag_pos)
                if tag == 0xB001:  # NumberOfImages
                    num_images = val
                elif tag == 0xB002:  # MPImageList
                    entry_list_offset = val

            if num_images is None or entry_list_offset is None or num_images < 2:
                continue

            # Iterate through secondary images (index 1 to num_images - 1)
            for img_idx in range(1, num_images):
                entry_pos = mp_header_start + entry_list_offset + (img_idx * 16)
                if entry_pos + 16 > n:
                    break

                _flags, img_size, img_offset, _dep1, _dep2 = struct.unpack_from(
                    order + "IIIHH", data, entry_pos
                )
                if img_size < 4 or img_offset == 0:
                    continue

                candidates = [
                    data[mp_header_start + img_offset : mp_header_start + img_offset + img_size],
                    data[img_offset : img_offset + img_size],
                ]
                for cand in candidates:
                    if cand.startswith(b"\xff\xd8"):
                        if not cand.endswith(b"\xff\xd9"):
                            cand = cand + b"\xff\xd9"
                        extracted_previews.append(cand)
                        break

        if not extracted_previews:
            return None

        # If multiple previews exist, select the one with highest resolution / size
        def preview_rank(p_bytes: bytes) -> tuple[int, int]:
            val = JpegValidator.validate(p_bytes)
            resolution = (val.width or 0) * (val.height or 0)
            return resolution, len(p_bytes)

        extracted_previews.sort(key=preview_rank, reverse=True)
        return extracted_previews[0]

    @_HybridMethod
    def raw_stream_carve(
        self_or_cls,
        source: bytes | io.BufferedIOBase | Path | str,
    ) -> list[bytes]:
        """Carves independent FF D8 FF ... FF D9 streams from raw byte sequences."""
        data = _normalize_source_bytes(source)
        n = len(data)
        carved_images: list[bytes] = []
        seen_hashes: set[int] = set()

        search_idx = 0
        while search_idx < n - 3:
            # Locate JPEG SOI marker with next marker prefix: FF D8 FF
            soi_idx = data.find(b"\xff\xd8\xff", search_idx)
            if soi_idx == -1:
                break

            # Walk through markers starting from soi_idx
            i = soi_idx + 2
            has_sof = False
            has_sos = False
            eoi_found = False
            image_end = None

            while i < n:
                if data[i] != 0xFF:
                    i += 1
                    continue

                while i < n and data[i] == 0xFF:
                    i += 1
                if i >= n:
                    break

                code = data[i]
                i += 1

                if code in (0x00, 0xFF):
                    continue
                if 0xD0 <= code <= 0xD7 or code == 0x01:
                    continue
                if code == 0xD8:
                    continue
                if code == 0xD9:
                    if has_sos or has_sof:
                        eoi_found = True
                        image_end = i
                    break

                # Variable-length marker segment
                if i + 2 > n:
                    break
                seg_len = struct.unpack(">H", data[i : i + 2])[0]
                if seg_len < 2 or i + seg_len > n:
                    break

                if code in (0xC0, 0xC1, 0xC2):
                    has_sof = True
                elif code == 0xDA:  # SOS
                    has_sos = True
                    i += seg_len
                    # Inside compressed entropy stream: scan for un-escaped markers
                    while i < n - 1:
                        if data[i] == 0xFF:
                            nxt = data[i + 1]
                            if nxt == 0x00:
                                i += 2
                                continue
                            elif 0xD0 <= nxt <= 0xD7:
                                i += 2
                                continue
                            elif nxt == 0xD9:  # EOI
                                eoi_found = True
                                image_end = i + 2
                                break
                            elif nxt != 0xFF:
                                i += 2
                                continue
                        i += 1
                    break

                i += seg_len

            if eoi_found and image_end is not None:
                candidate = data[soi_idx:image_end]
                # Filter out microscopic fragments and validate
                if len(candidate) >= 64:
                    h = hash(candidate)
                    if h not in seen_hashes:
                        val = JpegValidator.validate(candidate)
                        if val.is_valid:
                            carved_images.append(candidate)
                            seen_hashes.add(h)
                search_idx = soi_idx + 3
            else:
                search_idx = soi_idx + 1

        return carved_images

    @_HybridMethod
    def extract_best_preview(
        self_or_cls,
        source: bytes | io.BufferedIOBase | Path | str,
    ) -> CarvedPreview | None:
        """Finds and extracts the highest-quality preview available.

        Priority order:
        1. MPF Preview (Full HD high-resolution preview container in APP2)
        2. EXIF Thumbnail (embedded 160x120 thumbnail in APP1 IFD1)
        3. Raw stream carved JPEGs (when headers are fragmented/destroyed)
        """
        # 1. MPF Preview (highest resolution Full HD)
        try:
            mpf_data = ThumbnailCarver.extract_mpf_preview(source)
            if mpf_data:
                val = JpegValidator.validate(mpf_data)
                return CarvedPreview(
                    data=mpf_data,
                    preview_type="mpf",
                    width=val.width,
                    height=val.height,
                    size=len(mpf_data),
                )
        except Exception:
            pass

        # 2. EXIF Thumbnail (standard camera/smartphone thumbnail)
        try:
            exif_data = ThumbnailCarver.extract_exif_thumbnail(source)
            if exif_data:
                val = JpegValidator.validate(exif_data)
                return CarvedPreview(
                    data=exif_data,
                    preview_type="exif_thumb",
                    width=val.width,
                    height=val.height,
                    size=len(exif_data),
                )
        except Exception:
            pass

        # 3. Raw stream carved JPEGs (fallback when headers are corrupted)
        try:
            raw_bytes = _normalize_source_bytes(source)
            raw_carved = ThumbnailCarver.raw_stream_carve(source)
            candidates: list[CarvedPreview] = []
            for c_bytes in raw_carved:
                # Exclude the entire original file if it was healthy
                if len(c_bytes) < len(raw_bytes) or not raw_bytes.startswith(b"\xff\xd8"):
                    val = JpegValidator.validate(c_bytes)
                    if val.is_valid:
                        candidates.append(
                            CarvedPreview(
                                data=c_bytes,
                                preview_type="raw_carved",
                                width=val.width,
                                height=val.height,
                                size=len(c_bytes),
                            )
                        )
            if candidates:
                candidates.sort(
                    key=lambda cp: ((cp.width or 0) * (cp.height or 0), cp.size),
                    reverse=True,
                )
                return candidates[0]
        except Exception:
            pass

        return None


    @_HybridMethod
    def save_preview(
        self_or_cls,
        source_path: Path | str,
        preview_data: bytes,
        preview_type: str = "preview",
        output_dir: Path | str | None = None,
    ) -> Path:
        """Safely saves carved preview into _Previews/ without modifying the original."""
        src = Path(source_path)
        parent_dir = src.parent

        if output_dir is not None:
            dest_dir = Path(output_dir)
        else:
            dest_dir = parent_dir / "_Previews"

        dest_dir.mkdir(parents=True, exist_ok=True)

        if preview_type in ("thumb", "exif_thumb"):
            filename = f"{src.stem}_thumb.jpg"
        else:
            filename = f"{src.stem}_preview.jpg"

        dest_file = dest_dir / filename
        dest_file.write_bytes(preview_data)
        return dest_file

    @_HybridMethod
    def fallback_rescue(
        self_or_cls,
        source: bytes | io.BufferedIOBase | Path | str,
        donor_header: bytes | None = None,
        save: bool = False,
        output_dir: Path | str | None = None,
    ) -> RescueResult:
        """Automatic rescue: transplants donor header or falls back to best preview."""
        # 1. Attempt donor header transplantation if donor provided
        if donor_header is not None:
            try:
                raw_bytes = _normalize_source_bytes(source)
                detected_offset = EntropyAnalyzer.detect_entropy_start(raw_bytes)

                if detected_offset is not None:
                    # Check entropy of remaining slice to avoid corrupt transplantation
                    live_slice = raw_bytes[detected_offset:]
                    ent = EntropyAnalyzer.shannon_entropy(live_slice[:8192])
                    if ent >= 5.0:
                        splicer = HeaderSplicer(donor_header)
                        spliced = splicer.splice_target(raw_bytes, entropy_offset=detected_offset)
                        val = JpegValidator.validate(spliced.data)
                        if val.is_valid and val.has_sof and val.has_sos:
                            return RescueResult(
                                status="transplanted",
                                data=spliced.data,
                                preview_type="transplanted_full",
                                width=val.width,
                                height=val.height,
                                details="Successfully spliced donor header with live entropy bitstream",
                            )
            except Exception:
                # Fallback to preview extraction
                pass

        # 2. Fallback: extract best available preview
        best_preview = ThumbnailCarver.extract_best_preview(source)
        if best_preview is not None:
            saved_path = None
            if save and isinstance(source, (str, Path)):
                saved_path = ThumbnailCarver.save_preview(
                    source_path=source,
                    preview_data=best_preview.data,
                    preview_type=best_preview.preview_type,
                    output_dir=output_dir,
                )

            return RescueResult(
                status=f"recovered_{best_preview.preview_type}",
                data=best_preview.data,
                preview_type=best_preview.preview_type,
                saved_path=saved_path,
                width=best_preview.width,
                height=best_preview.height,
                details=f"Extracted {best_preview.preview_type} preview ({best_preview.width}x{best_preview.height} px)",
            )

        return RescueResult(
            status="failed",
            details="Neither live entropy bitstream nor embedded previews could be recovered",
        )
