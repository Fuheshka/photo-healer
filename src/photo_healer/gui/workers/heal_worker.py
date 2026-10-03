# -*- coding: utf-8 -*-
"""Background worker thread for single and batch photo healing operations.

Supports:
  - Auto-donor discovery with camera model (EXIF) and folder-level ranking
  - Reconstructive healing via HeaderSplicer and StreamResync (with pad_geometry)
  - Careful-mode backup creation (.bak) and overwrite protection
  - Streaming progress signals, item completion events, and real-time logging
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Sequence

from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal

from photo_healer.core.donor_pool import DonorIndex
from photo_healer.core.entropy import EntropyAnalyzer
from photo_healer.core.parser import JpegParser
from photo_healer.core.resync import StreamResync
from photo_healer.core.splicer import HeaderSplicer
from photo_healer.core.validator import JpegValidator
from photo_healer.gui.i18n import t
from photo_healer.gui.models.file_table_model import format_size


def extract_camera_info(jpeg_path: Path) -> tuple[str, str, tuple[int, int]]:
    """Extract make, model, and dimensions from JPEG EXIF if available."""
    make = ""
    model = ""
    size = (0, 0)
    try:
        with Image.open(jpeg_path) as im:
            size = im.size
            exif = im.getexif()
            if exif:
                make = str(exif.get(271, "")).strip()   # Tag 271: Make
                model = str(exif.get(272, "")).strip()  # Tag 272: Model
    except Exception:
        pass
    return make, model, size


def find_matching_donor(
    candidate_path: Path,
    archive_root: Path | None = None,
    exclude: Path | None = None,
    donor_index: DonorIndex | None = None,
) -> Path | None:
    """Find the best donor JPEG for candidate using DonorIndex or folder heuristics.

    Ranking:
      1. DonorIndex multi-criteria ranking (Tier 1-4 or DQT/geometry) if provided or indexed
      2. Intact JPEG in the same folder with matching filename prefix (legacy fallback)
      3. Any intact JPEG in the same folder
      4. Intact JPEG in parent folder
      5. Intact JPEG anywhere in archive_root
    """
    if donor_index is not None and len(donor_index) > 0:
        matches = donor_index.find_best_donor(candidate_path, exclude=exclude)
        if matches:
            return matches[0].path

    # Try building temporary index for immediate search dirs
    search_dirs: list[Path] = [candidate_path.parent]
    if candidate_path.parent.parent and candidate_path.parent.parent != candidate_path.parent:
        search_dirs.append(candidate_path.parent.parent)
    if archive_root and archive_root not in search_dirs and archive_root.is_dir():
        search_dirs.append(archive_root)

    temp_index = DonorIndex()
    for s_dir in search_dirs:
        if s_dir.is_dir():
            temp_index.add_folder(s_dir, recursive=(s_dir == archive_root))

    if len(temp_index) > 0:
        matches = temp_index.find_best_donor(candidate_path, exclude=exclude)
        if matches:
            return matches[0].path

    # Legacy fallback if temp_index could not find valid donor
    cand_name = candidate_path.name.upper()
    prefix = ""
    for ch in cand_name:
        if ch.isalpha() or ch == "_":
            prefix += ch
        else:
            break

    # 1. Look in same folder with prefix match
    folder = candidate_path.parent
    prefix_match: Path | None = None
    first_intact: Path | None = None

    if folder.is_dir():
        try:
            for item in sorted(folder.iterdir()):
                if item.suffix.lower() not in {".jpg", ".jpeg"}:
                    continue
                if exclude and item.resolve() == exclude.resolve():
                    continue
                if item.resolve() == candidate_path.resolve():
                    continue
                if "_HEALED" in item.name.upper() or item.name.endswith(".bak"):
                    continue

                try:
                    with open(item, "rb") as fh:
                        magic = fh.read(3)
                    if magic == b"\xff\xd8\xff":
                        if prefix and item.name.upper().startswith(prefix):
                            return item
                        if first_intact is None:
                            first_intact = item
                except OSError:
                    continue
        except OSError:
            pass

    if first_intact is not None:
        return first_intact

    # 2. Check outer search dirs
    for search_dir in search_dirs[1:]:
        if not search_dir.is_dir():
            continue
        try:
            for item in sorted(search_dir.iterdir()):
                if item.suffix.lower() not in {".jpg", ".jpeg"}:
                    continue
                if exclude and item.resolve() == exclude.resolve():
                    continue
                if "_HEALED" in item.name.upper() or item.name.endswith(".bak"):
                    continue
                try:
                    with open(item, "rb") as fh:
                        if fh.read(3) == b"\xff\xd8\xff":
                            return item
                except OSError:
                    continue
        except OSError:
            continue

    # 3. Fallback: search recursively across archive_root
    if archive_root and archive_root.is_dir():
        for dirpath, _, filenames in os.walk(archive_root):
            for fname in sorted(filenames):
                p = Path(dirpath) / fname
                if p.suffix.lower() not in {".jpg", ".jpeg"}:
                    continue
                if exclude and p.resolve() == exclude.resolve():
                    continue
                if p.resolve() == candidate_path.resolve():
                    continue
                if "_HEALED" in p.name.upper() or p.name.endswith(".bak"):
                    continue
                try:
                    with open(p, "rb") as fh:
                        if fh.read(3) == b"\xff\xd8\xff":
                            return p
                except OSError:
                    continue

    return None


class HealWorker(QThread):
    """Background worker for single and batch healing of damaged JPEG files."""

    progress = Signal(int, int, str)  # (current, total, current_filename)
    file_healed = Signal(dict)        # result dict
    log_message = Signal(str)         # status text
    finished = Signal(dict)           # summary dict

    def __init__(
        self,
        candidates: Sequence[Path | str | dict[str, Any]],
        donor_path: Path | str | None = None,
        auto_donor: bool = True,
        pad_geometry: bool = False,
        create_backup: bool = True,
        output_dir: Path | str | None = None,
        inplace: bool = False,
        archive_root: Path | str | None = None,
        strip_thumbnail: bool = True,
        donor_index: DonorIndex | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.candidates = list(candidates)
        self.manual_donor_path = Path(donor_path) if donor_path else None
        self.auto_donor = auto_donor
        self.pad_geometry = pad_geometry
        self.create_backup = create_backup
        self.output_dir = Path(output_dir) if output_dir else None
        self.inplace = inplace
        self.archive_root = Path(archive_root) if archive_root else None
        self.strip_thumbnail = strip_thumbnail
        self.donor_index = donor_index
        self._is_stopped = False

    def stop(self) -> None:
        """Gracefully request thread cancellation."""
        self._is_stopped = True
        self.requestInterruption()

    def run(self) -> None:
        total = len(self.candidates)
        summary: dict[str, Any] = {
            "total": total,
            "healed": 0,
            "skipped": 0,
            "errors": 0,
            "restored_bytes": 0,
            "status": "completed",
        }

        if total == 0:
            self.finished.emit(summary)
            return

        self.log_message.emit(t("heal.log.start_batch", count=total, default=f"Starting healing of {total} candidates..."))

        donor_header_cache: dict[Path, bytes] = {}

        def get_donor_header(p: Path) -> bytes | None:
            if p in donor_header_cache:
                return donor_header_cache[p]
            try:
                hdr = JpegParser(p).get_header_bytes()
                donor_header_cache[p] = hdr
                return hdr
            except Exception:
                return None

        for idx, item in enumerate(self.candidates, start=1):
            if self._is_stopped or self.isInterruptionRequested():
                summary["status"] = "cancelled"
                self.log_message.emit(t("heal.log.cancelled", default="Operation cancelled by user."))
                break

            # Normalize candidate path
            if isinstance(item, dict):
                cand_path = Path(item["path"])
            else:
                cand_path = Path(item)

            filename = cand_path.name
            self.progress.emit(idx, total, filename)

            if not cand_path.is_file():
                summary["errors"] += 1
                msg = t("heal.log.error", name=filename, error="File not found", default=f"[ERROR] {filename}: File not found")
                self.log_message.emit(msg)
                self.file_healed.emit({
                    "path": str(cand_path),
                    "name": filename,
                    "status": "error",
                    "error": "File not found",
                })
                continue

            # 1. Resolve donor
            donor_file: Path | None = None
            if self.manual_donor_path and self.manual_donor_path.is_file():
                donor_file = self.manual_donor_path
            elif self.auto_donor:
                donor_file = find_matching_donor(
                    cand_path,
                    archive_root=self.archive_root,
                    exclude=cand_path,
                    donor_index=self.donor_index,
                )

            if donor_file is None:
                summary["skipped"] += 1
                msg = t("heal.log.skipped", name=filename, reason="No donor found", default=f"[SKIP] {filename}: No donor found")
                self.log_message.emit(msg)
                self.file_healed.emit({
                    "path": str(cand_path),
                    "name": filename,
                    "status": "skipped",
                    "reason": "No donor found",
                })
                continue

            donor_hdr = get_donor_header(donor_file)
            if donor_hdr is None:
                summary["skipped"] += 1
                msg = t("heal.log.skipped", name=filename, reason="Failed to parse donor header", default=f"[SKIP] {filename}: Failed to parse donor")
                self.log_message.emit(msg)
                self.file_healed.emit({
                    "path": str(cand_path),
                    "name": filename,
                    "status": "skipped",
                    "reason": "Invalid donor header",
                })
                continue

            # 2. Reconstruct JPEG data
            try:
                cand_bytes = cand_path.read_bytes()
                healed_bytes: bytes | None = None
                method_used = "splice"

                # Check restart markers for StreamResync
                markers = StreamResync.scan_restart_markers(cand_bytes)
                if markers and len(markers) >= 2:
                    donor_for_resync = (
                        HeaderSplicer.strip_donor_thumbnails(donor_hdr)
                        if self.strip_thumbnail
                        else donor_hdr
                    )
                    resync_res = StreamResync.resync_stream(
                        cand_bytes,
                        donor=donor_for_resync,
                        pad_geometry=self.pad_geometry,
                    )
                    if resync_res.status == "resynced" or resync_res.total_markers_found > 0:
                        healed_bytes = resync_res.data
                        method_used = f"resync (padded={resync_res.padded_intervals_count})"

                if healed_bytes is None:
                    # Standard splice
                    offset = EntropyAnalyzer.detect_entropy_start(cand_bytes)
                    if offset is None:
                        offset = next((i for i, b in enumerate(cand_bytes) if b != 0), 65536)
                    splicer = HeaderSplicer(donor_hdr, strip_thumbnail=self.strip_thumbnail)
                    splice_res = splicer.splice_target(cand_bytes, entropy_offset=offset)
                    healed_bytes = splice_res.data
                    method_used = f"splice (offset={splice_res.entropy_offset})"

                # 3. Determine destination path
                if self.inplace:
                    out_path = cand_path
                    bak_path = cand_path.with_suffix(cand_path.suffix + ".bak")
                    if self.create_backup:
                        shutil.copy2(str(cand_path), str(bak_path))
                elif self.output_dir:
                    out_dir = self.output_dir
                    if self.archive_root:
                        try:
                            rel = cand_path.relative_to(self.archive_root)
                        except ValueError:
                            rel = Path(cand_path.name)
                    else:
                        rel = Path(cand_path.name)
                    out_path = out_dir / rel.parent / f"{cand_path.stem}_HEALED{cand_path.suffix}"
                else:
                    out_path = cand_path.parent / f"{cand_path.stem}_HEALED{cand_path.suffix}"

                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(healed_bytes)

                val = JpegValidator.validate(healed_bytes)
                dim = f"{val.width}x{val.height}" if val.width and val.height else "unknown"

                summary["healed"] += 1
                summary["restored_bytes"] += len(healed_bytes)

                log_line = t(
                    "heal.log.healed",
                    name=filename,
                    output=out_path.name,
                    size=format_size(len(healed_bytes)),
                    default=f"[OK] {filename} -> {out_path.name} ({format_size(len(healed_bytes))}, {dim})",
                )
                self.log_message.emit(log_line)

                self.file_healed.emit({
                    "path": str(cand_path),
                    "name": filename,
                    "status": "healed",
                    "output": str(out_path),
                    "size": len(healed_bytes),
                    "geometry": dim,
                    "method": method_used,
                    "donor": str(donor_file),
                })
            except Exception as e:
                summary["errors"] += 1
                err_msg = t("heal.log.error", name=filename, error=str(e), default=f"[ERROR] {filename}: {e}")
                self.log_message.emit(err_msg)
                self.file_healed.emit({
                    "path": str(cand_path),
                    "name": filename,
                    "status": "error",
                    "error": str(e),
                })

        done_msg = t(
            "heal.log.batch_done",
            healed=summary["healed"],
            skipped=summary["skipped"],
            errors=summary["errors"],
            default=f"Batch healing complete: {summary['healed']} healed, {summary['skipped']} skipped, {summary['errors']} errors.",
        )
        self.log_message.emit(done_msg)
        self.finished.emit(summary)
