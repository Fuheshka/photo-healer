# -*- coding: utf-8 -*-
"""Donor pool indexing and multi-criteria JPEG donor matching.

Complies with ITU-T T.81 / ISO/IEC 10918-1 standards for JPEG marker extraction.
Provides O(1) RAM streaming header indexing, persistent JSON pool caching with
mtime-based invalidation, and tiered multi-criteria ranking for damaged photos.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:
    from PIL import Image
    _HAS_PILLOW = True
except ImportError:  # pragma: no cover
    _HAS_PILLOW = False


def extract_filename_prefix(name: str) -> str:
    """Extract alphabetical camera/series prefix from a filename.

    Examples:
      'SANY0015.JPG' -> 'SANY'
      'IMG_1234.jpg' -> 'IMG_'
      'DSC0001.JPG'  -> 'DSC'
      'DSCF0001.JPG' -> 'DSCF'
      'P1010001.JPG' -> 'P'
      'DCIM001.JPG'  -> 'DCIM'
    """
    clean_name = Path(name).name.upper()
    prefix = ""
    for ch in clean_name:
        if ch.isalpha() or ch == "_":
            prefix += ch
        else:
            break
    return prefix


def _extract_camera_exif(source: Path | bytes) -> tuple[str | None, str | None]:
    """Extract camera Make (Tag 271) and Model (Tag 272) via Pillow if available."""
    if not _HAS_PILLOW:
        return None, None
    try:
        if isinstance(source, (str, Path)):
            with Image.open(source) as im:
                exif = im.getexif()
                if exif:
                    make = str(exif.get(271, "")).strip() or None
                    model = str(exif.get(272, "")).strip() or None
                    return make, model
        elif isinstance(source, (bytes, bytearray)):
            with Image.open(io.BytesIO(source)) as im:
                exif = im.getexif()
                if exif:
                    make = str(exif.get(271, "")).strip() or None
                    model = str(exif.get(272, "")).strip() or None
                    return make, model
    except Exception:
        pass
    return None, None


def extract_jpeg_metadata(
    source: Path | str | bytes | io.BufferedIOBase,
    chunk_size: int = 65536,
) -> dict[str, Any] | None:
    """Extract JPEG header metadata with O(1) RAM streaming.

    Reads only the header markers up to SOS (0xDA) or EOI (0xD9).
    Returns None if source is not a valid JPEG.
    """
    fh: io.BufferedIOBase | None = None
    should_close = False
    source_path: Path | None = None

    if isinstance(source, (str, Path)):
        source_path = Path(source)
        if not source_path.is_file():
            return None
        try:
            fh = open(source_path, "rb")
            should_close = True
        except OSError:
            return None
    elif isinstance(source, (bytes, bytearray)):
        fh = io.BytesIO(source)
        should_close = False
    elif isinstance(source, (io.BufferedIOBase, io.RawIOBase)):
        fh = source
        should_close = False
    else:
        return None

    try:
        # Check SOI: 0xFF 0xD8 0xFF
        initial_chunk = fh.read(chunk_size)
        if len(initial_chunk) < 4 or initial_chunk[:2] != b"\xff\xd8" or initial_chunk[2] != 0xFF:
            return None

        buffer = bytearray(initial_chunk)
        pos = 2  # right after SOI (0xFF 0xD8)

        dqt_tables: dict[int, bytes] = {}
        dht_tables: dict[tuple[int, int], bytes] = {}
        width: int | None = None
        height: int | None = None
        components: int | None = None
        subsampling: str | None = None

        while True:
            # Skip any fill/padding 0xFF bytes
            while pos < len(buffer) and buffer[pos] == 0xFF:
                pos += 1

            # Need at least 1 byte for marker code
            if pos >= len(buffer):
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                buffer.extend(chunk)
                while pos < len(buffer) and buffer[pos] == 0xFF:
                    pos += 1
                if pos >= len(buffer):
                    break

            marker_code = buffer[pos]
            pos += 1

            if marker_code in (0x00, 0xFF):
                continue

            # Standalone markers without length field
            if marker_code in (0xD8, 0xD9) or (0xD0 <= marker_code <= 0xD7) or marker_code == 0x01:
                if marker_code == 0xD9:  # EOI
                    break
                continue

            # Variable length marker: read 16-bit length
            while pos + 2 > len(buffer):
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                buffer.extend(chunk)

            if pos + 2 > len(buffer):
                break

            seg_len = struct.unpack(">H", buffer[pos : pos + 2])[0]
            if seg_len < 2:
                break

            # Ensure we have the full segment in buffer
            needed = (pos + seg_len) - len(buffer)
            while needed > 0:
                chunk = fh.read(max(needed, chunk_size))
                if not chunk:
                    break
                buffer.extend(chunk)
                needed = (pos + seg_len) - len(buffer)

            if pos + seg_len > len(buffer):
                break

            payload = bytes(buffer[pos + 2 : pos + seg_len])
            pos += seg_len

            # Process marker payload
            if marker_code == 0xDB:  # DQT
                offset = 0
                while offset < len(payload):
                    info = payload[offset]
                    precision = (info >> 4) & 0x0F
                    table_id = info & 0x0F
                    table_size = 64 if precision == 0 else 128
                    if offset + 1 + table_size <= len(payload):
                        table_bytes = payload[offset : offset + 1 + table_size]
                        dqt_tables[table_id] = table_bytes
                        offset += 1 + table_size
                    else:
                        break

            elif marker_code == 0xC4:  # DHT
                offset = 0
                while offset + 17 <= len(payload):
                    info = payload[offset]
                    tc = (info >> 4) & 0x0F  # table class (0=DC, 1=AC)
                    th = info & 0x0F         # table destination ID
                    counts = payload[offset + 1 : offset + 17]
                    total_symbols = sum(counts)
                    total_len = 1 + 16 + total_symbols
                    if offset + total_len <= len(payload):
                        table_bytes = payload[offset : offset + total_len]
                        dht_tables[(tc, th)] = table_bytes
                        offset += total_len
                    else:
                        break

            elif marker_code in (0xC0, 0xC1, 0xC2):  # SOF0, SOF1, SOF2
                if len(payload) >= 6:
                    _precision, h, w, comp = struct.unpack(">BHHB", payload[:6])
                    height = h
                    width = w
                    components = comp

                    # Parse sampling factors
                    comp_factors: list[tuple[int, int]] = []
                    offset = 6
                    for _ in range(comp):
                        if offset + 3 <= len(payload):
                            _cid = payload[offset]
                            factors = payload[offset + 1]
                            h_factor = (factors >> 4) & 0x0F
                            v_factor = factors & 0x0F
                            comp_factors.append((h_factor, v_factor))
                            offset += 3

                    if comp == 1:
                        subsampling = "gray"
                    elif comp == 3 and len(comp_factors) >= 3:
                        h0, v0 = comp_factors[0]
                        h1, v1 = comp_factors[1]
                        h2, v2 = comp_factors[2]
                        if (h0, v0) == (2, 2) and (h1, v1) == (1, 1) and (h2, v2) == (1, 1):
                            subsampling = "4:2:0"
                        elif (h0, v0) == (2, 1) and (h1, v1) == (1, 1) and (h2, v2) == (1, 1):
                            subsampling = "4:2:2"
                        elif (h0, v0) == (1, 1) and (h1, v1) == (1, 1) and (h2, v2) == (1, 1):
                            subsampling = "4:4:4"
                        elif (h0, v0) == (1, 2) and (h1, v1) == (1, 1) and (h2, v2) == (1, 1):
                            subsampling = "4:4:0"
                        else:
                            subsampling = f"{h0}x{v0},{h1}x{v1},{h2}x{v2}"

            elif marker_code == 0xDA:  # SOS: compressed bitstream begins
                break

        # Calculate DQT & DHT hashes
        dqt_hash: str | None = None
        if dqt_tables:
            concat_dqt = b"".join(dqt_tables[k] for k in sorted(dqt_tables.keys()))
            dqt_hash = hashlib.sha256(concat_dqt).hexdigest()

        dht_hash: str | None = None
        if dht_tables:
            concat_dht = b"".join(dht_tables[k] for k in sorted(dht_tables.keys()))
            dht_hash = hashlib.sha256(concat_dht).hexdigest()

        camera_make, camera_model = _extract_camera_exif(source_path if source_path else bytes(buffer))

        return {
            "width": width,
            "height": height,
            "components": components,
            "subsampling": subsampling,
            "dqt_hash": dqt_hash,
            "dht_hash": dht_hash,
            "camera_make": camera_make,
            "camera_model": camera_model,
        }

    finally:
        if should_close and fh:
            fh.close()


@dataclass
class DonorEntry:
    """Indexed donor JPEG metadata."""

    path: str
    file_size: int
    mtime: float
    prefix: str
    width: int | None = None
    height: int | None = None
    components: int | None = None
    subsampling: str | None = None
    dqt_hash: str | None = None
    dht_hash: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).resolve()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DonorEntry:
        return cls(**data)


@dataclass
class DonorMatch:
    """Ranked match result for a candidate image."""

    entry: DonorEntry
    score: float
    match_reason: str
    tier: int

    @property
    def path(self) -> Path:
        return self.entry.resolved_path


class DonorIndex:
    """In-memory index of healthy JPEG donors with fast multi-index lookup."""

    def __init__(self) -> None:
        self._entries: dict[str, DonorEntry] = {}
        self._folder_mtimes: dict[str, float] = {}

        # Inverted index mappings
        self._by_dqt: dict[str, list[str]] = {}
        self._by_prefix: dict[str, list[str]] = {}
        self._by_folder: dict[str, list[str]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[DonorEntry]:
        return list(self._entries.values())

    @property
    def folders(self) -> list[Path]:
        return [Path(p) for p in self._folder_mtimes.keys()]

    def _register_entry(self, entry: DonorEntry) -> None:
        canon_path = str(entry.resolved_path)
        if canon_path in self._entries:
            return

        self._entries[canon_path] = entry

        # Update inverted indexes
        if entry.dqt_hash:
            self._by_dqt.setdefault(entry.dqt_hash, []).append(canon_path)
        if entry.prefix:
            self._by_prefix.setdefault(entry.prefix, []).append(canon_path)

        folder_key = str(entry.resolved_path.parent)
        self._by_folder.setdefault(folder_key, []).append(canon_path)

    def _unregister_entry(self, canon_path: str) -> None:
        entry = self._entries.pop(canon_path, None)
        if not entry:
            return

        if entry.dqt_hash and entry.dqt_hash in self._by_dqt:
            self._by_dqt[entry.dqt_hash] = [p for p in self._by_dqt[entry.dqt_hash] if p != canon_path]
        if entry.prefix and entry.prefix in self._by_prefix:
            self._by_prefix[entry.prefix] = [p for p in self._by_prefix[entry.prefix] if p != canon_path]

        folder_key = str(entry.resolved_path.parent)
        if folder_key in self._by_folder:
            self._by_folder[folder_key] = [p for p in self._by_folder[folder_key] if p != canon_path]

    def add_folder(self, folder_path: Path | str, recursive: bool = True) -> int:
        """Scan and register all valid JPEG files in a folder.

        Returns number of newly registered donors.
        """
        folder = Path(folder_path).resolve()
        if not folder.is_dir():
            return 0

        self._folder_mtimes[str(folder)] = folder.stat().st_mtime
        added = 0

        iterator = folder.rglob("*") if recursive else folder.glob("*")
        for item in iterator:
            if not item.is_file():
                continue
            if item.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            if "_HEALED" in item.name.upper() or item.name.endswith(".bak"):
                continue

            try:
                st = item.stat()
                file_size = st.st_size
                mtime = st.st_mtime
            except OSError:
                continue

            meta = extract_jpeg_metadata(item)
            if meta is None:
                continue

            prefix = extract_filename_prefix(item.name)
            parent_dir = item.parent.resolve()
            if str(parent_dir) not in self._folder_mtimes:
                try:
                    self._folder_mtimes[str(parent_dir)] = parent_dir.stat().st_mtime
                except OSError:
                    pass

            entry = DonorEntry(
                path=str(item.resolve()),
                file_size=file_size,
                mtime=mtime,
                prefix=prefix,
                width=meta.get("width"),
                height=meta.get("height"),
                components=meta.get("components"),
                subsampling=meta.get("subsampling"),
                dqt_hash=meta.get("dqt_hash"),
                dht_hash=meta.get("dht_hash"),
                camera_make=meta.get("camera_make"),
                camera_model=meta.get("camera_model"),
            )
            self._register_entry(entry)
            added += 1

        return added

    def add_folders(self, paths: Sequence[Path | str], recursive: bool = True) -> int:
        """Batch add multiple folders."""
        total = 0
        for p in paths:
            total += self.add_folder(p, recursive=recursive)
        return total

    def remove_folder(self, folder_path: Path | str) -> int:
        """Remove all entries originating from folder_path."""
        target = Path(folder_path).resolve()
        removed = 0

        folder_key = str(target)
        self._folder_mtimes.pop(folder_key, None)

        paths_to_remove = [
            p for p, entry in self._entries.items()
            if target == entry.resolved_path.parent or target in entry.resolved_path.parents
        ]
        for p in paths_to_remove:
            self._unregister_entry(p)
            removed += 1

        return removed

    def is_cache_valid(self) -> bool:
        """Check if cached folder mtimes match current filesystem state."""
        for folder_str, recorded_mtime in self._folder_mtimes.items():
            f_path = Path(folder_str)
            if not f_path.is_dir():
                return False
            try:
                current_mtime = f_path.stat().st_mtime
                if abs(current_mtime - recorded_mtime) > 0.001:
                    return False
            except OSError:
                return False
        return True

    def save_to_json(self, path: Path | str) -> None:
        """Serialize index to JSON."""
        data = {
            "version": 1,
            "folder_mtimes": self._folder_mtimes,
            "entries": [entry.to_dict() for entry in self._entries.values()],
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load_from_json(cls, path: Path | str) -> DonorIndex:
        """Load index from JSON."""
        target = Path(path)
        content = json.loads(target.read_text(encoding="utf-8"))
        index = cls()
        index._folder_mtimes = content.get("folder_mtimes", {})
        for entry_dict in content.get("entries", []):
            entry = DonorEntry.from_dict(entry_dict)
            index._register_entry(entry)
        return index

    def find_best_donor(
        self,
        candidate_path: Path | str,
        exclude: Path | str | None = None,
    ) -> list[DonorMatch]:
        """Find best donor matches using multi-criteria ranking."""
        return find_best_donor(candidate_path, self, exclude=exclude)


def find_best_donor(
    candidate_path: Path | str,
    index: DonorIndex,
    exclude: Path | str | None = None,
) -> list[DonorMatch]:
    """Find and rank compatible donors from DonorIndex for candidate_path.

    Returns sorted list of DonorMatch descending by compatibility score.
    """
    cand = Path(candidate_path).resolve()
    if len(index) == 0:
        return []

    exclude_path = Path(exclude).resolve() if exclude else None

    # Filter out self, exclude, and temporary files
    available_entries = [
        e for e in index.entries
        if e.resolved_path != cand
        and (exclude_path is None or e.resolved_path != exclude_path)
        and "_HEALED" not in e.resolved_path.name.upper()
        and not e.resolved_path.name.endswith(".bak")
    ]
    if not available_entries:
        return []

    cand_size = cand.stat().st_size if cand.is_file() else 0
    cand_prefix = extract_filename_prefix(cand.name)
    cand_folder = str(cand.parent.resolve())

    # Check if candidate has readable header (non-TRIM damaged file)
    cand_meta = extract_jpeg_metadata(cand) if cand.is_file() else None

    matches: list[DonorMatch] = []

    # Strategy A: Readable DQT hash in candidate (Exact quantization match)
    if cand_meta and cand_meta.get("dqt_hash"):
        target_dqt = cand_meta["dqt_hash"]
        for entry in available_entries:
            if entry.dqt_hash == target_dqt:
                reason = "Exact DQT quantization match (100% compatible)"
                score = 1.0
                tier = 0
                matches.append(DonorMatch(entry=entry, score=score, match_reason=reason, tier=tier))

        if matches:
            matches.sort(key=lambda m: (m.tier, -m.score, abs(m.entry.file_size - cand_size)))
            return matches

    # Strategy B: Readable SOF0 geometry in candidate
    if cand_meta and cand_meta.get("width") and cand_meta.get("height"):
        target_w = cand_meta["width"]
        target_h = cand_meta["height"]
        target_sub = cand_meta.get("subsampling")

        for entry in available_entries:
            if entry.width == target_w and entry.height == target_h:
                tier = 1
                base_score = 0.88
                if target_sub and entry.subsampling == target_sub:
                    base_score += 0.04
                if cand_prefix and entry.prefix == cand_prefix:
                    base_score += 0.04
                if entry.file_size > 0 and cand_size > 0:
                    size_sim = max(0.0, 1.0 - abs(cand_size - entry.file_size) / max(cand_size, 1))
                    base_score += 0.02 * size_sim

                score = min(0.98, base_score)
                reason = f"Geometry match ({entry.width}x{entry.height} {entry.subsampling or ''})".strip()
                matches.append(DonorMatch(entry=entry, score=score, match_reason=reason, tier=tier))

        if matches:
            matches.sort(key=lambda m: (m.tier, -m.score, abs(m.entry.file_size - cand_size)))
            return matches

    # Strategy C: Zeroed / TRIM candidate (Header erased, use folder, prefix, size cluster)
    for entry in available_entries:
        is_same_folder = (str(entry.resolved_path.parent) == cand_folder)
        is_same_prefix = bool(cand_prefix and entry.prefix == cand_prefix)

        size_diff = abs(cand_size - entry.file_size)
        size_diff_pct = size_diff / max(cand_size, 1)
        size_sim = max(0.0, 1.0 - size_diff_pct)

        if is_same_folder and is_same_prefix:
            tier = 1
            score = 0.92 + 0.06 * size_sim
            reason = f"Same folder + prefix match ({entry.prefix})"
        elif is_same_prefix:
            tier = 2
            score = 0.78 + 0.08 * size_sim
            reason = f"Prefix match across pool ({entry.prefix})"
        elif size_diff_pct <= 0.15 and cand_size > 0:
            tier = 3
            score = 0.55 + 0.10 * (1.0 - size_diff_pct / 0.15)
            reason = f"Size cluster match (within {size_diff_pct * 100:.1f}%)"
        else:
            tier = 4
            score = 0.15 + 0.10 * size_sim
            reason = "Pool fallback donor"

        matches.append(DonorMatch(entry=entry, score=score, match_reason=reason, tier=tier))

    matches.sort(key=lambda m: (m.tier, -m.score, abs(m.entry.file_size - cand_size)))
    return matches
