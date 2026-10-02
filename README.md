# Photo Healer

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-396%20passed-brightgreen.svg)](tests/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20%7C%20Qt-green.svg)](src/photo_healer/gui/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/Fuheshka/photo-healer)

[English](README.md) | [Русский](README.ru.md)

**Forensic triage and header-transplant toolkit for photo archives damaged by SSD TRIM and quick formatting.**

When a solid-state drive (SSD) undergoes partition deletion, quick format, or TRIM garbage collection, the storage controller zeroes out blocks allocated to your files. Filesystem recovery tools (such as Recuva, DMDE, R-Studio, or PhotoRec) can restore file names and directory trees, but the files fail to open or appear completely black: the critical first 32–64 KB containing the JPEG file header have been wiped with zeros.

However, the **entropy data** (the actual DCT coefficients and Huffman-encoded bitstream representing the photo) is stored in subsequent clusters and often survives intact. **Photo Healer** inspects the damage, determines whether live photo data remains, transplants donor headers from undamaged photos taken with the same camera model, resynchronizes shifted MCU streams, and carves embedded preview thumbnails.

---

## Key Features

- **Streaming O(1) Triage**: Scans archives using streaming 64 KB chunks without loading entire files into memory. Instantly categorizes files into intact, unrecoverable TRIM zeros, repairable candidates, or unknown formats.
- **Donor Header Transplant**: Extracts valid JPEG headers (`SOI` -> `Exif` -> `DQT` -> `SOF0` -> `DHT` -> `SOS`) from matching donor photos and splices them onto damaged entropy streams.
- **MCU Stream Resynchronization**: Detects damaged restart markers (`RST0`–`RST7`) and applies interval padding (`pad_geometry`) to fix shifted color blocks and horizontal banding.
- **Embedded Thumbnail Carver**: Carves intact preview images (`APP1 EXIF` thumbnails, `APP2 MPF Full HD` previews, or raw JPEG streams) from files that cannot be fully restored at native resolution.
- **Interactive PySide6 GUI**:
  - **Triage Table**: Virtualized table supporting 50,000+ files with live status filters and badge delegates.
  - **Before-After Split Preview**: Interactive comparison view with a draggable divider slider, synchronous pan & zoom, and diagnostic quality cards.
  - **Thumbnail Gallery (Carve View)**: Adaptive grid of extracted embedded previews with resolution badges and batch export.
- **Autonomous Update Checker**: Zero-dependency background check via the GitHub Releases API with a 24-hour rate limit guard (`photo-healer update-check`).
- **Standalone Zero-Dependency Scripts**: Includes `triage.ps1` for pure Windows environments without Python installed, alongside standalone `triage.py`, `heal.py`, and `quarantine.py`.

---

## The SSD TRIM Problem Explained

| Symptom | Cause | Recoverable? |
|---|---|---|
| File opens as blank / "Invalid image error" | First 128 sectors (65,536 bytes) zeroed by TRIM | **Yes** — donor header transplant |
| File content is 100% zeros | Entire file cluster range erased by TRIM controller | **No** — TRIM zero, move to quarantine |
| Photo has shifted rows or purple/green color blocks | Missing/corrupted restart markers in entropy stream | **Yes** — MCU stream resynchronization |
| High-resolution stream corrupted, but EXIF survived | Partial cluster erasure across image body | **Yes** — carve embedded APP1/APP2 previews |

```
Standard JPEG Structure:
[SOI FF D8] → [APP0/APP1 Exif] → [DQT quantization] → [SOF0 frame info]
→ [DHT huffman] → [SOS scan header] → <entropy-coded bitstream> → [EOI FF D9]

SSD TRIM Damage Profile:
[00 00 00 ... 00 00 00] (0 to ~64 KB zeroed) → <live entropy data survives!>
```

---

## Quick Start

### Installation

Install with optional GUI support:
```bash
pip install -e ".[gui]"
```

For development and test execution:
```bash
pip install -e ".[dev,gui]"
```

### 1. Launching the GUI

Launch the interactive desktop interface:
```bash
photo-healer gui
# or direct executable
photo-healer-gui
```

### 2. Command-Line Usage (CLI)

#### Triage and Scan an Archive
```bash
# Analyze all images and save a detailed JSON report
photo-healer triage "D:\RecoveredPhotos" --report report.json

# Analyze and automatically isolate unrecoverable 100% TRIM-zero files
photo-healer triage "D:\RecoveredPhotos" --quarantine "D:\TRIM_Quarantine"
```

#### Heal Damaged Photos
```bash
# Transplant headers for candidate files listed in report.json
photo-healer heal --candidates report.json --output "D:\HealedPhotos"

# In-place repair with automatic .bak backup creation
photo-healer heal --candidates report.json --inplace

# Batch auto-heal an entire directory using matching donors from the same folder
photo-healer batch-heal "D:\RecoveredPhotos" --out-dir "D:\HealedPhotos"
```

#### Carve Embedded Thumbnails and Previews
```bash
# Extract EXIF and MPF preview images from damaged or unrecoverable photos
photo-healer carve "D:\RecoveredPhotos" --out-dir "D:\ExtractedPreviews"
```

#### Check for Updates
```bash
# Query the latest GitHub release
photo-healer update-check
```

### 3. Standalone Scripts (No Installation Required)

For emergency field triage on client machines:
```powershell
# Pure PowerShell 5.1+ (Windows built-in, zero dependencies)
.\triage.ps1 -Root "D:\RecoveredPhotos" -Quarantine "D:\TRIM_Quarantine"
```

```bash
# Standalone Python scripts
python triage.py "D:\RecoveredPhotos" --report report.json
python heal.py --candidates heal_candidates.json --inplace
python quarantine.py --report report.json --target "D:\TRIM_Quarantine"
```

---

## Architecture Overview

```
photo-healer/
├── src/photo_healer/
│   ├── core/                  # Core forensic algorithms (zero-dependency)
│   │   ├── carver.py          # APP1/APP2 and raw thumbnail extraction
│   │   ├── entropy.py         # Shannon entropy and bitstream analysis
│   │   ├── parser.py          # JPEG segment & marker parser (SOI..SOS)
│   │   ├── resync.py          # MCU restart interval realignment
│   │   ├── splicer.py         # Donor header synthesis and splicing
│   │   └── validator.py       # Structural JPEG validation
│   ├── cli/                   # Command-line interface
│   │   ├── main.py            # CLI entry point (triage, heal, carve, etc.)
│   │   ├── banner.py          # Monospace ASCII header cards
│   │   ├── i18n.py            # Bilingual CLI localization (EN / RU)
│   │   └── updater.py         # GitHub Releases update checker
│   └── gui/                   # Desktop graphical interface (PySide6)
│       ├── app.py             # Qt application bootstrap
│       ├── i18n.py            # Reactive GUI localization manager
│       ├── models/            # Virtualized table & filter models
│       ├── views/             # Triage, Heal, and Carve views
│       ├── widgets/           # Before-after split preview with zoom
│       └── workers/           # Asynchronous QThread workers
├── tests/                     # 396+ automated pytest test cases
├── triage.ps1                 # Standalone zero-dependency PowerShell scanner
├── triage.py                  # Standalone triage script
├── heal.py                    # Standalone header transplant script
└── quarantine.py              # Standalone file quarantine script
```

---

## Real-World Case Study

Tested on a 4.45 GB family photo archive recovered from a partition-formatted SSD drive:

```
[TRIM]  trim_zero            2,345 files   4,315.7 MB  (100% zeroes, unrecoverable)
[HEAL]  healed_candidate         7 files      20.1 MB  (100% successfully restored)
[OK]    valid                   33 files     115.8 MB  (intact photos used as donors)
```

All 7 damaged files were reconstructed to full 3264×2448 px resolution (8 MP SANYO camera), with all visual elements restored without decoding errors.

---

## Contributing

Contributions to this project are warmly welcomed! Here is how you can help:

- 🐛 **Report Bugs**: Encountered an issue, decoding error, or UI glitch? File a [Bug Report](https://github.com/Fuheshka/photo-healer/issues/new?template=bug_report.md).
- 💡 **Request Features**: Need support for a new RAW/PNG format or a new GUI feature? Submit a [Feature Request](https://github.com/Fuheshka/photo-healer/issues/new?template=feature_request.md).
- 🛠 **Submit Pull Requests**: Want to fix an issue or improve algorithms? Check out our [Contributing Guide (CONTRIBUTING.md)](CONTRIBUTING.md) and open a PR!
- ⭐️ **Star the Repo**: Give the project a star on [GitHub](https://github.com/Fuheshka/photo-healer) to help others discover it.

---

## Author & Support

- **Author**: Fuheshka ([@Fuheshka](https://github.com/Fuheshka))
- **Repository**: [github.com/Fuheshka/photo-healer](https://github.com/Fuheshka/photo-healer)

---

## License

This project is licensed under the MIT License.
