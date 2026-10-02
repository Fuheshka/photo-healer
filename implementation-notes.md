# Photo Healer — Implementation Notes

## Prompt 1: Triage + Quarantine (2026-10-02)

### Goal
High-speed batch scanner for SSD TRIM-damaged photo archive.
Classify every image as `trim_zero` (100% zeros) or `healed_candidate` (live data behind zeroed header).

### Technical findings (from forensic analysis)

**Archive**: `E:\15407 DATA\!Problem\ВсеФотографии\` — 2385 image files, 4.45 GB

**TRIM pattern confirmed:**
- Exactly **65 536 bytes** (128 × 512-byte SSD sectors) zeroed at file start
- From offset 65 536: high-entropy compressed data (Huffman-coded JPEG bitstream)
- Pattern is 100% consistent across all 7 healed candidates — same SSD block size

**Two damage types:**
1. `trim_zero` — entire file content erased. File size survives in MFT/FAT, but every byte is 0x00. **2 345 files (98.2%)** — unrecoverable.
2. `healed_candidate` — first 65 536 bytes (JPEG header + beginning of scan) erased, remaining entropy stream intact. **7 files (0.3%)** — all repaired.

**Donor header structure (SANY0058.JPG, SANYO 8MP camera):**
```
SOI   (FF D8)              offset 0,      2 bytes
APP0  (FF E0, len=16)      offset 2,     18 bytes  — JFIF marker
APP1  (FF E1, len=46210)   offset 20, 46212 bytes  — Exif metadata
DQT   (FF DB, len=67)      offset 46232, 69 bytes  — luma quantization table
DQT   (FF DB, len=67)      offset 46301, 69 bytes  — chroma quantization table
SOF0  (FF C0, len=17)      offset 46370, 19 bytes  — frame header (3264×2448)
DHT   (FF C4) ×4            offsets 46389–46820    — Huffman tables
SOS   (FF DA, len=12)      offset 46821, 14 bytes  — scan header
─────────────────────────────────────────────────
Total header: 46 835 bytes
```

**Validation:**
- All 7 healed files decoded by `System.Drawing.Image` as 3264×2448 px
- Visual content confirmed unique per file (different scenes, not donor thumbnail)
- Double EOI guard: `heal.py` checks if live data already ends in `FF D9` before appending

### Triage algorithm (both Python and PowerShell)

```
for each file:
    stream in 64 KB chunks
    find first_nonzero offset
    if first_nonzero == -1 → trim_zero (100% zeros)
    if first_nonzero ==  0 → check magic bytes → valid or other
    if first_nonzero  >  0 → healed_candidate
```

Stream approach guarantees O(1) memory regardless of file size.
For `trim_zero` files, average read is ≤ 1 chunk (they're completely zero so we stop at EOF).
For `valid` files we stop at the first non-zero byte in chunk 1.

### Test results (Prompt 1 verification)

**Test folder: `2 класс Настя ярмарка`**
```
IMG-20221028-WA0031.jpg → trim_zero ✅  (100% zeros, size=4 308 704 bytes)
```

**Test folder: `Детские`**
```
SANY0015.JPG → healed_candidate ✅  (first_nonzero=65536, live data 4 050 253 bytes)
SANY0058.JPG → valid ✅  (FF D8 FF, 3 857 509 bytes, used as donor)
```

**Full archive scan (triage_report.json)**
| status | files | MB |
|---|---|---|
| trim_zero | 2 345 | 4 315.7 |
| healed_candidate | 7 | 20.1 |
| valid | 33 | 115.8 |

**Batch heal (heal.py)**
- All 7 candidates healed: `SANY0015,0016,0037,0038,0039,0192,1342`
- Donor auto-detected: `SANY0058.JPG` (first valid JPEG in same folder)
- Output: `*_HEALED.JPG` files created alongside originals

**Quarantine (quarantine.py)**
- 2 345 TRIM-zero files moved to `E:\15407 DATA\!Problem\QUARANTINE_TRIM_ZERO\`
- Folder structure preserved
- 4 315.7 MB freed from working archive

### Decisions and trade-offs

| Decision | Rationale |
|---|---|
| Donor header from same folder | Same camera model → same DQT/DHT/SOF0 → highest compatibility |
| 65 536 byte offset hardcoded | 100% consistent across all candidates; not guessed |
| No `--delete` flag in triage | Safety: move to quarantine only, never destroy without explicit `--delete` |
| PowerShell version | Windows users without Python; PS 5.1 is pre-installed on Win10+ |
| Double EOI guard | Some files already have `FF D9` at end of entropy stream |

### Known limitations

- Donor header compatibility: works for same-model cameras. Different cameras may need custom donor selection.
- EXIF metadata in healed files: contains donor's Exif (wrong date/GPS). To fix: use ExifTool to strip and re-inject correct EXIF from backup/sidecar.
- This triage only covers JPEG/PNG/BMP/GIF/TIFF/WEBP. RAW formats (CR2, NEF, ARW) need separate magic table entries.

### Files created / modified

| File | Status |
|---|---|
| `triage.py` | Rewritten v2 — streaming 64 KB, full spec compliance |
| `triage.ps1` | New — PowerShell port, identical logic |
| `heal.py` | Written — auto-donor + `--inplace` mode |
| `quarantine.py` | Written — folder-structure-aware quarantine |
| `README.md` | Written — full project documentation |
| `implementation-notes.md` | This file |
