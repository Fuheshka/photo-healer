# Photo Healer

**High-speed triage and header-transplant toolkit for SSD TRIM-damaged photo archives.**

When an SSD controller runs TRIM after a partition deletion or format, it zeroes out blocks containing your files. The filesystem metadata (filenames, sizes) can survive, but the file contents — including the critical JPEG header — are wiped. This toolkit detects which files are truly gone and which can be repaired.

---

## The Problem

| Symptom | Cause | Recoverable? |
|---|---|---|
| File opens as blank / "invalid image" | First 128 sectors (65 536 bytes) zeroed | **Yes** — header transplant |
| File is 100% zeros | Entire file erased by TRIM | **No** |

---

## Tools

| Script | Language | Purpose |
|---|---|---|
| `triage.py` | Python 3.10+ | Classify all images — streaming 64 KB chunks |
| `triage.ps1` | PowerShell 5+ | Same, no Python required |
| `heal.py` | Python 3.10+ | Transplant donor JPEG header into damaged files |
| `quarantine.py` | Python 3.10+ | Move TRIM-zero files to quarantine folder |

---

## Quick Start

### 1. Scan the archive (Python)

```bash
python triage.py "E:\Photos\Archive" --report report.json
```

### 1. Scan the archive (PowerShell — no Python needed)

```powershell
.\triage.ps1 -Root "E:\Photos\Archive"
```

### 2. Move TRIM-zero files to quarantine

```bash
# Python
python triage.py "E:\Photos\Archive" --quarantine "E:\TRIM_Quarantine"

# PowerShell
.\triage.ps1 -Root "E:\Photos\Archive" -Quarantine "E:\TRIM_Quarantine"
```

### 3. Heal files with recoverable data

```bash
python heal.py --candidates heal_candidates.json
# With --inplace: replaces originals (backup → .bak)
python heal.py --candidates heal_candidates.json --inplace
```

---

## How Triage Works

```
For each image file:
  1. Stream in 64 KB chunks — never loads whole file
  2. Find first non-zero byte offset
     → offset = -1   : trim_zero        (100% zeros, unrecoverable)
     → offset =  0   : valid / other    (check magic bytes)
     → offset > 0    : healed_candidate (header zeroed, data intact)
```

### Status codes

| Status | Meaning |
|---|---|
| `valid` | Correct magic bytes at start — intact file |
| `trim_zero` | 100% zeros — TRIM erased, unrecoverable |
| `healed_candidate` | Leading zeros + live data — repairable via donor header |
| `other` | Non-zero start, wrong/unknown magic |

---

## How Header Transplant Works

JPEG structure:
```
[SOI FF D8] → [APP0/APP1 Exif] → [DQT quantization] → [SOF0 frame info]
→ [DHT huffman] → [SOS scan header] → <entropy-coded bitstream> → [EOI FF D9]
```

The TRIM-damaged files lose everything up to the entropy stream. `heal.py`:
1. Finds a valid donor JPEG from the same camera model (same folder)
2. Extracts donor header: `SOI → ... → SOS` (≈47 KB)
3. Concatenates: `donor_header + target_live_data`
4. Validates via `System.Drawing` (Windows) or PIL decode

---

## Requirements

- Python 3.10+ (for `triage.py`, `heal.py`, `quarantine.py`)
- PowerShell 5.1+ (for `triage.ps1`, built-in on Windows 10+)
- No external dependencies

---

## Real-world results

Tested on a 4.45 GB family photo archive recovered from a TRIM-damaged SSD:

```
[TRIM]  trim_zero            2345 files   4,315.7 MB  (unrecoverable)
[HEAL]  healed_candidate        7 files      20.1 MB  (all repaired successfully)
[OK]    valid                  33 files     115.8 MB  (intact donors)
```

All 7 healed files decoded as 3264×2448 px (8 MP SANYO camera). Images were visually intact.

---

## License

MIT
