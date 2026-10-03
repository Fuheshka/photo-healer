# -*- coding: utf-8 -*-
"""Background worker thread for batch thumbnail stripping, rebuilding, and cache reset.

Supports:
  - Streaming strip of donor thumbnails (EXIF IFD1 and APP2 MPF) without full JPEG decode
  - Rebuilding fresh IFD1 thumbnails from photo raster without quality loss
  - Safe Windows Explorer thumbnail and icon cache invalidation
  - Streaming progress signals, item completion events, and real-time logging
  - Graceful cancellation support without freezing the GUI
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QObject, QThread, Signal

from photo_healer.core.thumbnail import (
    FileProcessResult,
    clear_windows_thumbnail_cache,
    process_file,
)
from photo_healer.gui.i18n import t
from photo_healer.gui.models.file_table_model import format_size


class ThumbnailWorker(QThread):
    """Background worker thread for batch thumbnail operations and cache resetting."""

    progress = Signal(int, int, str)    # (current, total, current_filename)
    file_processed = Signal(object)     # FileProcessResult
    log_message = Signal(str)           # log string
    cache_cleared = Signal(dict)        # cache reset outcome dict
    finished = Signal(dict)             # summary dict

    def __init__(
        self,
        folder_path: Path | str | None = None,
        file_paths: Sequence[Path | str] | None = None,
        mode: str = "strip",
        size: tuple[int, int] = (160, 120),
        dry_run: bool = False,
        backup: bool = False,
        force: bool = False,
        quality: int = 75,
        clear_cache: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder_path = Path(folder_path) if folder_path else None
        self.file_paths = [Path(p) for p in file_paths] if file_paths else []
        self.mode = mode
        self.size = size
        self.dry_run = dry_run
        self.backup = backup
        self.force = force
        self.quality = quality
        self.clear_cache = clear_cache
        self._is_stopped = False

    def stop(self) -> None:
        """Gracefully request thread cancellation."""
        self._is_stopped = True
        self.requestInterruption()

    def run(self) -> None:
        """Execute thumbnail batch operations and optional cache reset."""
        summary: dict[str, Any] = {
            "total": 0,
            "processed": 0,
            "stripped": 0,
            "rebuilt": 0,
            "skipped": 0,
            "errors": 0,
            "bytes_freed": 0,
            "status": "completed",
            "duration_sec": 0.0,
            "cache_cleared": False,
        }

        # 1. Discover target files
        targets: list[Path] = []
        if self.file_paths:
            targets = [p for p in self.file_paths if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")]
        elif self.folder_path and self.folder_path.is_dir():
            targets = sorted(
                [
                    p
                    for p in self.folder_path.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ]
            )

        total = len(targets)
        summary["total"] = total

        start_time = time.perf_counter()

        if total > 0:
            for idx, file_path in enumerate(targets, start=1):
                if self._is_stopped or self.isInterruptionRequested():
                    summary["status"] = "cancelled"
                    self.log_message.emit(t("thumb_dialog.status_cancelled", default="Operation cancelled by user."))
                    break

                self.progress.emit(idx, total, file_path.name)

                res = process_file(
                    file_path=file_path,
                    mode=self.mode,
                    size=self.size,
                    dry_run=self.dry_run,
                    backup=self.backup,
                    force=self.force,
                    quality=self.quality,
                )

                summary["processed"] += 1
                if res.status == "stripped":
                    summary["stripped"] += 1
                    summary["bytes_freed"] += res.bytes_freed
                elif res.status == "rebuilt":
                    summary["rebuilt"] += 1
                    summary["bytes_freed"] += res.bytes_freed
                elif res.status == "skipped":
                    summary["skipped"] += 1
                elif res.status == "error":
                    summary["errors"] += 1

                self.file_processed.emit(res)

        # 2. Windows Cache Reset if requested
        if self.clear_cache and not (self._is_stopped or self.isInterruptionRequested()):
            cache_res = clear_windows_thumbnail_cache()
            summary["cache_cleared"] = cache_res.get("success", False)
            self.cache_cleared.emit(cache_res)

        summary["duration_sec"] = time.perf_counter() - start_time
        self.finished.emit(summary)
