# -*- coding: utf-8 -*-
"""Asynchronous non-blocking preview extractor worker for Photo Healer.

Scans archive folders or candidate files in a background QThread to carve embedded
previews (APP2 MPF Full HD, APP1 EXIF thumbnails, and raw carved streams)
without blocking the UI event loop.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QObject, QThread, Signal

from photo_healer.core.carver import ThumbnailCarver
from photo_healer.core.validator import JpegValidator
from photo_healer.gui.i18n import t
from photo_healer.gui.models.file_table_model import format_size

DEFAULT_IMAGE_EXTS: set[str] = {".jpg", ".jpeg"}


def format_preview_type_label(preview_type: str) -> str:
    """Format human-readable label for preview type."""
    if preview_type == "mpf":
        return "APP2 MPF Full HD"
    elif preview_type == "exif_thumb":
        return "APP1 EXIF Thumb"
    elif preview_type == "raw_carved":
        return "Raw Carved Stream"
    return preview_type.upper()


class CarveWorker(QThread):
    """Background worker for extracting embedded previews and thumbnails."""

    progress = Signal(int, int, str)   # (current, total, current_filename)
    preview_found = Signal(dict)       # preview metadata dict with 'data' bytes
    log_message = Signal(str)          # log message string
    finished = Signal(dict)            # summary dict

    def __init__(
        self,
        folder_path: Path | str | None = None,
        file_paths: Sequence[Path | str] | None = None,
        extensions: set[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder_path = Path(folder_path) if folder_path else None
        self.file_paths = [Path(p) for p in file_paths] if file_paths else []
        self.extensions: set[str] = (
            {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            if extensions is not None
            else DEFAULT_IMAGE_EXTS
        )
        self._is_stopped: bool = False

    def stop(self) -> None:
        """Signal the worker to stop processing gracefully."""
        self._is_stopped = True
        self.requestInterruption()

    def run(self) -> None:
        """Scan target files and extract embedded previews."""
        summary: dict[str, Any] = {
            "folder": str(self.folder_path) if self.folder_path else "",
            "total_files": 0,
            "scanned_files": 0,
            "previews_found": 0,
            "mpf_count": 0,
            "exif_count": 0,
            "raw_count": 0,
            "status": "completed",
        }

        # 1. Discover target files
        targets: list[Path] = []
        if self.file_paths:
            targets.extend([p for p in self.file_paths if p.is_file()])
        elif self.folder_path and self.folder_path.is_dir():
            for dirpath, _, filenames in os.walk(self.folder_path):
                if self._is_stopped or self.isInterruptionRequested():
                    summary["status"] = "cancelled"
                    self.finished.emit(summary)
                    return
                for fname in sorted(filenames):
                    p = Path(dirpath) / fname
                    if p.suffix.lower() in self.extensions:
                        targets.append(p)
        elif self.folder_path and not self.folder_path.is_dir():
            summary["status"] = "error"
            summary["error"] = f"Folder not found: {self.folder_path}"
            self.finished.emit(summary)
            return

        summary["total_files"] = len(targets)
        total = len(targets)

        if total == 0:
            self.finished.emit(summary)
            return

        # 2. Iterate and carve previews
        for idx, file_path in enumerate(targets):
            if self._is_stopped or self.isInterruptionRequested():
                summary["status"] = "cancelled"
                self.finished.emit(summary)
                return

            self.progress.emit(idx + 1, total, file_path.name)
            summary["scanned_files"] += 1

            try:
                self._process_single_file(file_path, summary)
            except Exception as exc:
                self.log_message.emit(f"Error carving {file_path.name}: {exc}")

        self.finished.emit(summary)

    def _process_single_file(self, file_path: Path, summary: dict[str, Any]) -> None:
        """Attempt to extract MPF, EXIF thumb, or raw carved previews from file."""
        found_any = False
        try:
            data_bytes = file_path.read_bytes()
        except OSError:
            return

        if len(data_bytes) < 32:
            return

        # 1. APP2 MPF Full HD Preview
        try:
            mpf_data = ThumbnailCarver.extract_mpf_preview(data_bytes)
            if mpf_data:
                val = JpegValidator.validate(mpf_data)
                self._emit_preview(
                    file_path=file_path,
                    data=mpf_data,
                    preview_type="mpf",
                    width=val.width,
                    height=val.height,
                    summary=summary,
                )
                found_any = True
        except Exception:
            pass

        # 2. APP1 EXIF Thumbnail
        try:
            exif_data = ThumbnailCarver.extract_exif_thumbnail(data_bytes)
            if exif_data:
                val = JpegValidator.validate(exif_data)
                self._emit_preview(
                    file_path=file_path,
                    data=exif_data,
                    preview_type="exif_thumb",
                    width=val.width,
                    height=val.height,
                    summary=summary,
                )
                found_any = True
        except Exception:
            pass

        # 3. Fallback: Raw stream carving if no structured preview found and file damaged
        if not found_any:
            try:
                raw_streams = ThumbnailCarver.raw_stream_carve(data_bytes)
                for stream_bytes in raw_streams:
                    # Ignore if identical to whole file
                    if len(stream_bytes) < len(data_bytes) or not data_bytes.startswith(b"\xff\xd8"):
                        val = JpegValidator.validate(stream_bytes)
                        if val.is_valid:
                            self._emit_preview(
                                file_path=file_path,
                                data=stream_bytes,
                                preview_type="raw_carved",
                                width=val.width,
                                height=val.height,
                                summary=summary,
                            )
                            found_any = True
                            break  # Best raw stream is enough
            except Exception:
                pass

    def _emit_preview(
        self,
        file_path: Path,
        data: bytes,
        preview_type: str,
        width: int | None,
        height: int | None,
        summary: dict[str, Any],
    ) -> None:
        """Emit preview metadata record."""
        size_bytes = len(data)
        size_kb = round(size_bytes / 1024.0, 1)
        res_str = f"{width}x{height}" if width and height else "Unknown"

        record: dict[str, Any] = {
            "source_path": str(file_path.resolve()),
            "source_name": file_path.name,
            "preview_type": preview_type,
            "type_label": format_preview_type_label(preview_type),
            "width": width,
            "height": height,
            "resolution": res_str,
            "size_bytes": size_bytes,
            "size_kb": size_kb,
            "size_str": f"{size_kb:.1f} KB",
            "data": data,
        }

        summary["previews_found"] += 1
        if preview_type == "mpf":
            summary["mpf_count"] += 1
        elif preview_type == "exif_thumb":
            summary["exif_count"] += 1
        elif preview_type == "raw_carved":
            summary["raw_count"] += 1

        self.preview_found.emit(record)
