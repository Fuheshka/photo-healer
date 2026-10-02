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
from photo_healer.cli.banner import show_banner
from photo_healer.cli.i18n import (
    SUPPORTED_LANGUAGES,
    ensure_windows_utf8,
    get_language,
    set_language,
    t,
)

# ── Windows Console UTF-8 Reconfiguration ─────────────────────────────────────
ensure_windows_utf8()


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
        result["note"] = t("classify.note.stat_error", error=e, default=f"Stat error: {e}")
        return result

    result["size"] = size
    if size == 0:
        result["status"] = "empty"
        result["note"] = t("classify.note.zero_length", default="Zero-length file")
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
        result["note"] = t("classify.note.read_error", error=e, default=f"Read error: {e}")
        return result

    result["first_nonzero"] = first_nz

    if first_nz == -1:
        result["status"] = "trim_zero"
        result["note"] = t("classify.note.trim_zero", size=format_size(size), default=f"100% TRIM-erased zeros ({format_size(size)})")
        return result

    if first_nz == 0:
        if expected_magic:
            try:
                with open(path, "rb") as fh:
                    header = fh.read(len(expected_magic))
                if header == expected_magic:
                    result["status"] = "valid"
                    result["note"] = t("classify.note.intact_magic", default="Intact file magic")
                else:
                    result["status"] = "other"
                    hex_str = header[:8].hex(" ").upper()
                    result["note"] = t("classify.note.unexpected_magic", magic=hex_str, default=f"Unexpected magic: {hex_str}")
            except OSError as e:
                result["note"] = t("classify.note.header_read_error", error=e, default=f"Header read error: {e}")
        else:
            result["status"] = "other"
            result["note"] = t("classify.note.unknown_magic", ext=ext, default=f"Unknown magic for ext {ext}")
        return result

    # first_nz > 0: zero-filled prefix with live trailing data
    result["status"] = "healed_candidate"
    result["note"] = t("classify.note.trim_candidate", count=first_nz, offset=first_nz, default=f"TRIM header zeroed ({first_nz} bytes), live stream starts at {first_nz}")
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
        sys.stderr.write(t("triage.error.not_a_directory", path=root, default=f"Error: Target path is not a directory: {root}") + "\n")
        return 1

    exts: set[str] = DEFAULT_EXTS
    if args.ext:
        exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.ext}

    if not args.quiet:
        print(t("triage.info.scanning", path=root.resolve(), default=f"Scanning directory: {root.resolve()}"))
        print(t("triage.info.ext_filter", exts=', '.join(sorted(exts)), default=f"Extensions filter : {', '.join(sorted(exts))}"))

    # Collect files
    file_list: list[Path] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() in exts:
                file_list.append(p)

    total_files = len(file_list)
    progress = ProgressBar(total_files, prefix=t("progress.auditing_files", default="Auditing files"), quiet=args.quiet)

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
        q_progress = ProgressBar(len(trim_zeros), prefix=t("progress.quarantining", default="Quarantining"), quiet=args.quiet)

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
                    sys.stderr.write(t("triage.warn.failed_move", name=src.name, error=e, default=f"Warning: Failed moving {src.name}: {e}") + "\n")

            q_progress.update(1, src.name)

        q_progress.finish()

    # Print summary table
    if not args.quiet:
        table_rows = [
            [t("status.valid", default="Valid / Intact photos"), str(counts.get("valid", 0)), format_size(sizes.get("valid", 0))],
            [t("status.healed_candidate", default="Heal candidates (live stream)"), str(counts.get("healed_candidate", 0)), format_size(sizes.get("healed_candidate", 0))],
            [t("status.trim_zero", default="TRIM zero (erased 0x00)"), str(counts.get("trim_zero", 0)), format_size(sizes.get("trim_zero", 0))],
            [t("status.other", default="Other formats / Unknown"), str(counts.get("other", 0)), format_size(sizes.get("other", 0))],
            [t("status.empty", default="Empty files (0 bytes)"), str(counts.get("empty", 0)), "0 B"],
            [t("status.error", default="File access errors"), str(counts.get("error", 0)), format_size(sizes.get("error", 0))],
        ]
        total_sz = sum(sizes.values())
        table_rows.append([t("metric.total_scanned", default="Total scanned files"), str(total_files), format_size(total_sz)])

        if args.quarantine:
            action_desc = t("metric.quarantined_dry_run", default="Quarantined (dry-run)") if args.dry_run else t("metric.quarantined_moved", default="Quarantined (moved)")
            table_rows.append([action_desc, str(moved_count), format_size(moved_size)])
            table_rows.append([t("metric.disk_freed", default="Disk space freed"), "-", format_size(moved_size)])

        headers = [t("table.header.category_status", default="Category / Status"), t("table.header.files", default="Files"), t("table.header.size", default="Size")]
        print(render_table(t("table.title.triage", default="PHOTO HEALER — TRIAGE REPORT"), headers, table_rows))

    # Save JSON report if requested
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(t("triage.info.report_saved", path=report_path.resolve(), default=f"Report saved: {report_path.resolve()}"))

        # Also write heal candidates shortlist
        candidates = [r for r in results if r["status"] == "healed_candidate"]
        if candidates:
            cand_path = report_path.parent / "heal_candidates.json"
            with open(cand_path, "w", encoding="utf-8") as fh:
                json.dump(candidates, fh, ensure_ascii=False, indent=2)
            if not args.quiet:
                print(t("triage.info.candidates_shortlist", path=cand_path.resolve(), count=len(candidates), default=f"Heal candidates shortlist: {cand_path.resolve()} ({len(candidates)} items)"))

    return 0


def handle_heal(args: argparse.Namespace) -> int:
    """Execute single photo header transplantation."""
    broken_path = Path(args.broken_file)
    donor_path = Path(args.donor)

    if not broken_path.is_file():
        sys.stderr.write(t("heal.error.broken_not_found", path=broken_path, default=f"Error: Broken file does not exist: {broken_path}") + "\n")
        return 1

    if not donor_path.is_file():
        sys.stderr.write(t("heal.error.donor_not_found", path=donor_path, default=f"Error: Donor file does not exist: {donor_path}") + "\n")
        return 1

    # Careful protection: determine destination and check overwrite
    if args.inplace:
        out_path = broken_path
        bak_path = broken_path.with_suffix(broken_path.suffix + ".bak")
        if bak_path.exists() and not args.force:
            sys.stderr.write(t("heal.error.backup_exists", path=bak_path, default=f"Error: Backup file already exists: {bak_path}. Use --force to overwrite.") + "\n")
            return 1
    elif args.output:
        out_path = Path(args.output)
        if out_path.exists() and not args.force:
            sys.stderr.write(t("heal.error.destination_exists", path=out_path, default=f"Error: Destination file already exists: {out_path}. Use --force to overwrite.") + "\n")
            return 1
    else:
        out_path = broken_path.parent / f"{broken_path.stem}_HEALED{broken_path.suffix}"
        if out_path.exists() and not args.force:
            sys.stderr.write(t("heal.error.destination_exists", path=out_path, default=f"Error: Destination file already exists: {out_path}. Use --force to overwrite.") + "\n")
            return 1

    # Extract donor header
    try:
        parser = JpegParser(donor_path)
        donor_header = parser.get_header_bytes()
    except Exception as e:
        sys.stderr.write(t("heal.error.donor_parse_failed", name=donor_path.name, error=e, default=f"Error: Failed to parse donor header from {donor_path.name}: {e}") + "\n")
        return 1

    # Check broken target entropy stream
    try:
        broken_bytes = broken_path.read_bytes()
    except OSError as e:
        sys.stderr.write(t("heal.error.broken_read_failed", error=e, default=f"Error reading broken file: {e}") + "\n")
        return 1

    detected_offset = EntropyAnalyzer.detect_entropy_start(broken_bytes)
    if detected_offset is None:
        rec = classify_file(broken_path)
        if rec["first_nonzero"] > 0:
            detected_offset = rec["first_nonzero"]
        else:
            sys.stderr.write(t("heal.error.no_live_entropy", name=broken_path.name, default=f"Error: No live entropy data found in {broken_path.name} (file is 100% TRIM-zero or corrupted).") + "\n")
            return 1

    # Splice donor header with live bitstream
    try:
        splicer = HeaderSplicer(donor_header)
        splice_res = splicer.splice_target(broken_bytes, entropy_offset=detected_offset)
    except Exception as e:
        sys.stderr.write(t("heal.error.splice_failed", error=e, default=f"Error splicing donor header: {e}") + "\n")
        return 1

    # Validate reconstructed image
    val = JpegValidator.validate(splice_res.data)

    if args.dry_run:
        if not args.quiet:
            print(t("heal.dry_run.header", src=broken_path.name, dst=out_path.name, default=f"[DRY-RUN] Would heal: {broken_path.name} -> {out_path.name}"))
            print(t("heal.dry_run.donor_header", size=len(donor_header), default=f"  Donor header  : {len(donor_header)} bytes"))
            print(t("heal.dry_run.entropy_start", offset=splice_res.entropy_offset, default=f"  Entropy start : offset {splice_res.entropy_offset}"))
            print(t("heal.dry_run.total_size", size=format_size(splice_res.total_bytes), default=f"  Total size    : {format_size(splice_res.total_bytes)}"))
            if val.is_valid:
                print(t("heal.dry_run.valid_jpeg_yes", default="  Valid JPEG    : Yes"))
            else:
                warn_str = ", ".join(val.errors)
                print(t("heal.dry_run.valid_jpeg_no", warnings=warn_str, default=f"  Valid JPEG    : No (warnings: {warn_str})"))
        return 0

    # Write output
    try:
        if args.inplace:
            shutil.copy2(str(broken_path), str(bak_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(splice_res.data)
    except OSError as e:
        sys.stderr.write(t("heal.error.write_failed", error=e, default=f"Error writing output file: {e}") + "\n")
        return 1

    if not args.quiet:
        dim = f"{val.width}x{val.height}" if val.width and val.height else t("heal.val.unknown_resolution", default="unknown resolution")
        print(t("heal.success.header", src=broken_path.name, dst=out_path.name, default=f"[HEALED] {broken_path.name} -> {out_path.name}"))
        print(t("heal.success.geometry", geometry=dim, default=f"  Geometry   : {dim}"))
        print(t("heal.success.total_size", size=format_size(splice_res.total_bytes), default=f"  Total size : {format_size(splice_res.total_bytes)}"))
        if args.inplace:
            print(t("heal.success.backup", name=bak_path.name, default=f"  Backup     : {bak_path.name}"))

    return 0


def handle_batch_heal(args: argparse.Namespace) -> int:
    """Execute batch healing for photo folders."""
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.stderr.write(t("batch_heal.error.folder_not_found", folder=folder, default=f"Error: Folder does not exist: {folder}") + "\n")
        return 1

    forced_donor = Path(args.donor) if args.donor else None
    if forced_donor and not forced_donor.is_file():
        sys.stderr.write(t("batch_heal.error.donor_not_found", path=forced_donor, default=f"Error: Specified donor file does not exist: {forced_donor}") + "\n")
        return 1

    if not args.quiet:
        print(t("batch_heal.info.scanning", folder=folder.resolve(), default=f"Scanning for heal candidates in: {folder.resolve()}"))

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
            print(t("batch_heal.info.no_candidates", folder=folder.resolve(), default=f"No damaged photo candidates found in {folder.resolve()}."))
        return 0

    if not args.quiet:
        print(t("batch_heal.info.found_candidates", count=len(candidates), default=f"Found {len(candidates)} heal candidates. Starting batch restoration..."))

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

    progress = ProgressBar(len(candidates), prefix=t("progress.batch_healing", default="Batch healing"), quiet=args.quiet)

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
        action_name = t("metric.healed_dry_run", default="Healed (dry-run)") if args.dry_run else t("metric.healed_success", default="Healed successfully")
        table_rows = [
            [t("metric.total_candidates", default="Total candidates"), str(len(candidates)), "-"],
            [action_name, str(healed_count), format_size(repaired_bytes)],
            [t("metric.skipped_no_donor_or_exists", default="Skipped (no donor / file exists)"), str(skipped_count), "-"],
            [t("metric.errors", default="Errors"), str(error_count), "-"],
            [t("metric.total_restored", default="Total data restored"), "-", format_size(repaired_bytes)],
        ]
        headers = [t("table.header.metric", default="Metric"), t("table.header.count", default="Count"), t("table.header.size", default="Size")]
        print(render_table(t("table.title.batch_heal", default="PHOTO HEALER — BATCH HEAL SUMMARY"), headers, table_rows))

    return 0


def handle_quarantine(args: argparse.Namespace) -> int:
    """Execute safe quarantine relocation from report."""
    report_file = Path(args.report)
    if not report_file.is_file():
        sys.stderr.write(t("quarantine.error.report_not_found", path=report_file, default=f"Error: Report file not found: {report_file}") + "\n")
        return 1

    dest_root = Path(args.dest)
    try:
        with open(report_file, "r", encoding="utf-8") as fh:
            records: list[dict[str, Any]] = json.load(fh)
    except Exception as e:
        sys.stderr.write(t("quarantine.error.report_read_failed", error=e, default=f"Error reading JSON report: {e}") + "\n")
        return 1

    trim_zeros = [r for r in records if r.get("status") == "trim_zero"]
    total_items = len(trim_zeros)

    if total_items == 0:
        if not args.quiet:
            print(t("quarantine.info.no_trim_zeros", default="No TRIM-zero files found in report."))
        return 0

    if not args.quiet:
        total_size = sum(r.get("size", 0) for r in trim_zeros)
        print(t("quarantine.info.found_trim_zeros", count=total_items, size=format_size(total_size), default=f"Found {total_items} TRIM-zero files ({format_size(total_size)}) to quarantine."))

    # Determine common prefix root
    all_paths = [Path(r["path"]) for r in trim_zeros if "path" in r]
    if all_paths:
        try:
            common_root = Path(os.path.commonpath([str(p.parent) for p in all_paths]))
        except ValueError:
            common_root = Path(all_paths[0].anchor)
    else:
        common_root = Path(".")

    progress = ProgressBar(total_items, prefix=t("progress.quarantining", default="Quarantining"), quiet=args.quiet)

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
                sys.stderr.write(t("quarantine.warn.failed_move", name=src.name, error=e, default=f"Warning: Failed to move {src.name}: {e}") + "\n")
                error_count += 1

        progress.update(1, src.name)

    progress.finish()

    # Summary table
    if not args.quiet:
        action_name = t("metric.relocated_dry_run", default="Relocated (dry-run)") if args.dry_run else t("metric.relocated_moved", default="Relocated to quarantine")
        table_rows = [
            [t("metric.report_items", default="Report items"), str(total_items), "-"],
            [action_name, str(moved_count), format_size(freed_bytes)],
            [t("metric.skipped_missing_or_exists", default="Skipped (missing / exists)"), str(skipped_count), "-"],
            [t("metric.errors", default="Errors"), str(error_count), "-"],
            [t("metric.disk_freed", default="Disk space freed"), "-", format_size(freed_bytes)],
        ]
        headers = [t("table.header.metric", default="Metric"), t("table.header.count", default="Count"), t("table.header.size", default="Size")]
        print(render_table(t("table.title.quarantine", default="PHOTO HEALER — QUARANTINE SUMMARY"), headers, table_rows))

    return 0


def handle_carve(args: argparse.Namespace) -> int:
    """Execute embedded preview and thumbnail carving."""
    target_path = Path(args.path)
    if not target_path.exists():
        sys.stderr.write(t("carve.error.path_not_found", path=target_path, default=f"Error: Target path does not exist: {target_path}") + "\n")
        return 1

    # Single file carve
    if target_path.is_file():
        preview: CarvedPreview | None = ThumbnailCarver.extract_best_preview(target_path)
        if preview is None:
            if not args.quiet:
                print(t("carve.info.no_preview", name=target_path.name, default=f"No embedded preview or thumbnail found in: {target_path.name}"))
            return 0

        out_dir = Path(args.dest) if args.dest else target_path.parent / "_Previews"
        out_file = out_dir / f"{target_path.stem}_{preview.preview_type}.jpg"

        if out_file.exists() and not args.force:
            sys.stderr.write(t("carve.error.output_exists", path=out_file, default=f"Error: Output file already exists: {out_file}. Use --force to overwrite.") + "\n")
            return 1

        dim_str = f"{preview.width}x{preview.height}" if preview.width and preview.height else "unknown"

        if args.dry_run:
            if not args.quiet:
                print(t("carve.dry_run.header", name=target_path.name, default=f"[DRY-RUN] Would carve preview from {target_path.name}:"))
                print(t("carve.dry_run.type", type=preview.preview_type, default=f"  Type       : {preview.preview_type}"))
                print(t("carve.dry_run.resolution", resolution=dim_str, default=f"  Resolution : {dim_str}"))
                print(t("carve.dry_run.size", size=format_size(preview.size), default=f"  Size       : {format_size(preview.size)}"))
                print(t("carve.dry_run.destination", name=out_file.name, default=f"  Destination: {out_file.name}"))
            return 0

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(preview.data)
        except OSError as e:
            sys.stderr.write(t("carve.error.write_failed", error=e, default=f"Error writing carved preview: {e}") + "\n")
            return 1

        if not args.quiet:
            print(t("carve.success.header", src=target_path.name, dst=out_file.name, default=f"[CARVED] {target_path.name} -> {out_file.name}"))
            print(t("carve.dry_run.type", type=preview.preview_type, default=f"  Type       : {preview.preview_type}"))
            print(t("carve.dry_run.resolution", resolution=dim_str, default=f"  Resolution : {dim_str}"))
            print(t("carve.dry_run.size", size=format_size(preview.size), default=f"  Size       : {format_size(preview.size)}"))
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
            print(t("carve.info.no_images", path=target_path.resolve(), default=f"No image files found in {target_path.resolve()}."))
        return 0

    if not args.quiet:
        print(t("carve.info.scanning", count=total_files, default=f"Scanning {total_files} files for embedded previews..."))

    progress = ProgressBar(total_files, prefix=t("progress.carving_previews", default="Carving previews"), quiet=args.quiet)

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
            t_type = preview.preview_type
            type_counts[t_type] = type_counts.get(t_type, 0) + 1
        except Exception:
            skipped_count += 1

        progress.update(1, f.name)

    progress.finish()

    # Summary table
    if not args.quiet:
        action_name = t("metric.carved_dry_run", default="Carved (dry-run)") if args.dry_run else t("metric.carved_success", default="Carved previews")
        table_rows = [
            [t("metric.total_scanned", default="Scanned files"), str(total_files), "-"],
            [action_name, str(carved_count), format_size(total_saved_bytes)],
            [t("metric.mpf_previews", default="  - MPF Full HD previews"), str(type_counts.get("mpf", 0)), "-"],
            [t("metric.exif_thumbnails", default="  - EXIF thumbnails"), str(type_counts.get("exif_thumb", 0)), "-"],
            [t("metric.raw_carved", default="  - Raw stream carved"), str(type_counts.get("raw_carved", 0)), "-"],
            [t("metric.skipped_no_preview_or_exists", default="Skipped (no preview / exists)"), str(skipped_count), "-"],
            [t("metric.total_extracted_volume", default="Total extracted volume"), "-", format_size(total_saved_bytes)],
        ]
        headers = [t("table.header.metric", default="Metric"), t("table.header.count", default="Count"), t("table.header.size", default="Size")]
        print(render_table(t("table.title.carve", default="PHOTO HEALER — CARVER SUMMARY"), headers, table_rows))

    return 0


def handle_gui(args: argparse.Namespace) -> int:
    """Launch the GUI application with PySide6 dependency verification."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        lang = getattr(args, "lang", None) or get_language()
        if lang == "ru":
            sys.stderr.write(
                "Ошибка: Для работы графического интерфейса необходима библиотека PySide6.\n"
                "Установите зависимости GUI с помощью команды:\n"
                "    pip install photo-healer[gui]\n"
                "или:\n"
                "    pip install PySide6 pillow\n"
            )
        else:
            sys.stderr.write(
                "Error: GUI requires PySide6 and Pillow libraries.\n"
                "Install GUI dependencies with:\n"
                "    pip install photo-healer[gui]\n"
                "or:\n"
                "    pip install PySide6 pillow\n"
            )
        return 1

    try:
        from photo_healer.gui.app import main as gui_app_main
    except ImportError as e:
        sys.stderr.write(f"Error loading GUI modules: {e}\n")
        return 1

    gui_argv = ["photo-healer-gui"]
    if getattr(args, "folder", None):
        gui_argv.extend(["--folder", str(args.folder)])
    if getattr(args, "lang", None):
        gui_argv.extend(["--lang", str(args.lang)])

    return gui_app_main(gui_argv)


# ── CLI Parser Setup & Main ───────────────────────────────────────────────────
def build_parser(lang: str | None = None) -> argparse.ArgumentParser:
    """Construct the top-level argument parser and subcommands with localized text."""
    parser = argparse.ArgumentParser(
        prog="photo-healer",
        description=t("cli.description", lang=lang, default="Photo Healer — Forensic repair tool for SSD TRIM-damaged photo archives."),
        epilog=t("cli.epilog", lang=lang, default="Use 'photo-healer <command> --help' for details on each subcommand."),
    )
    parser.add_argument(
        "--lang",
        choices=list(SUPPORTED_LANGUAGES),
        help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru; default: system auto-detect)"),
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=t("cli.arg.version", lang=lang, default="Show program version and exit"),
    )

    subparsers = parser.add_subparsers(dest="command", metavar=t("cli.metavar.command", lang=lang, default="<command>"))

    # 1. triage
    p_triage = subparsers.add_parser(
        "triage",
        help=t("cmd.triage.help", lang=lang, default="Scan and audit directory for TRIM damage and candidates"),
        description=t("cmd.triage.desc", lang=lang, default="High-speed streaming triage to audit image archives and classify TRIM damage."),
    )
    p_triage.add_argument("path", help=t("triage.arg.path", lang=lang, default="Directory path to scan and audit"))
    p_triage.add_argument("--quarantine", metavar="DIR", help=t("triage.arg.quarantine", lang=lang, default="Move TRIM-zero files to quarantine directory"))
    p_triage.add_argument("--report", metavar="FILE", help=t("triage.arg.report", lang=lang, default="Save triage report to specified JSON file"))
    p_triage.add_argument("--ext", nargs="+", metavar="EXT", help=t("triage.arg.ext", lang=lang, default="File extensions to include (default: all image types)"))
    p_triage.add_argument("--dry-run", action="store_true", help=t("triage.arg.dry_run", lang=lang, default="Simulate quarantine without moving files"))
    p_triage.add_argument("--force", action="store_true", help=t("triage.arg.force", lang=lang, default="Overwrite existing files in quarantine destination"))
    p_triage.add_argument("--quiet", action="store_true", help=t("triage.arg.quiet", lang=lang, default="Suppress progress bars and summary tables"))
    p_triage.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))
    p_triage.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))

    # 2. heal
    p_heal = subparsers.add_parser(
        "heal",
        help=t("cmd.heal.help", lang=lang, default="Heal a single photo using a donor JPEG header"),
        description=t("cmd.heal.desc", lang=lang, default="Transplant donor JPEG markers (DQT, DHT, SOF, SOS) onto damaged file."),
    )
    p_heal.add_argument("broken_file", help=t("heal.arg.broken_file", lang=lang, default="Path to damaged JPEG image"))
    p_heal.add_argument("--donor", required=True, help=t("heal.arg.donor", lang=lang, default="Path to healthy donor JPEG image"))
    p_heal.add_argument("--output", metavar="DEST", help=t("heal.arg.output", lang=lang, default="Output file path (default: <name>_HEALED.jpg)"))
    p_heal.add_argument("--inplace", action="store_true", help=t("heal.arg.inplace", lang=lang, default="Replace original file in place (creates .bak backup)"))
    p_heal.add_argument("--force", action="store_true", help=t("heal.arg.force", lang=lang, default="Force overwrite existing destination or backup files"))
    p_heal.add_argument("--dry-run", action="store_true", help=t("heal.arg.dry_run", lang=lang, default="Simulate healing without writing to disk"))
    p_heal.add_argument("--quiet", action="store_true", help=t("heal.arg.quiet", lang=lang, default="Suppress progress and informational logs"))
    p_heal.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))
    p_heal.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))

    # 3. batch-heal
    p_batch = subparsers.add_parser(
        "batch-heal",
        help=t("cmd.batch_heal.help", lang=lang, default="Batch recovery of photo series with auto-donor matching"),
        description=t("cmd.batch_heal.desc", lang=lang, default="Scan directory for all TRIM-damaged candidates and repair using donor headers."),
    )
    p_batch.add_argument("folder", help=t("batch_heal.arg.folder", lang=lang, default="Folder containing damaged photos"))
    p_batch.add_argument("--donor", help=t("batch_heal.arg.donor", lang=lang, default="Explicit donor file to use for all candidates"))
    p_batch.add_argument("--auto-donor", action="store_true", help=t("batch_heal.arg.auto_donor", lang=lang, default="Automatically search for healthy donor in folder"))
    p_batch.add_argument("--output", metavar="DIR", help=t("batch_heal.arg.output", lang=lang, default="Output directory for healed photos"))
    p_batch.add_argument("--inplace", action="store_true", help=t("batch_heal.arg.inplace", lang=lang, default="Replace original files in place (creates .bak backups)"))
    p_batch.add_argument("--force", action="store_true", help=t("batch_heal.arg.force", lang=lang, default="Force overwrite existing healed files or backups"))
    p_batch.add_argument("--dry-run", action="store_true", help=t("batch_heal.arg.dry_run", lang=lang, default="Simulate batch healing without writing files"))
    p_batch.add_argument("--quiet", action="store_true", help=t("batch_heal.arg.quiet", lang=lang, default="Suppress progress bars and summary output"))
    p_batch.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))
    p_batch.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))

    # 4. quarantine
    p_quar = subparsers.add_parser(
        "quarantine",
        help=t("cmd.quarantine.help", lang=lang, default="Safely move TRIM-zero unrecoverable files to quarantine"),
        description=t("cmd.quarantine.desc", lang=lang, default="Relocate TRIM-erased 0x00 files recorded in triage report to clean the archive."),
    )
    p_quar.add_argument("--report", required=True, metavar="FILE", help=t("quarantine.arg.report", lang=lang, default="JSON report path generated by triage"))
    p_quar.add_argument("--dest", required=True, metavar="DIR", help=t("quarantine.arg.dest", lang=lang, default="Quarantine destination directory"))
    p_quar.add_argument("--dry-run", action="store_true", help=t("quarantine.arg.dry_run", lang=lang, default="Simulate moves without moving files"))
    p_quar.add_argument("--force", action="store_true", help=t("quarantine.arg.force", lang=lang, default="Force overwrite if destination already exists"))
    p_quar.add_argument("--quiet", action="store_true", help=t("quarantine.arg.quiet", lang=lang, default="Suppress progress and summary output"))
    p_quar.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))
    p_quar.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))

    # 5. carve
    p_carve = subparsers.add_parser(
        "carve",
        help=t("cmd.carve.help", lang=lang, default="Extract embedded previews (MPF, EXIF thumbnails, raw streams)"),
        description=t("cmd.carve.desc", lang=lang, default="Carve embedded JPEG thumbnails, Full HD MPF previews, or raw image streams."),
    )
    p_carve.add_argument("path", help=t("carve.arg.path", lang=lang, default="File or folder path to carve previews from"))
    p_carve.add_argument("--dest", metavar="DIR", help=t("carve.arg.dest", lang=lang, default="Output directory for carved previews (default: _Previews)"))
    p_carve.add_argument("--force", action="store_true", help=t("carve.arg.force", lang=lang, default="Force overwrite existing carved previews"))
    p_carve.add_argument("--dry-run", action="store_true", help=t("carve.arg.dry_run", lang=lang, default="Simulate extraction without writing files"))
    p_carve.add_argument("--quiet", action="store_true", help=t("carve.arg.quiet", lang=lang, default="Suppress progress and summary output"))
    p_carve.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))
    p_carve.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))

    # 6. gui
    p_gui = subparsers.add_parser(
        "gui",
        help=t("cmd.gui.help", lang=lang, default="Launch Photo Healer graphical desktop application"),
        description=t("cmd.gui.desc", lang=lang, default="Launch the interactive desktop interface with diagnostics, recovery, and preview gallery."),
    )
    p_gui.add_argument("--folder", metavar="DIR", help=t("gui.arg.folder", lang=lang, default="Initial archive folder to open in GUI"))
    p_gui.add_argument("--lang", choices=list(SUPPORTED_LANGUAGES), help=t("cli.arg.lang", lang=lang, default="Interface language (en, ru)"))
    p_gui.add_argument("--no-banner", action="store_true", help=t("cli.arg.no_banner", lang=lang, default="Suppress terminal splash screen and ASCII banner"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    # Early detection of --lang in argv before constructing parser so help is translated
    chosen_lang: str | None = None
    for idx, arg in enumerate(argv):
        if arg == "--lang" and idx + 1 < len(argv):
            chosen_lang = argv[idx + 1]
            break
        elif arg.startswith("--lang="):
            chosen_lang = arg.split("=", 1)[1]
            break

    if chosen_lang:
        set_language(chosen_lang)

    active_lang = get_language()
    parser = build_parser(lang=active_lang)

    if not argv:
        if "--no-banner" not in sys.argv:
            show_banner(version=__version__, lang=active_lang)
        parser.print_help()
        return 1

    args = parser.parse_args(argv)

    if getattr(args, "lang", None):
        set_language(args.lang)
        active_lang = args.lang

    quiet = getattr(args, "quiet", False)
    no_banner = getattr(args, "no_banner", False) or "--no-banner" in argv

    if not quiet and not no_banner:
        show_banner(version=__version__, lang=active_lang)

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
    elif args.command == "gui":
        return handle_gui(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
