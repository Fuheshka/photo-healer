#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo Healer — triage.py  v2.0
High-speed streaming triage for SSD TRIM-damaged photo archives.

Classifies every image file into:
  trim_zero        — 100% zero bytes (TRIM-erased, unrecoverable)
  healed_candidate — leading zeros + live data (header transplant possible)
  valid            — starts with correct magic bytes (intact)
  other            — unknown/non-image format

Streams files in 64 KB chunks — never loads entire file into RAM.

Usage:
  python triage.py <root_dir> [options]

Options:
  --quarantine <dir>   Move trim_zero files here (keeps folder structure)
  --report <file>      JSON report path (default: triage_report.json)
  --ext .jpg .png ...  Limit to these extensions (default: all image types)
  --dry-run            Show quarantine moves without executing them
"""

import io
import os
import sys
import json
import shutil
import argparse
from pathlib import Path

def ensure_utf8_io() -> None:
    """Safely configure stdout/stderr for UTF-8 on Windows without breaking capture streams."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ── Constants ─────────────────────────────────────────────────────────────────

CHUNK = 65536          # 64 KB streaming chunk
ZERO_CHUNK = b"\x00" * CHUNK
MAGIC = {
    ".jpg":  b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png":  b"\x89PNG",
    ".gif":  b"GIF8",
    ".bmp":  b"BM",
    ".tif":  b"\x49\x49\x2a",   # little-endian TIFF
    ".tiff": b"\x4d\x4d\x00\x2a",  # big-endian TIFF
    ".webp": b"RIFF",
}
IMAGE_EXTS = set(MAGIC.keys())

# Status labels
TRIM_ZERO  = "trim_zero"
CANDIDATE  = "healed_candidate"
VALID      = "valid"
OTHER      = "other"
EMPTY      = "empty"
ERROR      = "error"


# ── Core classifier ───────────────────────────────────────────────────────────

def classify(
    path: Path | str,
    cached_size: int | None = None,
    cached_name: str | None = None,
) -> dict:
    """
    Stream-reads path in 64 KB chunks.
    Returns a dict: path, name, size, status, first_nonzero, note.
    Never loads the whole file into memory.
    """
    str_path = str(path)
    name = cached_name or (path.name if isinstance(path, Path) else os.path.basename(str_path))
    result = {
        "path": str(path.resolve()) if isinstance(path, Path) and cached_name is None else str_path,
        "name": name,
        "size": 0,
        "status": ERROR,
        "first_nonzero": -1,
        "note": "",
    }

    if cached_size is not None:
        size = cached_size
    else:
        try:
            size = (path if isinstance(path, Path) else Path(str_path)).stat().st_size
        except OSError as e:
            result["note"] = str(e)
            return result

    result["size"] = size

    if size == 0:
        result["status"] = EMPTY
        result["note"] = "zero-length file"
        return result

    dot_pos = name.rfind(".")
    ext = name[dot_pos:].lower() if dot_pos != -1 else ""
    expected_magic = MAGIC.get(ext)

    try:
        with open(str_path, "rb") as fh:
            offset = 0
            first_nonzero = -1
            first_chunk: bytes | None = None

            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                if first_chunk is None:
                    first_chunk = chunk
                if chunk == ZERO_CHUNK:
                    offset += len(chunk)
                    continue
                trimmed = chunk.lstrip(b"\x00")
                if trimmed:
                    first_nonzero = offset + (len(chunk) - len(trimmed))
                    break
                offset += len(chunk)

    except OSError as e:
        result["note"] = str(e)
        return result

    result["first_nonzero"] = first_nonzero

    if first_nonzero == -1:
        # All bytes read were zero
        result["status"] = TRIM_ZERO
        result["note"] = f"100% zeros — TRIM erased ({size / 1048576:.2f} MB)"
        return result

    if first_nonzero == 0:
        # File starts with non-zero data — check magic
        if expected_magic:
            head = (first_chunk or b"")[:len(expected_magic)]
            if head == expected_magic:
                result["status"] = VALID
                result["note"] = "intact"
            else:
                result["status"] = OTHER
                result["note"] = f"unexpected magic: {head[:8].hex(' ').upper()}"
        else:
            result["status"] = OTHER
            result["note"] = f"no magic for ext {ext}"
        return result

    # first_nonzero > 0 → leading zeros + live data = candidate
    result["status"] = CANDIDATE
    result["note"] = (
        f"header zeroed ({first_nonzero} bytes = "
        f"{first_nonzero // 512} sectors), "
        f"live data starts at {first_nonzero}"
    )
    return result


# ── Directory scanner ─────────────────────────────────────────────────────────

def scan(root: Path, exts: set[str]) -> list[dict]:
    results = []
    total = 0
    resolved_root = root.resolve()
    candidate_entries: list[tuple[str, str, int]] = []

    def scan_dir(dir_path: str) -> None:
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            scan_dir(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            name = entry.name
                            dot_pos = name.rfind(".")
                            ext = name[dot_pos:].lower() if dot_pos != -1 else ""
                            if ext in exts:
                                try:
                                    sz = entry.stat(follow_symlinks=False).st_size
                                except OSError:
                                    sz = 0
                                candidate_entries.append((entry.path, name, sz))
                    except OSError:
                        continue
        except OSError:
            pass

    scan_dir(str(resolved_root))

    for fpath_str, fname, fsize in candidate_entries:
        total += 1
        r = classify(fpath_str, cached_size=fsize, cached_name=fname)
        results.append(r)

        if total % 200 == 0:
            counts = _counts(results)
            print(
                f"  [{total:>5}]  valid={counts.get(VALID,0):>4}  "
                f"trim={counts.get(TRIM_ZERO,0):>4}  "
                f"candidate={counts.get(CANDIDATE,0):>3}  "
                f"other={counts.get(OTHER,0):>3}",
                flush=True,
            )

    return results


def _counts(results: list[dict]) -> dict:
    c: dict[str, int] = {}
    for r in results:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    counts = _counts(results)
    sizes: dict[str, int] = {}
    for r in results:
        sizes[r["status"]] = sizes.get(r["status"], 0) + r["size"]

    icons = {
        VALID:     "[OK]  ",
        TRIM_ZERO: "[TRIM]",
        CANDIDATE: "[HEAL]",
        OTHER:     "[???] ",
        EMPTY:     "[NULL]",
        ERROR:     "[ERR] ",
    }

    width = 60
    print("\n" + "=" * width)
    print("  PHOTO HEALER — TRIAGE REPORT")
    print("=" * width)
    for status in (VALID, CANDIDATE, TRIM_ZERO, OTHER, EMPTY, ERROR):
        n = counts.get(status, 0)
        if n == 0:
            continue
        mb = sizes.get(status, 0) / 1_048_576
        icon = icons.get(status, "      ")
        print(f"  {icon}  {status:<20} {n:>5} files   {mb:>9,.1f} MB")
    print("=" * width)
    total_n = sum(counts.values())
    total_mb = sum(sizes.values()) / 1_048_576
    print(f"  TOTAL                        {total_n:>5} files   {total_mb:>9,.1f} MB")
    print("=" * width)


# ── Quarantine ────────────────────────────────────────────────────────────────

def quarantine(
    results: list[dict],
    dest: Path,
    root: Path,
    dry_run: bool = False,
) -> tuple[int, float]:
    """Move trim_zero files to dest, preserving folder structure."""
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    moved = 0
    freed = 0.0

    for r in results:
        if r["status"] != TRIM_ZERO:
            continue
        src = Path(r["path"])
        if not src.exists():
            continue

        try:
            rel = src.relative_to(root)
        except ValueError:
            rel = Path(src.name)

        dst = dest / rel

        if dry_run:
            print(f"  [DRY] {src.name}  ->  {rel}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved += 1
                freed += r["size"]
            except OSError as e:
                print(f"  [ERR] {src.name}: {e}", file=sys.stderr)

    return moved, freed / 1_048_576


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(
        description="Photo Healer Triage v2 — streaming TRIM-damage classifier"
    )
    parser.add_argument("root", help="Directory to scan")
    parser.add_argument(
        "--quarantine",
        metavar="DIR",
        help="Move TRIM-zero files to this directory (safe, no deletion)",
    )
    parser.add_argument(
        "--report",
        default="triage_report.json",
        help="Output JSON report (default: triage_report.json)",
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        metavar="EXT",
        help="File extensions to include (e.g. .jpg .jpeg). Default: all image types",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --quarantine: show moves without executing",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        sys.exit(f"ERROR: not a directory: {root}")

    exts = {e if e.startswith(".") else f".{e}" for e in args.ext} if args.ext else IMAGE_EXTS

    print(f"[SCAN] {root}")
    print(f"       extensions : {', '.join(sorted(exts))}")
    print(f"       chunk size : {CHUNK // 1024} KB\n")

    results = scan(root, exts)
    print_summary(results)

    # JSON report
    report_path = Path(args.report)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[RPT] Report saved: {report_path}")

    # Heal-candidates shortlist
    candidates = [r for r in results if r["status"] == CANDIDATE]
    if candidates:
        cand_path = report_path.parent / "heal_candidates.json"
        with open(cand_path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        print(f"[RPT] Heal candidates ({len(candidates)}): {cand_path}")

    # Quarantine
    if args.quarantine:
        q_dir = Path(args.quarantine)
        mode = "DRY-RUN" if args.dry_run else "MOVING"
        print(f"\n[Q] {mode} trim_zero -> {q_dir}")
        moved, freed_mb = quarantine(results, q_dir, root, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"[Q] Moved: {moved} files  ({freed_mb:,.1f} MB freed)")


if __name__ == "__main__":
    main()
