# -*- coding: utf-8 -*-
"""Asynchronous non-blocking streaming folder triage worker for Photo Healer.

Executes streaming O(1) RAM forensic audits across archive folders in a background
QThread, reading files in 64 KB chunks to preserve smooth 60 FPS UI interaction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

CHUNK_SIZE: int = 65536

IMAGE_MAGICS: dict[str, bytes] = {
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG",
    ".gif": b"GIF8",
    ".bmp": b"BM",
    ".tif": b"\x49\x49\x2a",
    ".tiff": b"\x4d\x4d\x00\x2a",
    ".webp": b"RIFF",
}

DEFAULT_EXTS: set[str] = set(IMAGE_MAGICS.keys())


def audit_file_streaming(path: Path) -> dict[str, Any]:
    """Streaming O(1) RAM classifier for TRIM-damaged image archives."""
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "name": path.name,
        "size": 0,
        "status": "error",
        "first_nonzero": -1,
        "note": "",
    }

    try:
        size = path.stat().st_size
    except OSError as e:
        result["note"] = f"Stat error: {e}"
        return result

    result["size"] = size
    if size == 0:
        result["status"] = "empty"
        result["note"] = "Zero-length file"
        return result

    ext = path.suffix.lower()
    expected_magic = IMAGE_MAGICS.get(ext)

    try:
        with open(path, "rb") as fh:
            offset = 0
            first_nz = -1
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                for idx, byte in enumerate(chunk):
                    if byte != 0:
                        first_nz = offset + idx
                        break
                if first_nz != -1:
                    break
                offset += len(chunk)
    except OSError as e:
        result["note"] = f"Read error: {e}"
        return result

    result["first_nonzero"] = first_nz

    if first_nz == -1:
        result["status"] = "trim_zero"
        result["note"] = f"100% TRIM-erased zeros ({size} bytes)"
        return result

    if first_nz == 0:
        if expected_magic:
            try:
                with open(path, "rb") as fh:
                    header = fh.read(len(expected_magic))
                if header == expected_magic:
                    result["status"] = "valid"
                    result["note"] = "Intact file magic"
                else:
                    result["status"] = "other"
                    hex_str = header[:8].hex(" ").upper()
                    result["note"] = f"Unexpected magic: {hex_str}"
            except OSError as e:
                result["note"] = f"Header read error: {e}"
        else:
            result["status"] = "other"
            result["note"] = f"Unknown magic for extension {ext}"
        return result

    # first_nz > 0: zero-filled prefix with live trailing bitstream
    result["status"] = "healed_candidate"
    result["note"] = f"TRIM header zeroed ({first_nz} bytes), live stream starts at {first_nz}"
    return result


class TriageWorker(QThread):
    """Background worker scanning folders for TRIM-damaged images."""

    progress = Signal(int, int, str)  # (current, total, filename)
    file_found = Signal(dict)         # (record_dict)
    finished = Signal(dict)           # (summary_dict)

    def __init__(
        self,
        folder_path: Path | str,
        extensions: set[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder_path = Path(folder_path)
        self.extensions: set[str] = (
            {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            if extensions is not None
            else DEFAULT_EXTS
        )
        self._is_stopped: bool = False

    def stop(self) -> None:
        """Request the worker to stop processing gracefully."""
        self._is_stopped = True
        self.requestInterruption()

    def run(self) -> None:
        """Scan directory tree and emit progress and classification signals."""
        summary: dict[str, Any] = {
            "folder": str(self.folder_path),
            "total_files": 0,
            "scanned_files": 0,
            "total_size": 0,
            "counts": {
                "healed_candidate": 0,
                "trim_zero": 0,
                "valid": 0,
                "empty": 0,
                "other": 0,
                "error": 0,
            },
            "sizes": {
                "healed_candidate": 0,
                "trim_zero": 0,
                "valid": 0,
                "empty": 0,
                "other": 0,
                "error": 0,
            },
            "status": "completed",
        }

        if not self.folder_path.is_dir():
            summary["status"] = "error"
            summary["error"] = f"Folder not found: {self.folder_path}"
            self.finished.emit(summary)
            return

        # 1. Discover target files
        candidate_paths: list[Path] = []
        for dirpath, _, filenames in os.walk(self.folder_path):
            if self._is_stopped or self.isInterruptionRequested():
                summary["status"] = "cancelled"
                self.finished.emit(summary)
                return

            for fname in filenames:
                p = Path(dirpath) / fname
                if p.suffix.lower() in self.extensions:
                    candidate_paths.append(p)

        total_files = len(candidate_paths)
        summary["total_files"] = total_files

        if total_files == 0:
            self.finished.emit(summary)
            return

        # 2. Audit files streaming O(1)
        for idx, file_path in enumerate(candidate_paths, 1):
            if self._is_stopped or self.isInterruptionRequested():
                summary["status"] = "cancelled"
                break

            record = audit_file_streaming(file_path)
            status = record.get("status", "error")
            size = record.get("size", 0)

            # Update counters
            summary["scanned_files"] += 1
            summary["total_size"] += size
            summary["counts"][status] = summary["counts"].get(status, 0) + 1
            summary["sizes"][status] = summary["sizes"].get(status, 0) + size

            # Emit live signals
            self.progress.emit(idx, total_files, file_path.name)
            self.file_found.emit(record)

        self.finished.emit(summary)
