"""High-speed JPEG thumbnail stripper, EXIF IFD1 rebuild engine and Windows cache reset.

Provides streaming strip of legacy donor thumbnails without full image decoding,
lossless IFD1 thumbnail regeneration via Pillow without re-encoding the main photo,
and safe Windows Shell icon/thumbnail cache invalidation.
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class FileProcessResult:
    """Outcome of processing a single image file."""

    path: Path
    status: str  # "stripped", "rebuilt", "skipped", "error"
    bytes_freed: int = 0
    orig_size: int = 0
    new_size: int = 0
    error: str | None = None
    backup_path: Path | None = None
    details: str = ""


# ── EXIF & Marker Stripping ───────────────────────────────────────────────────

def _clean_exif_payload(payload: bytes) -> bytes:
    """Removes IFD1 thumbnail pointers and safely truncates/zeroes thumbnail data.

    Preserves IFD0 tags (Camera Make, Model, DateTime, Orientation, GPS) while
    completely stripping embedded thumbnail streams and pointers.
    """
    tiff_start = 6  # after 'Exif\\x00\\x00'
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

    # 1. Clean IFD1 if present
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

        # Unlink IFD1 by zeroing the next-IFD pointer in IFD0
        struct.pack_into(order + "I", payload_ba, ifd1_offset_pos, 0)

    # 2. Clean IFD0 in case thumbnail tags were written directly in IFD0
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

    # 3. Truncation or zeroing
    # Check if IFD1 was located after all IFD0 data values
    if ifd1_offset != 0 and ifd1_offset >= ifd0_offset:
        all_before = True
        type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}
        for k in range(num_ifd0):
            entry_pos = ifd0_entries_start + k * 12
            tag, typ, count, val = struct.unpack_from(order + "HHII", payload_ba, entry_pos)
            total_sz = count * type_sizes.get(typ, 1)
            if total_sz > 4 and val >= ifd1_offset:
                all_before = False
                break
        if all_before:
            return bytes(payload_ba[: tiff_start + ifd1_offset])

    # If IFD1 was not at the end or absent, check if thumb_offset is at the end
    if thumb_offset is not None and thumb_len is not None and thumb_len > 0:
        target_pos = tiff_start + thumb_offset
        if 0 <= target_pos < len(payload_ba):
            if target_pos + thumb_len >= len(payload_ba) - 8:
                return bytes(payload_ba[:target_pos])
            else:
                wipe_end = min(target_pos + thumb_len, len(payload_ba))
                payload_ba[target_pos:wipe_end] = b"\x00" * (wipe_end - target_pos)

    return bytes(payload_ba)


def strip_jpeg_thumbnails(data: bytes) -> tuple[bytes, int]:
    """Strips donor EXIF thumbnails and MPF preview containers from JPEG data.

    Pure standard library, streaming marker parser without decoding image pixels.
    Returns (cleaned_bytes, bytes_freed).
    """
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG data: missing SOI marker (0xFF 0xD8)")

    n = len(data)
    res = bytearray(b"\xff\xd8")
    i = 2
    bytes_freed = 0

    while i < n:
        if data[i] != 0xFF:
            res.append(data[i])
            i += 1
            continue

        # Skip padding 0xFF bytes
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            res.append(0xFF)
            break

        marker_code = data[i]
        i += 1

        if marker_code in (0x00, 0xFF):
            res.extend(b"\xff" + bytes([marker_code]))
            continue

        # Standalone markers (SOI, EOI, RST, TEM)
        if marker_code in (0xD8, 0xD9) or (0xD0 <= marker_code <= 0xD7) or marker_code == 0x01:
            res.extend(b"\xff" + bytes([marker_code]))
            if marker_code == 0xD9:
                break
            continue

        # Variable-length markers
        if i + 2 > n:
            res.extend(b"\xff" + bytes([marker_code]) + data[i:])
            break

        seg_len = struct.unpack(">H", data[i : i + 2])[0]
        if seg_len < 2 or i + seg_len > n:
            res.extend(b"\xff" + bytes([marker_code]) + data[i:])
            break

        payload = data[i + 2 : i + seg_len]
        i += seg_len

        # 1. APP2 (0xFF 0xE2) - Check for MPF preview container
        if marker_code == 0xE2 and payload.startswith(b"MPF\x00"):
            # Omit entire MPF preview container
            bytes_freed += seg_len + 2
            continue

        # 2. APP1 (0xFF 0xE1) - Check for EXIF container
        if marker_code == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            cleaned_payload = _clean_exif_payload(payload)
            if len(cleaned_payload) < len(payload):
                bytes_freed += len(payload) - len(cleaned_payload)
            new_len = len(cleaned_payload) + 2
            res.extend(b"\xff\xe1" + struct.pack(">H", new_len) + cleaned_payload)
            continue

        # 3. Normal marker segment
        res.extend(b"\xff" + bytes([marker_code]) + struct.pack(">H", seg_len) + payload)

        # SOS (0xFF 0xDA) marks start of entropy bitstream
        if marker_code == 0xDA:
            if i < n:
                res.extend(data[i:])
            break

    result_bytes = bytes(res)
    actual_freed = max(0, len(data) - len(result_bytes))
    return result_bytes, actual_freed


# ── EXIF IFD1 Thumbnail Rebuilding ───────────────────────────────────────────

def _build_minimal_exif(thumb_bytes: bytes) -> bytes:
    """Builds a minimal valid EXIF APP1 payload containing IFD0 and IFD1 with thumbnail."""
    order = "<"
    tiff = bytearray()
    tiff.extend(b"II\x2a\x00")
    tiff.extend(struct.pack(order + "I", 8))  # IFD0 at offset 8

    # IFD0: 1 entry (Orientation = 1 Normal)
    tiff.extend(struct.pack(order + "H", 1))
    tiff.extend(struct.pack(order + "HHII", 0x0112, 3, 1, 1))
    ifd1_offset = 8 + 2 + 12 + 4  # 26
    tiff.extend(struct.pack(order + "I", ifd1_offset))

    # IFD1 at offset 26: 3 entries
    # 0x0103: Compression (6 = JPEG)
    # 0x0201: JPEGInterchangeFormat (thumb_offset)
    # 0x0202: JPEGInterchangeFormatLength (len)
    thumb_offset = ifd1_offset + 2 + 3 * 12 + 4  # 68
    tiff.extend(struct.pack(order + "H", 3))
    tiff.extend(struct.pack(order + "HHII", 0x0103, 3, 1, 6))
    tiff.extend(struct.pack(order + "HHII", 0x0201, 4, 1, thumb_offset))
    tiff.extend(struct.pack(order + "HHII", 0x0202, 4, 1, len(thumb_bytes)))
    tiff.extend(struct.pack(order + "I", 0))

    tiff.extend(thumb_bytes)
    return b"Exif\x00\x00" + bytes(tiff)


def _inject_ifd1_to_exif(payload: bytes, thumb_bytes: bytes) -> bytes:
    """Injects IFD1 and thumbnail data into existing EXIF payload."""
    tiff_start = 6
    if len(payload) < tiff_start + 8:
        return _build_minimal_exif(thumb_bytes)

    bom = payload[tiff_start : tiff_start + 2]
    order = "<" if bom == b"II" else ">"
    tiff = bytearray(payload[tiff_start:])

    ifd0_offset = struct.unpack_from(order + "I", tiff, 4)[0]
    if ifd0_offset + 2 > len(tiff):
        return _build_minimal_exif(thumb_bytes)

    num_ifd0 = struct.unpack_from(order + "H", tiff, ifd0_offset)[0]
    ifd1_offset_pos = ifd0_offset + 2 + num_ifd0 * 12
    if ifd1_offset_pos + 4 > len(tiff):
        return _build_minimal_exif(thumb_bytes)

    # Word-align append position for IFD1
    ifd1_offset = len(tiff)
    if ifd1_offset % 2 != 0:
        tiff.append(0)
        ifd1_offset = len(tiff)

    # Point IFD0 next_ifd to our new IFD1
    struct.pack_into(order + "I", tiff, ifd1_offset_pos, ifd1_offset)

    thumb_offset = ifd1_offset + 2 + 3 * 12 + 4
    if thumb_offset % 2 != 0:
        thumb_offset += 1

    tiff.extend(struct.pack(order + "H", 3))
    tiff.extend(struct.pack(order + "HHII", 0x0103, 3, 1, 6))
    tiff.extend(struct.pack(order + "HHII", 0x0201, 4, 1, thumb_offset))
    tiff.extend(struct.pack(order + "HHII", 0x0202, 4, 1, len(thumb_bytes)))
    tiff.extend(struct.pack(order + "I", 0))

    while len(tiff) < thumb_offset:
        tiff.append(0)

    tiff.extend(thumb_bytes)
    return b"Exif\x00\x00" + bytes(tiff)


def rebuild_jpeg_thumbnail(
    data: bytes,
    size: tuple[int, int] = (160, 120),
    quality: int = 75,
) -> bytes:
    """Reads actual JPEG frame with Pillow, generates downscaled thumbnail, and inserts into IFD1.

    Zero re-encoding or compression loss of the main photo entropy body.
    """
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG data: missing SOI marker")

    try:
        from PIL import Image, ImageFile
        # Enable decoding of partially truncated / damaged forensic photo frames
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except ImportError:
        raise ImportError(
            "Pillow is required for thumbnail rebuilding. Install it with: pip install pillow"
        )

    # 1. Decode frame and produce thumbnail JPEG in memory
    with Image.open(io.BytesIO(data)) as img:
        thumb = img.copy()
        thumb.thumbnail(size, Image.Resampling.LANCZOS)
        tb = io.BytesIO()
        thumb.convert("RGB").save(tb, format="JPEG", quality=quality)
        thumb_bytes = tb.getvalue()

    # 2. Strip any legacy thumbnails / MPF containers first
    cleaned_jpeg, _ = strip_jpeg_thumbnails(data)

    # 3. Locate or insert EXIF APP1 segment
    n = len(cleaned_jpeg)
    app1_start: int | None = None
    app1_end: int | None = None
    existing_exif_payload: bytes | None = None

    i = 2
    while i < n:
        if cleaned_jpeg[i] != 0xFF:
            i += 1
            continue
        while i < n and cleaned_jpeg[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker_code = cleaned_jpeg[i]
        i += 1

        if marker_code in (0xD8, 0xD9) or (0xD0 <= marker_code <= 0xD7) or marker_code in (0x00, 0x01):
            continue

        if i + 2 > n:
            break
        seg_len = struct.unpack(">H", cleaned_jpeg[i : i + 2])[0]
        payload = cleaned_jpeg[i + 2 : i + seg_len]

        if marker_code == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            app1_start = i - 2
            app1_end = i + seg_len
            existing_exif_payload = payload
            break

        i += seg_len
        if marker_code == 0xDA:
            break

    # 4. Construct new EXIF payload with IFD1 thumbnail
    if existing_exif_payload is not None:
        new_payload = _inject_ifd1_to_exif(existing_exif_payload, thumb_bytes)
        # If too large for single JPEG segment (max 65533 bytes), fall back to minimal exif
        if len(new_payload) + 2 > 65535:
            new_payload = _build_minimal_exif(thumb_bytes)
    else:
        new_payload = _build_minimal_exif(thumb_bytes)

    new_app1 = b"\xff\xe1" + struct.pack(">H", len(new_payload) + 2) + new_payload

    # 5. Assemble final JPEG
    if app1_start is not None and app1_end is not None:
        return cleaned_jpeg[:app1_start] + new_app1 + cleaned_jpeg[app1_end:]

    # Insert after APP0 if present, otherwise immediately after SOI (index 2)
    if cleaned_jpeg[2:4] == b"\xff\xe0":
        app0_len = struct.unpack(">H", cleaned_jpeg[4:6])[0]
        insert_pos = 4 + app0_len
        return cleaned_jpeg[:insert_pos] + new_app1 + cleaned_jpeg[insert_pos:]
    else:
        return cleaned_jpeg[:2] + new_app1 + cleaned_jpeg[2:]


# ── Windows Icon & Thumbnail Cache Reset ──────────────────────────────────────

def clear_windows_thumbnail_cache() -> dict[str, Any]:
    """Safely invalidates Windows Shell icon and thumbnail caches.

    Uses SHChangeNotify(SHCNE_ASSOCCHANGED) for instant refresh, invokes ie4uinit.exe,
    and removes unlocked thumbcache files without terminating user processes.
    """
    results: dict[str, Any] = {
        "success": True,
        "platform": sys.platform,
        "actions": [],
        "files_cleared": 0,
        "errors": [],
    }

    if sys.platform != "win32":
        results["actions"].append("skipped_non_windows")
        return results

    # 1. Notify Windows Shell (Explorer) that system associations and icons changed
    try:
        import ctypes
        # SHCNE_ASSOCCHANGED = 0x08000000, SHCNF_IDLIST = 0
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        results["actions"].append("sh_change_notify")
    except Exception as e:
        results["errors"].append(f"SHChangeNotify failed: {e}")

    # 2. Invoke ie4uinit.exe to flush icon database
    try:
        # ie4uinit.exe -show flushes and reloads icon cache safely
        cmd = ["ie4uinit.exe", "-show"]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        results["actions"].append("ie4uinit_show")
    except Exception as e:
        results["errors"].append(f"ie4uinit failed: {e}")

    # 3. Clean unlocked thumbcache files in %LOCALAPPDATA%\\Microsoft\\Windows\\Explorer
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.expanduser(r"~\AppData\Local")

    explorer_cache_dir = Path(local_app_data) / "Microsoft" / "Windows" / "Explorer"
    if explorer_cache_dir.is_dir():
        for item in explorer_cache_dir.glob("thumbcache_*.db"):
            try:
                # Only remove if not locked by active explorer.exe
                item.unlink(missing_ok=True)
                results["files_cleared"] += 1
            except (PermissionError, OSError):
                # Locked files are in active use by Explorer, safely skip
                pass

    return results


# ── File & Directory Batch Processor ──────────────────────────────────────────

def process_file(
    file_path: Path | str,
    mode: str = "strip",
    size: tuple[int, int] = (160, 120),
    dry_run: bool = False,
    backup: bool = False,
    force: bool = False,
    quality: int = 75,
) -> FileProcessResult:
    """Processes a single JPEG photo according to selected mode (strip or rebuild)."""
    path = Path(file_path)
    if not path.is_file():
        return FileProcessResult(path=path, status="error", error=f"File not found: {path}")

    try:
        orig_bytes = path.read_bytes()
    except Exception as e:
        return FileProcessResult(path=path, status="error", error=f"Read error: {e}")

    orig_size = len(orig_bytes)

    if not orig_bytes.startswith(b"\xff\xd8"):
        return FileProcessResult(
            path=path,
            status="skipped",
            orig_size=orig_size,
            new_size=orig_size,
            details="Not a valid JPEG image (missing SOI)",
        )

    try:
        if mode == "strip":
            new_bytes, freed = strip_jpeg_thumbnails(orig_bytes)
            action_status = "stripped"
        elif mode == "rebuild":
            new_bytes = rebuild_jpeg_thumbnail(orig_bytes, size=size, quality=quality)
            freed = max(0, orig_size - len(new_bytes))
            action_status = "rebuilt"
        else:
            return FileProcessResult(path=path, status="error", error=f"Unknown mode: {mode}")
    except Exception as e:
        return FileProcessResult(path=path, status="error", orig_size=orig_size, error=str(e))

    new_size = len(new_bytes)
    bak_path: Path | None = None

    if not dry_run:
        # Safety backup (.bak)
        if backup:
            bak_path = path.with_suffix(path.suffix + ".bak")
            if bak_path.exists() and not force:
                return FileProcessResult(
                    path=path,
                    status="skipped",
                    orig_size=orig_size,
                    new_size=orig_size,
                    details=f"Backup file {bak_path.name} already exists. Use --force to overwrite.",
                )
            try:
                shutil.copy2(str(path), str(bak_path))
            except Exception as e:
                return FileProcessResult(
                    path=path,
                    status="error",
                    orig_size=orig_size,
                    error=f"Failed to create backup: {e}",
                )

        # Atomic in-place write
        try:
            temp_file = path.with_name(f".{path.name}.tmp.{os.getpid()}")
            temp_file.write_bytes(new_bytes)
            temp_file.replace(path)
        except Exception as e:
            return FileProcessResult(
                path=path,
                status="error",
                orig_size=orig_size,
                error=f"Write error: {e}",
            )

    return FileProcessResult(
        path=path,
        status=action_status,
        bytes_freed=freed,
        orig_size=orig_size,
        new_size=new_size,
        backup_path=bak_path,
    )


def process_directory(
    target: Path | str,
    mode: str = "strip",
    size: tuple[int, int] = (160, 120),
    dry_run: bool = False,
    backup: bool = False,
    force: bool = False,
    quality: int = 75,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[FileProcessResult]:
    """Dispatches preview fixing across a folder or single file."""
    root = Path(target)
    if not root.exists():
        raise FileNotFoundError(f"Target path does not exist: {root}")

    if root.is_file():
        files = [root]
    else:
        files = sorted(
            [
                p
                for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
            ]
        )

    results: list[FileProcessResult] = []
    total = len(files)

    for idx, f in enumerate(files):
        if progress_cb:
            progress_cb(idx + 1, total, f.name)
        res = process_file(
            file_path=f,
            mode=mode,
            size=size,
            dry_run=dry_run,
            backup=backup,
            force=force,
            quality=quality,
        )
        results.append(res)

    return results
