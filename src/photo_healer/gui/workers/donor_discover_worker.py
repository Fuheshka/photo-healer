# -*- coding: utf-8 -*-
"""Background worker thread for auto-discovering donor photo folders.

Scans standard system directories, removable drives (SD/USB), and optional
custom roots without blocking the GUI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, QThread, Signal

from photo_healer.core.donor_discovery import DonorDiscovery, DonorFolderCandidate


class DonorDiscoverWorker(QThread):
    """Background worker that discovers donor folder candidates."""

    folder_found = Signal(object)          # Emits DonorFolderCandidate as found
    progress = Signal(int, int, str)       # (current, total, current_path_name)
    log_message = Signal(str)              # Status/log message
    finished = Signal(list)                # Emits list[DonorFolderCandidate]

    def __init__(
        self,
        root_dirs: Sequence[Path | str] | None = None,
        max_depth: int = 3,
        target_prefixes: set[str] | None = None,
        archive_root: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.root_dirs = [Path(r).resolve() for r in root_dirs] if root_dirs else None
        self.max_depth = max_depth
        self.target_prefixes = set(target_prefixes) if target_prefixes else None
        self.archive_root = Path(archive_root).resolve() if archive_root else None
        self._stop_flag = False

    def stop(self) -> None:
        """Signal the worker to halt scanning cleanly."""
        self._stop_flag = True
        self.requestInterruption()

    @property
    def is_stopped(self) -> bool:
        return self._stop_flag or self.isInterruptionRequested()

    def run(self) -> None:
        """Execute discovery across system folders, removable drives, or specified roots."""
        # Determine roots to scan
        if self.root_dirs:
            roots_to_scan = list(self.root_dirs)
        else:
            roots_to_scan = DonorDiscovery.discover_system_photo_folders()
            # If archive_root is specified, also scan its drive/parent area
            if self.archive_root and self.archive_root.is_dir():
                parent_dir = self.archive_root.parent
                if parent_dir and parent_dir != self.archive_root and parent_dir not in roots_to_scan:
                    roots_to_scan.append(parent_dir)

        total_roots = len(roots_to_scan)
        all_candidates: list[DonorFolderCandidate] = []
        seen_paths: set[str] = set()

        for idx, root in enumerate(roots_to_scan, start=1):
            if self.is_stopped:
                self.log_message.emit("Folder discovery cancelled by user.")
                break

            root_name = root.name or str(root)
            self.progress.emit(idx, total_roots, root_name)

            if not root.is_dir():
                continue

            try:
                candidates = DonorDiscovery.discover_donor_folders(
                    root=root,
                    max_depth=self.max_depth,
                    target_prefixes=self.target_prefixes,
                    archive_root=self.archive_root,
                    is_cancelled=lambda: self.is_stopped,
                )
                for cand in candidates:
                    canon_key = str(cand.path).lower()
                    if canon_key not in seen_paths:
                        seen_paths.add(canon_key)
                        all_candidates.append(cand)
                        self.folder_found.emit(cand)
            except Exception as exc:
                self.log_message.emit(f"Error scanning {root_name}: {exc}")

        # Final sort by priority score descending, then jpeg_count
        all_candidates.sort(key=lambda c: (-c.priority_score, -c.jpeg_count))
        self.finished.emit(all_candidates)
