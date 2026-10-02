#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Photo Healer CLI — Unified command-line interface for SSD TRIM photo forensics.

Provides five ergonomic commands:
  triage      - Fast streaming audit and classification of archive folders
  heal        - Single-photo header transplantation with donor JPEG
  batch-heal  - Batch recovery of photo series with auto-donor matching
  quarantine  - Safe relocation of 100% TRIM-erased zero files
  carve       - Extraction of embedded previews (MPF, EXIF thumbnails, raw streams)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

from photo_healer import __version__
from photo_healer.core.parser import JpegParser
from photo_healer.core.entropy import EntropyAnalyzer
from photo_healer.core.splicer import HeaderSplicer
from photo_healer.core.validator import JpegValidator
from photo_healer.core.carver import ThumbnailCarver, CarvedPreview


# ── Windows Console UTF-8 Reconfiguration ─────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Constants & Formats ───────────────────────────────────────────────────────
CHUNK_SIZE = 65536

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
DEFAULT_EXTS = set(IMAGE_MAGICS.keys())


# ── Format Helpers ────────────────────────────────────────────────────────────
def format_size(size_bytes: int | float) -> str:
    """Format byte size into human readable string."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size_bytes} B"


def render_table(title: str, headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """Render a clean, high-contrast Unicode summary table."""
    if not rows:
        return f"\n=== {title} (Empty) ===\n"

    num_cols = len(headers)
    if aligns is None:
        aligns = ["left"] + ["right"] * (num_cols - 1)

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(cell)))

    # Ensure total width accommodates title
    total_inner = sum(col_widths) + 3 * (num_cols - 1)
    if len(title) > total_inner:
        col_widths[0] += len(title) - total_inner

    lines: list[str] = []
    # Top border
    top_parts = ["─" * (w + 2) for w in col_widths]
    lines.append("┌" + "┬".join(top_parts) + "┐")

    # Title line
    full_inner_w = sum(col_widths) + 3 * (num_cols - 1) + 2
    title_padded = f" {title} ".center(full_inner_w)
    lines.append(f"│{title_padded}│")

    # Header separator
    sep_parts = ["─" * (w + 2) for w in col_widths]
    lines.append("├" + "┼".join(sep_parts) + "┤")

    # Header row
    hdr_cells = []
    for h, w, align in zip(headers, col_widths, aligns):
        cell_str = h.ljust(w) if align == "left" else h.rjust(w)
        hdr_cells.append(f" {cell_str} ")
    lines.append("│" + "│".join(hdr_cells) + "│")

    # Mid separator
    lines.append("├" + "┼".join(sep_parts) + "┤")

    # Data rows
    for row in rows:
        cells = []
        for cell, w, align in zip(row, col_widths, aligns):
            cell_str = str(cell)
            formatted = cell_str.ljust(w) if align == "left" else cell_str.rjust(w)
            cells.append(f" {formatted} ")
        lines.append("│" + "│".join(cells) + "│")

    # Bottom border
    lines.append("└" + "┴".join(sep_parts) + "┘")
    return "\n" + "\n".join(lines) + "\n"


# ── Progress Bar ──────────────────────────────────────────────────────────────
class ProgressBar:
    """Zero-dependency visual progress bar for terminal environments."""

    def __init__(self, total: int, prefix: str = "Processing", quiet: bool = False, bar_len: int = 24):
        self.total = max(0, total)
        self.prefix = prefix
        self.quiet = quiet
        self.bar_len = bar_len
        self.current = 0
        self._rendered = False

    def update(self, count: int = 1, item_name: str = "") -> None:
        if self.quiet:
            return
        self.current += count
        ratio = (self.current / self.total) if self.total > 0 else 1.0
        ratio = min(1.0, max(0.0, ratio))
        filled = int(self.bar_len * ratio)
        bar = "█" * filled + "░" * (self.bar_len - filled)
        pct = int(ratio * 100)

        # Truncate item name for stable display width
        max_item_len = 24
        short_name = (item_name[: max_item_len - 3] + "...") if len(item_name) > max_item_len else item_name
        text = f"\r{self.prefix} [{bar}] {pct:>3}% ({self.current}/{self.total}) {short_name:<{max_item_len}}"
        sys.stdout.write(text)
        sys.stdout.flush()
        self._rendered = True

    def finish(self) -> None:
        if self.quiet:
            return
        if self._rendered:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._rendered = False


# ── File Classifier ───────────────────────────────────────────────────────────
def classify_file(path: Path) -> dict[str, Any]:
    """Streaming O(1) RAM classifier for SSD TRIM-damaged image archives."""
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
        result["note"] = f"100% TRIM-erased zeros ({format_size(size)})"
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
                    result["note"] = f"Unexpected magic: {header[:8].hex(' ').upper()}"
            except OSError as e:
                result["note"] = f"Header read error: {e}"
        else:
            result["status"] = "other"
            result["note"] = f"Unknown magic for ext {ext}"
        return result

    # first_nz > 0: zero-filled prefix with live trailing data
    result["status"] = "healed_candidate"
    result["note"] = f"TRIM header zeroed ({first_nz} bytes), live stream starts at {first_nz}"
    return result


def find_donor_in_folder(folder: Path, exclude: Path | None = None) -> Path | None:
    """Find the first healthy donor JPEG in folder or its parent."""
    search_dirs = [folder]
    if folder.parent and folder.parent != folder:
        search_dirs.append(folder.parent)

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        try:
            for item in sorted(search_dir.iterdir()):
                if item.suffix.lower() not in {".jpg", ".jpeg"}:
                    continue
                if exclude and item.resolve() == exclude.resolve():
                    continue
                try:
                    with open(item, "rb") as fh:
                        magic = fh.read(3)
                    if magic == b"\xff\xd8\xff":
                        return item
                except OSError:
                    continue
        except OSError:
            continue
    return None


# ── Subcommand Handlers ───────────────────────────────────────────────────────
def handle_triage(args: argparse.Namespace) -> int:
    """Execute triage scan and audit."""
    root = Path(args.path)
    if not root.is_dir():
        sys.stderr.write(f"Error: Target path is not a directory: {root}\n")
        return 1

    exts: set[str] = DEFAULT_EXTS
    if args.ext:
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.ext}

    if not args.quiet:
        print(f"Scanning directory: {root.resolve()}")
        print(f"Extensions filter : {', '.join(sorted(exts))}")

    # Collect files
    file_list: list[Path] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() in exts:
                file_list.append(p)

    total_files = len(file_list)
    progress = ProgressBar(total_files, prefix="Auditing files", quiet=args.quiet)

    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}

    for file_path in file_list:
        record = classify_file(file_path)
        results.append(record)
        st = record["status"]
        counts[st] = counts.get(st, 0) + 1
        sizes[st] = sizes.get(st, 0) + record["size"]
        progress.update(1, file_path.name)

    progress.finish()

    # Handle quarantine if requested
    moved_count = 0
    moved_size = 0
    skipped_quarantine = 0

    if args.quarantine:
        q_dest = Path(args.quarantine)
        trim_zeros = [r for r in results if r["status"] == "trim_zero"]
        q_progress = ProgressBar(len(trim_zeros), prefix="Quarantining", quiet=args.quiet)

        if not args.dry_run:
            q_dest.mkdir(parents=True, exist_ok=True)

        for item in trim_zeros:
            src = Path(item["path"])
            if not src.exists():
                skipped_quarantine += 1
                q_progress.update(1, src.name)
                continue

            try:
                rel = src.relative_to(root)
            except ValueError:
                rel = Path(src.name)

            dst = q_dest / rel

            if dst.exists() and not args.force:
                skipped_quarantine += 1
                q_progress.update(1, src.name)
                continue

            if args.dry_run:
                moved_count += 1
                moved_size += item["size"]
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                    moved_count += 1
                    moved_size += item["size"]
                except OSError as e:
                    sys.stderr.write(f"Warning: Failed moving {src.name}: {e}\n")

            q_progress.update(1, src.name)

        q_progress.finish()

    # Print summary table
    if not args.quiet:
        table_rows = [
            ["Valid / Intact photos", str(counts.get("valid", 0)), format_size(sizes.get("valid", 0))],
            ["Heal candidates (live stream)", str(counts.get("healed_candidate", 0)), format_size(sizes.get("healed_candidate", 0))],
            ["TRIM zero (erased 0x00)", str(counts.get("trim_zero", 0)), format_size(sizes.get("trim_zero", 0))],
            ["Other formats / Unknown", str(counts.get("other", 0)), format_size(sizes.get("other", 0))],
            ["Empty files (0 bytes)", str(counts.get("empty", 0)), "0 B"],
            ["File access errors", str(counts.get("error", 0)), format_size(sizes.get("error", 0))],
        ]
        total_sz = sum(sizes.values())
        table_rows.append(["Total scanned files", str(total_files), format_size(total_sz)])

        if args.quarantine:
            action_desc = "Quarantined (dry-run)" if args.dry_run else "Quarantined (moved)"
            table_rows.append([action_desc, str(moved_count), format_size(moved_size)])
            table_rows.append(["Disk space freed", "-", format_size(moved_size)])

        print(render_table("PHOTO HEALER — TRIAGE REPORT", ["Category / Status", "Files", "Size"], table_rows))

    # Save JSON report if requested
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"Report saved: {report_path.resolve()}")

        # Also write heal candidates shortlist
        candidates = [r for r in results if r["status"] == "healed_candidate"]
        if candidates:
            cand_path = report_path.parent / "heal_candidates.json"
            with open(cand_path, "w", encoding="utf-8") as fh:
                json.dump(candidates, fh, ensure_ascii=False, indent=2)
            if not args.quiet:
                print(f"Heal candidates shortlist: {cand_path.resolve()} ({len(candidates)} items)")

    return 0


def handle_heal(args: argparse.Namespace) -> int:
    """Execute single photo header transplantation."""
    broken_path = Path(args.broken_file)
    donor_path = Path(args.donor)

    if not broken_path.is_file():
        sys.stderr.write(f"Error: Broken file does not exist: {broken_path}\n")
        return 1

    if not donor_path.is_file():
        sys.stderr.write(f"Error: Donor file does not exist: {donor_path}\n")
        return 1

    # Careful protection: determine destination and check overwrite
    if args.inplace:
        out_path = broken_path
        bak_path = broken_path.with_suffix(broken_path.suffix + ".bak")
        if bak_path.exists() and not args.force:
            sys.stderr.write(f"Error: Backup file already exists: {bak_path}. Use --force to overwrite.\n")
            return 1
    elif args.output:
        out_path = Path(args.output)
        if out_path.exists() and not args.force:
            sys.stderr.write(f"Error: Destination file already exists: {out_path}. Use --force to overwrite.\n")
            return 1
    else:
        out_path = broken_path.parent / f"{broken_path.stem}_HEALED{broken_path.suffix}"
        if out_path.exists() and not args.force:
            sys.stderr.write(f"Error: Destination file already exists: {out_path}. Use --force to overwrite.\n")
            return 1

    # Extract donor header
    try:
        parser = JpegParser(donor_path)
        donor_header = parser.get_header_bytes()
    except Exception as e:
        sys.stderr.write(f"Error: Failed to parse donor header from {donor_path.name}: {e}\n")
        return 1

    # Check broken target entropy stream
    try:
        broken_bytes = broken_path.read_bytes()
    except OSError as e:
        sys.stderr.write(f"Error reading broken file: {e}\n")
        return 1

    detected_offset = EntropyAnalyzer.detect_entropy_start(broken_bytes)
    if detected_offset is None:
        rec = classify_file(broken_path)
        if rec["first_nonzero"] > 0:
            detected_offset = rec["first_nonzero"]
        else:
            sys.stderr.write(f"Error: No live entropy data found in {broken_path.name} (file is 100% TRIM-zero or corrupted).\n")
            return 1

    # Splice donor header with live bitstream
    try:
        splicer = HeaderSplicer(donor_header)
        splice_res = splicer.splice_target(broken_bytes, entropy_offset=detected_offset)
    except Exception as e:
        sys.stderr.write(f"Error splicing donor header: {e}\n")
        return 1

    # Validate reconstructed image
    val = JpegValidator.validate(splice_res.data)

    if args.dry_run:
        if not args.quiet:
            print(f"[DRY-RUN] Would heal: {broken_path.name} -> {out_path.name}")
            print(f"  Donor header  : {len(donor_header)} bytes")
            print(f"  Entropy start : offset {splice_res.entropy_offset}")
            print(f"  Total size    : {format_size(splice_res.total_bytes)}")
            print(f"  Valid JPEG    : {'Yes' if val.is_valid else 'No (warnings: ' + ', '.join(val.errors) + ')'}")
        return 0

    # Write output
    try:
        if args.inplace:
            shutil.copy2(str(broken_path), str(bak_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(splice_res.data)
    except OSError as e:
        sys.stderr.write(f"Error writing output file: {e}\n")
        return 1

    if not args.quiet:
        dim = f"{val.width}x{val.height}" if val.width and val.height else "unknown resolution"
        print(f"[HEALED] {broken_path.name} -> {out_path.name}")
        print(f"  Geometry   : {dim}")
        print(f"  Total size : {format_size(splice_res.total_bytes)}")
        if args.inplace:
            print(f"  Backup     : {bak_path.name}")

    return 0


def handle_batch_heal(args: argparse.Namespace) -> int:
    """Execute batch healing for photo folders."""
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.stderr.write(f"Error: Folder does not exist: {folder}\n")
        return 1

    forced_donor = Path(args.donor) if args.donor else None
    if forced_donor and not forced_donor.is_file():
        sys.stderr.write(f"Error: Specified donor file does not exist: {forced_donor}\n")
        return 1

    if not args.quiet:
        print(f"Scanning for heal candidates in: {folder.resolve()}")

    # Find candidates
    candidates: list[Path] = []
    for dirpath, _, filenames in os.walk(folder):
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() in {".jpg", ".jpeg"}:
                rec = classify_file(p)
                if rec["status"] == "healed_candidate":
                    candidates.append(p)

    if not candidates:
        if not args.quiet:
            print(f"No damaged photo candidates found in {folder.resolve()}.")
        return 0

    if not args.quiet:
        print(f"Found {len(candidates)} heal candidates. Starting batch restoration...")

    donor_header_cache: dict[Path, bytes] = {}

    def get_donor_header(d_path: Path) -> bytes | None:
        if d_path in donor_header_cache:
            return donor_header_cache[d_path]
        try:
            h = JpegParser(d_path).get_header_bytes()
            donor_header_cache[d_path] = h
            return h
        except Exception:
            return None

    progress = ProgressBar(len(candidates), prefix="Batch healing", quiet=args.quiet)

    healed_count = 0
    skipped_count = 0
    error_count = 0
    repaired_bytes = 0

    for cand in candidates:
        # Determine donor
        if forced_donor:
            donor = forced_donor
        else:
            donor = find_donor_in_folder(cand.parent, exclude=cand)
            if donor is None:
                skipped_count += 1
                progress.update(1, cand.name)
                continue

        donor_header = get_donor_header(donor)
        if donor_header is None:
            skipped_count += 1
            progress.update(1, cand.name)
            continue

        # Determine destination
        if args.inplace:
            out_path = cand
            bak_path = cand.with_suffix(cand.suffix + ".bak")
            if bak_path.exists() and not args.force:
                skipped_count += 1
                progress.update(1, cand.name)
                continue
        elif args.output:
            out_dir = Path(args.output)
            try:
                rel = cand.relative_to(folder)
            except ValueError:
                rel = Path(cand.name)
            out_path = out_dir / rel.parent / f"{cand.stem}_HEALED{cand.suffix}"
            if out_path.exists() and not args.force:
                skipped_count += 1
                progress.update(1, cand.name)
                continue
        else:
            out_path = cand.parent / f"{cand.stem}_HEALED{cand.suffix}"
            if out_path.exists() and not args.force:
                skipped_count += 1
                progress.update(1, cand.name)
                continue

        # Splice
        try:
            cand_bytes = cand.read_bytes()
            detected_offset = EntropyAnalyzer.detect_entropy_start(cand_bytes)
            if detected_offset is None:
                rec = classify_file(cand)
                if rec["first_nonzero"] > 0:
                    detected_offset = rec["first_nonzero"]
                else:
                    skipped_count += 1
                    progress.update(1, cand.name)
                    continue

            splicer = HeaderSplicer(donor_header)
            splice_res = splicer.splice_target(cand_bytes, entropy_offset=detected_offset)

            if not args.dry_run:
                if args.inplace:
                    shutil.copy2(str(cand), str(bak_path))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(splice_res.data)

            healed_count += 1
            repaired_bytes += splice_res.total_bytes
        except Exception:
            error_count += 1

        progress.update(1, cand.name)

    progress.finish()

    # Summary table
    if not args.quiet:
        action_name = "Healed (dry-run)" if args.dry_run else "Healed successfully"
        table_rows = [
            ["Total candidates", str(len(candidates)), "-"],
            [action_name, str(healed_count), format_size(repaired_bytes)],
            ["Skipped (no donor / file exists)", str(skipped_count), "-"],
            ["Errors", str(error_count), "-"],
            ["Total data restored", "-", format_size(repaired_bytes)],
        ]
        print(render_table("PHOTO HEALER — BATCH HEAL SUMMARY", ["Metric", "Count", "Size"], table_rows))

    return 0


def handle_quarantine(args: argparse.Namespace) -> int:
    """Execute safe quarantine relocation from report."""
    report_file = Path(args.report)
    if not report_file.is_file():
        sys.stderr.write(f"Error: Report file not found: {report_file}\n")
        return 1

    dest_root = Path(args.dest)
    try:
        with open(report_file, "r", encoding="utf-8") as fh:
            records: list[dict[str, Any]] = json.load(fh)
    except Exception as e:
        sys.stderr.write(f"Error reading JSON report: {e}\n")
        return 1

    trim_zeros = [r for r in records if r.get("status") == "trim_zero"]
    total_items = len(trim_zeros)

    if total_items == 0:
        if not args.quiet:
            print("No TRIM-zero files found in report.")
        return 0

    if not args.quiet:
        total_size = sum(r.get("size", 0) for r in trim_zeros)
        print(f"Found {total_items} TRIM-zero files ({format_size(total_size)}) to quarantine.")

    # Determine common prefix root
    all_paths = [Path(r["path"]) for r in trim_zeros if "path" in r]
    if all_paths:
        try:
            common_root = Path(os.path.commonpath([str(p.parent) for p in all_paths]))
        except ValueError:
            common_root = Path(all_paths[0].anchor)
    else:
        common_root = Path(".")

    progress = ProgressBar(total_items, prefix="Quarantining", quiet=args.quiet)

    moved_count = 0
    skipped_count = 0
    error_count = 0
    freed_bytes = 0

    if not args.dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)

    for item in trim_zeros:
        src = Path(item["path"])
        size = item.get("size", 0)

        if not src.exists():
            skipped_count += 1
            progress.update(1, src.name)
            continue

        try:
            rel = src.relative_to(common_root)
        except ValueError:
            rel = Path(src.name)

        dst = dest_root / rel

        if dst.exists() and not args.force:
            skipped_count += 1
            progress.update(1, src.name)
            continue

        if args.dry_run:
            moved_count += 1
            freed_bytes += size
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved_count += 1
                freed_bytes += size
            except OSError as e:
                sys.stderr.write(f"Warning: Failed to move {src.name}: {e}\n")
                error_count += 1

        progress.update(1, src.name)

    progress.finish()

    # Summary table
    if not args.quiet:
        action_name = "Relocated (dry-run)" if args.dry_run else "Relocated to quarantine"
        table_rows = [
            ["Report items", str(total_items), "-"],
            [action_name, str(moved_count), format_size(freed_bytes)],
            ["Skipped (missing / exists)", str(skipped_count), "-"],
            ["Errors", str(error_count), "-"],
            ["Disk space freed", "-", format_size(freed_bytes)],
        ]
        print(render_table("PHOTO HEALER — QUARANTINE SUMMARY", ["Metric", "Count", "Size"], table_rows))

    return 0


def handle_carve(args: argparse.Namespace) -> int:
    """Execute embedded preview and thumbnail carving."""
    target_path = Path(args.path)
    if not target_path.exists():
        sys.stderr.write(f"Error: Target path does not exist: {target_path}\n")
        return 1

    # Single file carve
    if target_path.is_file():
        preview: CarvedPreview | None = ThumbnailCarver.extract_best_preview(target_path)
        if preview is None:
            if not args.quiet:
                print(f"No embedded preview or thumbnail found in: {target_path.name}")
            return 0

        out_dir = Path(args.dest) if args.dest else target_path.parent / "_Previews"
        out_file = out_dir / f"{target_path.stem}_{preview.preview_type}.jpg"

        if out_file.exists() and not args.force:
            sys.stderr.write(f"Error: Output file already exists: {out_file}. Use --force to overwrite.\n")
            return 1

        dim_str = f"{preview.width}x{preview.height}" if preview.width and preview.height else "unknown"

        if args.dry_run:
            if not args.quiet:
                print(f"[DRY-RUN] Would carve preview from {target_path.name}:")
                print(f"  Type       : {preview.preview_type}")
                print(f"  Resolution : {dim_str}")
                print(f"  Size       : {format_size(preview.size)}")
                print(f"  Destination: {out_file.name}")
            return 0

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(preview.data)
        except OSError as e:
            sys.stderr.write(f"Error writing carved preview: {e}\n")
            return 1

        if not args.quiet:
            print(f"[CARVED] {target_path.name} -> {out_file.name}")
            print(f"  Type       : {preview.preview_type}")
            print(f"  Resolution : {dim_str}")
            print(f"  Size       : {format_size(preview.size)}")
        return 0

    # Directory carve
    file_list: list[Path] = []
    for dirpath, _, filenames in os.walk(target_path):
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() in DEFAULT_EXTS:
                file_list.append(p)

    total_files = len(file_list)
    if total_files == 0:
        if not args.quiet:
            print(f"No image files found in {target_path.resolve()}.")
        return 0

    if not args.quiet:
        print(f"Scanning {total_files} files for embedded previews...")

    progress = ProgressBar(total_files, prefix="Carving previews", quiet=args.quiet)

    carved_count = 0
    skipped_count = 0
    total_saved_bytes = 0
    type_counts: dict[str, int] = {}

    default_dest_root = Path(args.dest) if args.dest else None

    for f in file_list:
        try:
            preview = ThumbnailCarver.extract_best_preview(f)
            if preview is None:
                skipped_count += 1
                progress.update(1, f.name)
                continue

            if default_dest_root:
                out_dir = default_dest_root
            else:
                out_dir = f.parent / "_Previews"

            out_file = out_dir / f"{f.stem}_{preview.preview_type}.jpg"

            if out_file.exists() and not args.force:
                skipped_count += 1
                progress.update(1, f.name)
                continue

            if not args.dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file.write_bytes(preview.data)

            carved_count += 1
            total_saved_bytes += preview.size
            t = preview.preview_type
            type_counts[t] = type_counts.get(t, 0) + 1
        except Exception:
            skipped_count += 1

        progress.update(1, f.name)

    progress.finish()

    # Summary table
    if not args.quiet:
        action_name = "Carved (dry-run)" if args.dry_run else "Carved previews"
        table_rows = [
            ["Scanned files", str(total_files), "-"],
            [action_name, str(carved_count), format_size(total_saved_bytes)],
            ["  - MPF Full HD previews", str(type_counts.get("mpf", 0)), "-"],
            ["  - EXIF thumbnails", str(type_counts.get("exif_thumb", 0)), "-"],
            ["  - Raw stream carved", str(type_counts.get("raw_carved", 0)), "-"],
            ["Skipped (no preview / exists)", str(skipped_count), "-"],
            ["Total extracted volume", "-", format_size(total_saved_bytes)],
        ]
        print(render_table("PHOTO HEALER — CARVER SUMMARY", ["Metric", "Count", "Size"], table_rows))

    return 0


# ── CLI Parser Setup & Main ───────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser and subcommands."""
    parser = argparse.ArgumentParser(
        prog="photo-healer",
        description="Photo Healer — Forensic repair tool for SSD TRIM-damaged photo archives.",
        epilog="Use 'photo-healer <command> --help' for details on each subcommand.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # 1. triage
    p_triage = subparsers.add_parser(
        "triage",
        help="Scan and audit directory for TRIM damage and candidates",
        description="High-speed streaming triage to audit image archives and classify TRIM damage.",
    )
    p_triage.add_argument("path", help="Directory path to scan and audit")
    p_triage.add_argument("--quarantine", metavar="DIR", help="Move TRIM-zero files to quarantine directory")
    p_triage.add_argument("--report", metavar="FILE", help="Save triage report to specified JSON file")
    p_triage.add_argument("--ext", nargs="+", metavar="EXT", help="File extensions to include (default: all image types)")
    p_triage.add_argument("--dry-run", action="store_true", help="Simulate quarantine without moving files")
    p_triage.add_argument("--force", action="store_true", help="Overwrite existing files in quarantine destination")
    p_triage.add_argument("--quiet", action="store_true", help="Suppress progress bars and summary tables")

    # 2. heal
    p_heal = subparsers.add_parser(
        "heal",
        help="Heal a single photo using a donor JPEG header",
        description="Transplant donor JPEG markers (DQT, DHT, SOF, SOS) onto damaged file.",
    )
    p_heal.add_argument("broken_file", help="Path to damaged JPEG image")
    p_heal.add_argument("--donor", required=True, help="Path to healthy donor JPEG image")
    p_heal.add_argument("--output", metavar="DEST", help="Output file path (default: <name>_HEALED.jpg)")
    p_heal.add_argument("--inplace", action="store_true", help="Replace original file in place (creates .bak backup)")
    p_heal.add_argument("--force", action="store_true", help="Force overwrite existing destination or backup files")
    p_heal.add_argument("--dry-run", action="store_true", help="Simulate healing without writing to disk")
    p_heal.add_argument("--quiet", action="store_true", help="Suppress progress and informational logs")

    # 3. batch-heal
    p_batch = subparsers.add_parser(
        "batch-heal",
        help="Batch recovery of photo series with auto-donor matching",
        description="Scan directory for all TRIM-damaged candidates and repair using donor headers.",
    )
    p_batch.add_argument("folder", help="Folder containing damaged photos")
    p_batch.add_argument("--donor", help="Explicit donor file to use for all candidates")
    p_batch.add_argument("--auto-donor", action="store_true", help="Automatically search for healthy donor in folder")
    p_batch.add_argument("--output", metavar="DIR", help="Output directory for healed photos")
    p_batch.add_argument("--inplace", action="store_true", help="Replace original files in place (creates .bak backups)")
    p_batch.add_argument("--force", action="store_true", help="Force overwrite existing healed files or backups")
    p_batch.add_argument("--dry-run", action="store_true", help="Simulate batch healing without writing files")
    p_batch.add_argument("--quiet", action="store_true", help="Suppress progress bars and summary output")

    # 4. quarantine
    p_quar = subparsers.add_parser(
        "quarantine",
        help="Safely move TRIM-zero unrecoverable files to quarantine",
        description="Relocate TRIM-erased 0x00 files recorded in triage report to clean the archive.",
    )
    p_quar.add_argument("--report", required=True, metavar="FILE", help="JSON report path generated by triage")
    p_quar.add_argument("--dest", required=True, metavar="DIR", help="Quarantine destination directory")
    p_quar.add_argument("--dry-run", action="store_true", help="Simulate moves without moving files")
    p_quar.add_argument("--force", action="store_true", help="Force overwrite if destination already exists")
    p_quar.add_argument("--quiet", action="store_true", help="Suppress progress and summary output")

    # 5. carve
    p_carve = subparsers.add_parser(
        "carve",
        help="Extract embedded previews (MPF, EXIF thumbnails, raw streams)",
        description="Carve embedded JPEG thumbnails, Full HD MPF previews, or raw image streams.",
    )
    p_carve.add_argument("path", help="File or folder path to carve previews from")
    p_carve.add_argument("--dest", metavar="DIR", help="Output directory for carved previews (default: _Previews)")
    p_carve.add_argument("--force", action="store_true", help="Force overwrite existing carved previews")
    p_carve.add_argument("--dry-run", action="store_true", help="Simulate extraction without writing files")
    p_carve.add_argument("--quiet", action="store_true", help="Suppress progress and summary output")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()

    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help()
        return 1

    args = parser.parse_args(argv)

    if args.command == "triage":
        return handle_triage(args)
    elif args.command == "heal":
        return handle_heal(args)
    elif args.command == "batch-heal":
        return handle_batch_heal(args)
    elif args.command == "quarantine":
        return handle_quarantine(args)
    elif args.command == "carve":
        return handle_carve(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
