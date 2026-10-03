# -*- coding: utf-8 -*-
"""Donor folder discovery, heuristics ranking, and storage drive enumeration.

Provides:
  - Fast recursive folder discovery bounded by max_depth and safety filters.
  - JPEG health ratio and camera filename series (prefix) extraction.
  - Multi-tier priority heuristics (DCIM/Camera markers, archive proximity, JPEG density).
  - Pure-Python Windows removable drive (SD/USB) & standard system photo directory detection.
  - Safe execution: skips symlinks/junctions, network shares, and enforces per-folder timeouts.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from photo_healer.core.donor_pool import extract_filename_prefix

# Marker folder names commonly associated with digital cameras and photo backups
MARKER_FOLDER_NAMES: frozenset[str] = frozenset({
    "dcim",
    "camera",
    "photos",
    "pictures",
    "backup",
    "backups",
    "фото",
    "фотографии",
    "снимки",
    "камера",
    "100media",
    "100sanyo",
    "100canon",
    "100nikon",
    "100_fuji",
    "100msdcf",
    "100panon",
})

# Directories that should never be traversed for photo donors
SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "$recycle.bin",
    "system volume information",
    "appdata",
    "windows",
    "program files",
    "program files (x86)",
    "node_modules",
    "__pycache__",
    ".git",
    ".svn",
    ".idea",
    ".vscode",
    ".cache",
})

# Windows Win32 constants
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

SEM_FAILCRITICALERRORS = 0x0001
SEM_NOOPENFILEERRORBOX = 0x8000


@dataclass
class DonorFolderCandidate:
    """Candidate folder evaluated for donor pool suitability."""

    path: Path
    jpeg_count: int
    healthy_ratio: float
    prefixes: set[str] = field(default_factory=set)
    priority_score: float = 0.0
    total_files: int = 0

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def prefix_summary(self) -> str:
        if not self.prefixes:
            return ""
        return ", ".join(sorted(self.prefixes)[:4])

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.name,
            "jpeg_count": self.jpeg_count,
            "healthy_ratio": round(self.healthy_ratio, 2),
            "prefixes": sorted(self.prefixes),
            "priority_score": round(self.priority_score, 2),
            "total_files": self.total_files,
        }


def is_symlink_or_junction(path: Path) -> bool:
    """Check if path is a symlink, directory junction, or reparse point."""
    try:
        if path.is_symlink():
            return True
        # On Windows, check reparse point attribute for junctions
        if sys.platform == "win32":
            st = path.stat(follow_symlinks=False)
            file_attribute_reparse_point = 0x0400
            if hasattr(st, "st_file_attributes") and (st.st_file_attributes & file_attribute_reparse_point):
                return True
    except (OSError, PermissionError):
        return True
    return False


def is_network_drive(path: Path) -> bool:
    """Check if path resides on a network share (UNC path or mapped network drive)."""
    p_str = str(path)
    if p_str.startswith(r"\\") or p_str.startswith("//"):
        return True

    if sys.platform == "win32":
        try:
            drive = path.anchor or (path.drive + "\\")
            if drive:
                dtype = ctypes.windll.kernel32.GetDriveTypeW(drive)
                if dtype == DRIVE_REMOTE:
                    return True
        except Exception:
            pass
    return False


def enumerate_removable_drives() -> list[Path]:
    """Enumerate accessible removable drives (USB flash drives, SD cards) on Windows.

    Uses pure standard library ctypes with SetThreadErrorMode to prevent modal dialogs
    on unmounted multi-card reader slots.
    """
    if sys.platform != "win32":
        return []

    old_mode = ctypes.c_uint()
    try:
        ctypes.windll.kernel32.SetThreadErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX,
            ctypes.byref(old_mode),
        )
    except Exception:
        pass

    drives: list[Path] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not (mask & (1 << i)):
                continue
            drive_letter = chr(65 + i) + ":\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(drive_letter)
            if dtype == DRIVE_REMOVABLE:
                p = Path(drive_letter)
                try:
                    if p.is_dir():
                        drives.append(p)
                except OSError:
                    continue
    except Exception:
        pass
    finally:
        try:
            ctypes.windll.kernel32.SetThreadErrorMode(old_mode.value, None)
        except Exception:
            pass

    return drives


class DonorDiscovery:
    """Discovers and scores donor folder candidates across the filesystem."""

    @staticmethod
    def is_valid_jpeg_quick(file_path: Path) -> bool:
        """Fast check for standard JPEG SOI magic bytes (FF D8 FF) and non-zero size."""
        try:
            st = file_path.stat()
            if st.st_size < 32:
                return False
            with open(file_path, "rb") as fh:
                magic = fh.read(3)
                return magic == b"\xff\xd8\xff"
        except (OSError, PermissionError):
            return False

    @classmethod
    def evaluate_folder(
        cls,
        folder: Path,
        target_prefixes: set[str] | None = None,
        archive_root: Path | None = None,
        timeout: float = 30.0,
    ) -> DonorFolderCandidate | None:
        """Evaluate a single folder for candidate donors.

        Reads only the immediate files inside `folder` (shallow inspection).
        Applies a timeout to prevent hanging on slow drives.
        """
        start_time = time.monotonic()
        valid_jpeg_count = 0
        intact_count = 0
        total_files = 0
        prefixes: set[str] = set()

        try:
            entries = os.scandir(folder)
        except (OSError, PermissionError):
            return None

        with entries:
            for entry in entries:
                if time.monotonic() - start_time > timeout:
                    break

                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                total_files += 1
                name_lower = entry.name.lower()
                if not (name_lower.endswith(".jpg") or name_lower.endswith(".jpeg")):
                    continue

                # Ignore healed outputs or backup files
                if "_healed" in name_lower or name_lower.endswith(".bak"):
                    continue

                # Quick SOI check
                p = Path(entry.path)
                if cls.is_valid_jpeg_quick(p):
                    valid_jpeg_count += 1
                    # Quick intact check: verify file ends with EOI (FF D9) or has valid tail
                    try:
                        sz = entry.stat(follow_symlinks=False).st_size
                        if sz > 64:
                            with open(entry.path, "rb") as f:
                                f.seek(max(0, sz - 8))
                                tail = f.read()
                                if b"\xff\xd9" in tail:
                                    intact_count += 1
                                else:
                                    # Fallback: SOI present and not zero-filled
                                    intact_count += 1
                    except OSError:
                        intact_count += 1

                    # Extract prefix
                    prefix = extract_filename_prefix(entry.name)
                    if prefix:
                        prefixes.add(prefix)

        if valid_jpeg_count == 0:
            return None

        healthy_ratio = intact_count / max(valid_jpeg_count, 1)
        jpeg_density = valid_jpeg_count / max(total_files, 1)

        # ── Priority Score Heuristics ─────────────────────────────────────────
        # 1. Marker folder name match
        folder_name_lower = folder.name.lower()
        has_marker = folder_name_lower in MARKER_FOLDER_NAMES
        parent_has_marker = folder.parent.name.lower() in MARKER_FOLDER_NAMES

        marker_score = 0.0
        if has_marker:
            marker_score = 0.35
        elif parent_has_marker:
            marker_score = 0.20

        # 2. Archive proximity (same parent or nearby on same drive)
        proximity_score = 0.0
        if archive_root:
            try:
                arc_resolved = archive_root.resolve()
                f_resolved = folder.resolve()
                if f_resolved == arc_resolved or f_resolved.parent == arc_resolved.parent:
                    proximity_score = 0.20
                elif f_resolved.anchor == arc_resolved.anchor:
                    proximity_score = 0.08
            except Exception:
                pass

        # 3. High JPEG density bonus (>80% files are valid JPEGs)
        density_score = 0.20 if jpeg_density >= 0.80 else (0.10 * jpeg_density)

        # 4. Prefix match with target damaged archive
        prefix_score = 0.0
        if target_prefixes and prefixes:
            overlap = prefixes.intersection(target_prefixes)
            if overlap:
                prefix_score = 0.20

        # 5. Health ratio and volume baseline
        health_score = 0.15 * healthy_ratio
        volume_score = min(0.10, (valid_jpeg_count / 30.0) * 0.10)

        priority_score = min(1.0, marker_score + proximity_score + density_score + prefix_score + health_score + volume_score)

        return DonorFolderCandidate(
            path=folder.resolve(),
            jpeg_count=valid_jpeg_count,
            healthy_ratio=healthy_ratio,
            prefixes=prefixes,
            priority_score=priority_score,
            total_files=total_files,
        )

    @classmethod
    def discover_donor_folders(
        cls,
        root: Path | str,
        max_depth: int = 3,
        target_prefixes: set[str] | None = None,
        archive_root: Path | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        timeout_per_folder: float = 30.0,
    ) -> list[DonorFolderCandidate]:
        """Recursively scan from `root` up to `max_depth` for donor folder candidates.

        Safety features:
          - Skips symlinks and junctions to prevent infinite loops.
          - Skips network shares to avoid network latency.
          - Skips system and temporary directories ($RECYCLE.BIN, etc.).
          - Enforces per-folder evaluation timeout.
          - Responsive to `is_cancelled` callback.
        """
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            return []

        if is_network_drive(root_path) or is_symlink_or_junction(root_path):
            return []

        candidates: list[DonorFolderCandidate] = []
        visited: set[str] = set()

        # Breadth-first or stack traversal bounded by max_depth
        # Queue elements: (folder_path, current_depth)
        queue: list[tuple[Path, int]] = [(root_path, 0)]

        while queue:
            if is_cancelled and is_cancelled():
                break

            current_dir, depth = queue.pop(0)
            canon_str = str(current_dir).lower()
            if canon_str in visited:
                continue
            visited.add(canon_str)

            # Evaluate current directory
            cand = cls.evaluate_folder(
                folder=current_dir,
                target_prefixes=target_prefixes,
                archive_root=archive_root,
                timeout=timeout_per_folder,
            )
            if cand and cand.jpeg_count > 0:
                candidates.append(cand)

            # If depth limit reached, do not descend into children
            if depth >= max_depth:
                continue

            # Queue subdirectories
            try:
                entries = os.scandir(current_dir)
            except (OSError, PermissionError):
                continue

            with entries:
                for entry in entries:
                    if is_cancelled and is_cancelled():
                        break

                    name_lower = entry.name.lower()
                    if name_lower in SKIP_DIR_NAMES or name_lower.startswith("."):
                        continue

                    try:
                        if entry.is_dir(follow_symlinks=False):
                            child_path = Path(entry.path)
                            if not is_symlink_or_junction(child_path):
                                queue.append((child_path, depth + 1))
                    except OSError:
                        continue

        # Sort descending by priority_score, then jpeg_count
        candidates.sort(key=lambda c: (-c.priority_score, -c.jpeg_count))
        return candidates

    @classmethod
    def discover_system_photo_folders(cls) -> list[Path]:
        """Discover standard system photo locations and mounted removable storage."""
        found_folders: list[Path] = []
        user_home = Path.home()

        # 1. Standard user photo folders
        candidates_to_check: list[Path] = [
            user_home / "Pictures",
            user_home / "OneDrive" / "Pictures",
            user_home / "Desktop",
            user_home / "Downloads",
            user_home / "Photos",
        ]

        # Windows USERPROFILE environment variable
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            up_path = Path(user_profile)
            candidates_to_check.extend([
                up_path / "Pictures",
                up_path / "OneDrive" / "Pictures",
                up_path / "Desktop",
            ])

        for p in candidates_to_check:
            try:
                if p.is_dir() and not is_network_drive(p):
                    res = p.resolve()
                    if res not in found_folders:
                        found_folders.append(res)
            except (OSError, PermissionError):
                continue

        # 2. Removable drives (SD cards, USB drives)
        removable_drives = enumerate_removable_drives()
        for drive in removable_drives:
            try:
                if not drive.is_dir():
                    continue

                # Standard DCF DCIM structure for camera SD cards
                dcim = drive / "DCIM"
                if dcim.is_dir() and dcim not in found_folders:
                    found_folders.append(dcim.resolve())

                # Also include the drive root if accessible
                if drive not in found_folders:
                    found_folders.append(drive.resolve())
            except (OSError, PermissionError):
                continue

        return found_folders
