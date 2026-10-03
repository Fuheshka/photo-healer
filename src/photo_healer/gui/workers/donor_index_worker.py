# -*- coding: utf-8 -*-
"""Background worker thread for asynchronous donor pool folder indexing.

Indexes multiple folders into a DonorIndex without blocking the UI,
providing live progress signals and cooperative thread cancellation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, QThread, Signal

from photo_healer.core.donor_pool import DonorIndex


class DonorIndexWorker(QThread):
    """Background worker that indexes donor folders into a DonorIndex."""

    progress = Signal(int, int, str)       # (current_index, total_folders, current_folder_name)
    folder_indexed = Signal(Path, int)     # (folder_path, jpeg_count_added)
    log_message = Signal(str)              # Status/log message
    finished = Signal(object)              # Emits the resulting DonorIndex

    def __init__(
        self,
        folders: Sequence[Path | str],
        existing_index: DonorIndex | None = None,
        recursive: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.folders = [Path(f).resolve() for f in folders]
        self.existing_index = existing_index
        self.recursive = recursive
        self._stop_flag = False

    def stop(self) -> None:
        """Signal the worker to halt indexing cleanly."""
        self._stop_flag = True
        self.requestInterruption()

    @property
    def is_stopped(self) -> bool:
        return self._stop_flag or self.isInterruptionRequested()

    def run(self) -> None:
        """Execute folder indexing across all specified directories."""
        index = self.existing_index if self.existing_index is not None else DonorIndex()
        total_folders = len(self.folders)

        if total_folders == 0:
            self.finished.emit(index)
            return

        for idx, folder in enumerate(self.folders, start=1):
            if self.is_stopped:
                self.log_message.emit("Indexing cancelled by user.")
                break

            folder_name = folder.name or str(folder)
            self.progress.emit(idx, total_folders, folder_name)

            if not folder.is_dir():
                self.log_message.emit(f"Folder not found: {folder}")
                self.folder_indexed.emit(folder, 0)
                continue

            try:
                added = index.add_folder(folder, recursive=self.recursive)
                self.folder_indexed.emit(folder, added)
                self.log_message.emit(f"Indexed {added} JPEGs from {folder_name}")
            except Exception as exc:
                self.log_message.emit(f"Error indexing {folder_name}: {exc}")
                self.folder_indexed.emit(folder, 0)

        self.finished.emit(index)
