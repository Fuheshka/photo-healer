# -*- coding: utf-8 -*-
"""Bilingual (English & Russian) UI localization for Photo Healer CLI.

Follows app-i18n-localization standard with zero-dependency lightweight dictionary,
automatic runtime system locale detection, manual override via --lang or
PHOTO_HEALER_LANG environment variable, and Windows console UTF-8 safety wrapper.
"""

from __future__ import annotations

import ctypes
import locale
import os
import sys
import warnings
from typing import Any

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ru")
DEFAULT_LANGUAGE: str = "en"

_CURRENT_LANGUAGE: str | None = None

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
    "batch_heal.arg.auto_donor": "Automatically search for healthy donor in folder",
    "batch_heal.arg.donor": "Explicit donor file to use for all candidates",
    "batch_heal.arg.dry_run": "Simulate batch healing without writing files",
    "batch_heal.arg.folder": "Folder containing damaged photos",
    "batch_heal.arg.force": "Force overwrite existing healed files or backups",
    "batch_heal.arg.inplace": "Replace original files in place (creates .bak backups)",
    "batch_heal.arg.output": "Output directory for healed photos",
    "batch_heal.arg.quiet": "Suppress progress bars and summary output",
    "batch_heal.error.donor_not_found": "Error: Specified donor file does not exist: {path}",
    "batch_heal.error.folder_not_found": "Error: Folder does not exist: {folder}",
    "batch_heal.info.found_candidates": "Found {count} heal candidates. Starting batch restoration...",
    "batch_heal.info.no_candidates": "No damaged photo candidates found in {folder}.",
    "batch_heal.info.scanning": "Scanning for heal candidates in: {folder}",
    "batch_heal_arg_auto_donor": "Automatically search for healthy donor in folder",
    "batch_heal_arg_donor": "Explicit donor file to use for all candidates",
    "batch_heal_arg_dry_run": "Simulate batch healing without writing files",
    "batch_heal_arg_folder": "Folder containing damaged photos",
    "batch_heal_arg_force": "Force overwrite existing healed files or backups",
    "batch_heal_arg_inplace": "Replace original files in place (creates .bak backups)",
    "batch_heal_arg_output": "Output directory for healed photos",
    "batch_heal_arg_quiet": "Suppress progress bars and summary output",
    "batch_heal_error_donor_not_found": "Error: Specified donor file does not exist: {path}",
    "batch_heal_error_folder_not_found": "Error: Folder does not exist: {folder}",
    "batch_heal_info_found_candidates": "Found {count} heal candidates. Starting batch restoration...",
    "batch_heal_info_no_candidates": "No damaged photo candidates found in {folder}.",
    "batch_heal_info_scanning": "Scanning for heal candidates in: {folder}",
    "carve.arg.dest": "Output directory for carved previews (default: _Previews)",
    "carve.arg.dry_run": "Simulate extraction without writing files",
    "carve.arg.force": "Force overwrite existing carved previews",
    "carve.arg.path": "File or folder path to carve previews from",
    "carve.arg.quiet": "Suppress progress and summary output",
    "carve.dry_run.destination": "  Destination: {name}",
    "carve.dry_run.header": "[DRY-RUN] Would carve preview from {name}:",
    "carve.dry_run.resolution": "  Resolution : {resolution}",
    "carve.dry_run.size": "  Size       : {size}",
    "carve.dry_run.type": "  Type       : {type}",
    "carve.error.output_exists": "Error: Output file already exists: {path}. Use --force to overwrite.",
    "carve.error.path_not_found": "Error: Target path does not exist: {path}",
    "carve.error.write_failed": "Error writing carved preview: {error}",
    "carve.info.no_images": "No image files found in {path}.",
    "carve.info.no_preview": "No embedded preview or thumbnail found in: {name}",
    "carve.info.scanning": "Scanning {count} files for embedded previews...",
    "carve.success.header": "[CARVED] {src} -> {dst}",
    "carve_arg_dest": "Output directory for carved previews (default: _Previews)",
    "carve_arg_dry_run": "Simulate extraction without writing files",
    "carve_arg_force": "Force overwrite existing carved previews",
    "carve_arg_path": "File or folder path to carve previews from",
    "carve_arg_quiet": "Suppress progress and summary output",
    "carve_dry_run_destination": "  Destination: {name}",
    "carve_dry_run_header": "[DRY-RUN] Would carve preview from {name}:",
    "carve_dry_run_resolution": "  Resolution : {resolution}",
    "carve_dry_run_size": "  Size       : {size}",
    "carve_dry_run_type": "  Type       : {type}",
    "carve_error_output_exists": "Error: Output file already exists: {path}. Use --force to overwrite.",
    "carve_error_path_not_found": "Error: Target path does not exist: {path}",
    "carve_error_write_failed": "Error writing carved preview: {error}",
    "carve_info_no_images": "No image files found in {path}.",
    "carve_info_no_preview": "No embedded preview or thumbnail found in: {name}",
    "carve_info_scanning": "Scanning {count} files for embedded previews...",
    "carve_success_header": "[CARVED] {src} -> {dst}",
    "classify.note.header_read_error": "Header read error: {error}",
    "classify.note.intact_magic": "Intact file magic",
    "classify.note.read_error": "Read error: {error}",
    "classify.note.stat_error": "Stat error: {error}",
    "classify.note.trim_candidate": "TRIM header zeroed ({count} bytes), live stream starts at {offset}",
    "classify.note.trim_zero": "100% TRIM-erased zeros ({size})",
    "classify.note.unexpected_magic": "Unexpected magic: {magic}",
    "classify.note.unknown_magic": "Unknown magic for ext {ext}",
    "classify.note.zero_length": "Zero-length file",
    "classify_note_header_read_error": "Header read error: {error}",
    "classify_note_intact_magic": "Intact file magic",
    "classify_note_read_error": "Read error: {error}",
    "classify_note_stat_error": "Stat error: {error}",
    "classify_note_trim_candidate": "TRIM header zeroed ({count} bytes), live stream starts at {offset}",
    "classify_note_trim_zero": "100% TRIM-erased zeros ({size})",
    "classify_note_unexpected_magic": "Unexpected magic: {magic}",
    "classify_note_unknown_magic": "Unknown magic for ext {ext}",
    "classify_note_zero_length": "Zero-length file",
    "cli.arg.lang": "Interface language (en, ru; default: system auto-detect)",
    "cli.arg.no_banner": "Suppress terminal splash screen and ASCII banner",
    "cli.arg.verbose": "Enable verbose diagnostic logging and forensic trace",
    "cli.arg.version": "Show program version number and exit",
    "cli.description": "Photo Healer - Forensic repair tool for SSD TRIM-damaged photo archives.",
    "cli.epilog": "Use 'photo-healer <command> --help' for details on each subcommand.",
    "cli.metavar.command": "<command>",
    "cli_arg_lang": "Interface language (en, ru; default: system auto-detect)",
    "cli_arg_no_banner": "Suppress terminal splash screen and ASCII banner",
    "cli_arg_verbose": "Enable verbose diagnostic logging and forensic trace",
    "cli_arg_version": "Show program version number and exit",
    "cli_description": "Photo Healer - Forensic repair tool for SSD TRIM-damaged photo archives.",
    "cli_epilog": "Use 'photo-healer <command> --help' for details on each subcommand.",
    "cli_metavar_command": "<command>",
    "cmd.batch_heal.desc": "Scan directory for all TRIM-damaged candidates and repair using donor headers.",
    "cmd.batch_heal.help": "Batch recovery of photo series with auto-donor matching",
    "cmd.batch_heal.name": "batch-heal",
    "cmd.carve.desc": "Carve embedded JPEG thumbnails, Full HD MPF previews, or raw image streams.",
    "cmd.carve.help": "Extract embedded previews (MPF, EXIF thumbnails, raw streams)",
    "cmd.carve.name": "carve",
    "cmd.heal.desc": "Transplant donor JPEG markers (DQT, DHT, SOF, SOS) onto damaged file.",
    "cmd.heal.help": "Heal a single photo using a donor JPEG header",
    "cmd.heal.name": "heal",
    "cmd.quarantine.desc": "Relocate TRIM-erased 0x00 files recorded in triage report to clean the archive.",
    "cmd.quarantine.help": "Safely move TRIM-zero unrecoverable files to quarantine",
    "cmd.quarantine.name": "quarantine",
    "cmd.triage.desc": "High-speed streaming triage to audit image archives and classify TRIM damage.",
    "cmd.triage.help": "Scan and audit directory for TRIM damage and candidates",
    "cmd.triage.name": "triage",
    "cmd.gui.desc": "Launch the interactive desktop interface with diagnostics, recovery, and preview gallery.",
    "cmd.gui.help": "Launch Photo Healer graphical desktop application",
    "cmd.gui.name": "gui",
    "gui.arg.folder": "Initial archive folder to open in GUI",
    "cmd_batch_heal_desc": "Scan directory for all TRIM-damaged candidates and repair using donor headers.",
    "cmd_batch_heal_help": "Batch recovery of photo series with auto-donor matching",
    "cmd_batch_heal_name": "batch-heal",
    "cmd_carve_desc": "Carve embedded JPEG thumbnails, Full HD MPF previews, or raw image streams.",
    "cmd_carve_help": "Extract embedded previews (MPF, EXIF thumbnails, raw streams)",
    "cmd_carve_name": "carve",
    "cmd_heal_desc": "Transplant donor JPEG markers (DQT, DHT, SOF, SOS) onto damaged file.",
    "cmd_heal_help": "Heal a single photo using a donor JPEG header",
    "cmd_heal_name": "heal",
    "cmd_quarantine_desc": "Relocate TRIM-erased 0x00 files recorded in triage report to clean the archive.",
    "cmd_quarantine_help": "Safely move TRIM-zero unrecoverable files to quarantine",
    "cmd_quarantine_name": "quarantine",
    "cmd_triage_desc": "High-speed streaming triage to audit image archives and classify TRIM damage.",
    "cmd_triage_help": "Scan and audit directory for TRIM damage and candidates",
    "cmd_triage_name": "triage",
    "heal.arg.broken_file": "Path to damaged JPEG image",
    "heal.arg.donor": "Path to healthy donor JPEG image",
    "heal.arg.dry_run": "Simulate healing without writing to disk",
    "heal.arg.force": "Force overwrite existing destination or backup files",
    "heal.arg.inplace": "Replace original file in place (creates .bak backup)",
    "heal.arg.output": "Output file path (default: <name>_HEALED.jpg)",
    "heal.arg.quiet": "Suppress progress and informational logs",
    "heal.dry_run.donor_header": "  Donor header  : {size} bytes",
    "heal.dry_run.entropy_start": "  Entropy start : offset {offset}",
    "heal.dry_run.header": "[DRY-RUN] Would heal: {src} -> {dst}",
    "heal.dry_run.total_size": "  Total size    : {size}",
    "heal.dry_run.valid_jpeg_no": "  Valid JPEG    : No (warnings: {warnings})",
    "heal.dry_run.valid_jpeg_yes": "  Valid JPEG    : Yes",
    "heal.error.backup_exists": "Error: Backup file already exists: {path}. Use --force to overwrite.",
    "heal.error.broken_not_found": "Error: Broken file does not exist: {path}",
    "heal.error.broken_read_failed": "Error reading broken file: {error}",
    "heal.error.destination_exists": "Error: Destination file already exists: {path}. Use --force to overwrite.",
    "heal.error.donor_not_found": "Error: Donor file does not exist: {path}",
    "heal.error.donor_parse_failed": "Error: Failed to parse donor header from {name}: {error}",
    "heal.error.no_live_entropy": "Error: No live entropy data found in {name} (file is 100% TRIM-zero or corrupted).",
    "heal.error.splice_failed": "Error splicing donor header: {error}",
    "heal.error.write_failed": "Error writing output file: {error}",
    "heal.success.backup": "  Backup     : {name}",
    "heal.success.geometry": "  Geometry   : {geometry}",
    "heal.success.header": "[HEALED] {src} -> {dst}",
    "heal.success.total_size": "  Total size : {size}",
    "heal.val.unknown_resolution": "unknown resolution",
    "heal_arg_broken_file": "Path to damaged JPEG image",
    "heal_arg_donor": "Path to healthy donor JPEG image",
    "heal_arg_dry_run": "Simulate healing without writing to disk",
    "heal_arg_force": "Force overwrite existing destination or backup files",
    "heal_arg_inplace": "Replace original file in place (creates .bak backup)",
    "heal_arg_output": "Output file path (default: <name>_HEALED.jpg)",
    "heal_arg_quiet": "Suppress progress and informational logs",
    "heal_dry_run_donor_header": "  Donor header  : {size} bytes",
    "heal_dry_run_entropy_start": "  Entropy start : offset {offset}",
    "heal_dry_run_header": "[DRY-RUN] Would heal: {src} -> {dst}",
    "heal_dry_run_total_size": "  Total size    : {size}",
    "heal_dry_run_valid_jpeg_no": "  Valid JPEG    : No (warnings: {warnings})",
    "heal_dry_run_valid_jpeg_yes": "  Valid JPEG    : Yes",
    "heal_error_backup_exists": "Error: Backup file already exists: {path}. Use --force to overwrite.",
    "heal_error_broken_not_found": "Error: Broken file does not exist: {path}",
    "heal_error_broken_read_failed": "Error reading broken file: {error}",
    "heal_error_destination_exists": "Error: Destination file already exists: {path}. Use --force to overwrite.",
    "heal_error_donor_not_found": "Error: Donor file does not exist: {path}",
    "heal_error_donor_parse_failed": "Error: Failed to parse donor header from {name}: {error}",
    "heal_error_no_live_entropy": "Error: No live entropy data found in {name} (file is 100% TRIM-zero or corrupted).",
    "heal_error_splice_failed": "Error splicing donor header: {error}",
    "heal_error_write_failed": "Error writing output file: {error}",
    "heal_success_backup": "  Backup     : {name}",
    "heal_success_geometry": "  Geometry   : {geometry}",
    "heal_success_header": "[HEALED] {src} -> {dst}",
    "heal_success_total_size": "  Total size : {size}",
    "heal_val_unknown_resolution": "unknown resolution",
    "metric.carved_dry_run": "Carved (dry-run)",
    "metric.carved_success": "Carved previews",
    "metric.disk_freed": "Disk space freed",
    "metric.errors": "Errors",
    "metric.exif_thumbnails": "  - EXIF thumbnails",
    "metric.healed_dry_run": "Healed (dry-run)",
    "metric.healed_success": "Healed successfully",
    "metric.mpf_previews": "  - MPF Full HD previews",
    "metric.quarantined_dry_run": "Quarantined (dry-run)",
    "metric.quarantined_moved": "Quarantined (moved)",
    "metric.raw_carved": "  - Raw stream carved",
    "metric.relocated_dry_run": "Relocated (dry-run)",
    "metric.relocated_moved": "Relocated to quarantine",
    "metric.report_items": "Report items",
    "metric.skipped_missing_or_exists": "Skipped (missing / exists)",
    "metric.skipped_no_donor_or_exists": "Skipped (no donor / file exists)",
    "metric.skipped_no_preview_or_exists": "Skipped (no preview / exists)",
    "metric.total_candidates": "Total candidates",
    "metric.total_extracted_volume": "Total extracted volume",
    "metric.total_restored": "Total data restored",
    "metric.total_scanned": "Total scanned files",
    "metric_carved_dry_run": "Carved (dry-run)",
    "metric_carved_success": "Carved previews",
    "metric_disk_freed": "Disk space freed",
    "metric_errors": "Errors",
    "metric_exif_thumbnails": "  - EXIF thumbnails",
    "metric_healed_dry_run": "Healed (dry-run)",
    "metric_healed_success": "Healed successfully",
    "metric_mpf_previews": "  - MPF Full HD previews",
    "metric_quarantined_dry_run": "Quarantined (dry-run)",
    "metric_quarantined_moved": "Quarantined (moved)",
    "metric_raw_carved": "  - Raw stream carved",
    "metric_relocated_dry_run": "Relocated (dry-run)",
    "metric_relocated_moved": "Relocated to quarantine",
    "metric_report_items": "Report items",
    "metric_skipped_missing_or_exists": "Skipped (missing / exists)",
    "metric_skipped_no_donor_or_exists": "Skipped (no donor / file exists)",
    "metric_skipped_no_preview_or_exists": "Skipped (no preview / exists)",
    "metric_total_candidates": "Total candidates",
    "metric_total_extracted_volume": "Total extracted volume",
    "metric_total_restored": "Total data restored",
    "metric_total_scanned": "Total scanned files",
    "progress.auditing_files": "Auditing files",
    "progress.batch_healing": "Batch healing",
    "progress.carving_previews": "Carving previews",
    "progress.default": "Processing",
    "progress.quarantining": "Quarantining",
    "progress_auditing_files": "Auditing files",
    "progress_batch_healing": "Batch healing",
    "progress_carving_previews": "Carving previews",
    "progress_default": "Processing",
    "progress_quarantining": "Quarantining",
    "quarantine.arg.dest": "Quarantine destination directory",
    "quarantine.arg.dry_run": "Simulate moves without moving files",
    "quarantine.arg.force": "Force overwrite if destination already exists",
    "quarantine.arg.quiet": "Suppress progress and summary output",
    "quarantine.arg.report": "JSON report path generated by triage",
    "quarantine.error.report_not_found": "Error: Report file not found: {path}",
    "quarantine.error.report_read_failed": "Error reading JSON report: {error}",
    "quarantine.info.found_trim_zeros": "Found {count} TRIM-zero files ({size}) to quarantine.",
    "quarantine.info.no_trim_zeros": "No TRIM-zero files found in report.",
    "quarantine.warn.failed_move": "Warning: Failed to move {name}: {error}",
    "quarantine_arg_dest": "Quarantine destination directory",
    "quarantine_arg_dry_run": "Simulate moves without moving files",
    "quarantine_arg_force": "Force overwrite if destination already exists",
    "quarantine_arg_quiet": "Suppress progress and summary output",
    "quarantine_arg_report": "JSON report path generated by triage",
    "quarantine_error_report_not_found": "Error: Report file not found: {path}",
    "quarantine_error_report_read_failed": "Error reading JSON report: {error}",
    "quarantine_info_found_trim_zeros": "Found {count} TRIM-zero files ({size}) to quarantine.",
    "quarantine_info_no_trim_zeros": "No TRIM-zero files found in report.",
    "quarantine_warn_failed_move": "Warning: Failed to move {name}: {error}",
    "status.empty": "Empty files (0 bytes)",
    "status.error": "File access errors",
    "status.healed_candidate": "Heal candidates (live stream)",
    "status.other": "Other formats / Unknown",
    "status.trim_zero": "TRIM zero (erased 0x00)",
    "status.valid": "Valid / Intact photos",
    "status_empty": "Empty files (0 bytes)",
    "status_enum.CORRUPTED": "CORRUPTED",
    "status_enum.DAMAGED": "DAMAGED",
    "status_enum.EMPTY": "EMPTY",
    "status_enum.HEALTHY": "HEALTHY",
    "status_enum.NON_IMAGE": "NON_IMAGE",
    "status_enum.ZERO_FILL": "ZERO_FILL",
    "status_enum_CORRUPTED": "CORRUPTED",
    "status_enum_DAMAGED": "DAMAGED",
    "status_enum_EMPTY": "EMPTY",
    "status_enum_HEALTHY": "HEALTHY",
    "status_enum_NON_IMAGE": "NON_IMAGE",
    "status_enum_ZERO_FILL": "ZERO_FILL",
    "status_error": "File access errors",
    "status_healed_candidate": "Heal candidates (live stream)",
    "status_other": "Other formats / Unknown",
    "status_trim_zero": "TRIM zero (erased 0x00)",
    "status_valid": "Valid / Intact photos",
    "table.empty": "=== {title} (Empty) ===",
    "table.header.category_status": "Category / Status",
    "table.header.count": "Count",
    "table.header.files": "Files",
    "table.header.metric": "Metric",
    "table.header.size": "Size",
    "table.title.batch_heal": "PHOTO HEALER — BATCH HEAL SUMMARY",
    "table.title.carve": "PHOTO HEALER — CARVER SUMMARY",
    "table.title.quarantine": "PHOTO HEALER — QUARANTINE SUMMARY",
    "table.title.triage": "PHOTO HEALER — TRIAGE REPORT",
    "table_empty": "=== {title} (Empty) ===",
    "table_header_category_status": "Category / Status",
    "table_header_count": "Count",
    "table_header_files": "Files",
    "table_header_metric": "Metric",
    "table_header_size": "Size",
    "table_title_batch_heal": "PHOTO HEALER — BATCH HEAL SUMMARY",
    "table_title_carve": "PHOTO HEALER — CARVER SUMMARY",
    "table_title_quarantine": "PHOTO HEALER — QUARANTINE SUMMARY",
    "table_title_triage": "PHOTO HEALER — TRIAGE REPORT",
    "triage.arg.dry_run": "Simulate quarantine without moving files",
    "triage.arg.ext": "File extensions to include (default: all image types)",
    "triage.arg.force": "Overwrite existing files in quarantine destination",
    "triage.arg.path": "Directory path to scan and audit",
    "triage.arg.quarantine": "Move TRIM-zero files to quarantine directory",
    "triage.arg.quiet": "Suppress progress bars and summary tables",
    "triage.arg.report": "Save triage report to specified JSON file",
    "triage.error.not_a_directory": "Error: Target path is not a directory: {path}",
    "triage.info.candidates_shortlist": "Heal candidates shortlist: {path} ({count} items)",
    "triage.info.ext_filter": "Extensions filter : {exts}",
    "triage.info.report_saved": "Report saved: {path}",
    "triage.info.scanning": "Scanning directory: {path}",
    "triage.warn.failed_move": "Warning: Failed moving {name}: {error}",
    "triage_arg_dry_run": "Simulate quarantine without moving files",
    "triage_arg_ext": "File extensions to include (default: all image types)",
    "triage_arg_force": "Overwrite existing files in quarantine destination",
    "triage_arg_path": "Directory path to scan and audit",
    "triage_arg_quarantine": "Move TRIM-zero files to quarantine directory",
    "triage_arg_quiet": "Suppress progress bars and summary tables",
    "triage_arg_report": "Save triage report to specified JSON file",
    "triage_error_not_a_directory": "Error: Target path is not a directory: {path}",
    "triage_info_candidates_shortlist": "Heal candidates shortlist: {path} ({count} items)",
    "triage_info_ext_filter": "Extensions filter : {exts}",
    "triage_info_report_saved": "Report saved: {path}",
    "triage_info_scanning": "Scanning directory: {path}",
    "triage_warn_failed_move": "Warning: Failed moving {name}: {error}",
    "units.b": "B",
    "units.gb": "GB",
    "units.kb": "KB",
    "units.mb": "MB",
    "units.tb": "TB",
    "units_b": "B",
    "units_gb": "GB",
    "units_kb": "KB",
    "units_mb": "MB",
    "units_tb": "TB",
    "cmd.update_check.desc": "Query GitHub Releases API to check if a new version is available.",
    "cmd.update_check.help": "Check for newer Photo Healer releases on GitHub",
    "cmd_update_check_desc": "Query GitHub Releases API to check if a new version is available.",
    "cmd_update_check_help": "Check for newer Photo Healer releases on GitHub",
    "update_check.arg.force": "Force check ignoring 24-hour cooldown",
    "update_check.arg.quiet": "Suppress output if up to date",
    "update_check_arg_force": "Force check ignoring 24-hour cooldown",
    "update_check_arg_quiet": "Suppress output if up to date",
    "updater.checking": "Checking for updates...",
    "updater.error": "Could not check for updates: {error}",
    "updater.new_version_notice": "A new version {version} is available. Download: {url}",
    "updater.throttled": "Update check skipped (checked within last 24 hours).",
    "updater.up_to_date": "You are running the latest version ({version}).",
    "updater.update_available": "Update Available",
    "updater_checking": "Checking for updates...",
    "updater_error": "Could not check for updates: {error}",
    "updater_new_version_notice": "A new version {version} is available. Download: {url}",
    "updater_throttled": "Update check skipped (checked within last 24 hours).",
    "updater_up_to_date": "You are running the latest version ({version}).",
    "updater_update_available": "Update Available"
},
    "ru": {
    "batch_heal.arg.auto_donor": "Автоматически искать здорового донора в каталоге",
    "batch_heal.arg.donor": "Явный файл донора для использования для всех кандидатов",
    "batch_heal.arg.dry_run": "Симулировать пакетное восстановление без записи файлов",
    "batch_heal.arg.folder": "Каталог с поврежденными фотографиями",
    "batch_heal.arg.force": "Принудительно перезаписать существующие восстановленные файлы или копии",
    "batch_heal.arg.inplace": "Заменить исходные файлы на месте (создает копии .bak)",
    "batch_heal.arg.output": "Каталог для восстановленных фотографий",
    "batch_heal.arg.quiet": "Скрыть индикаторы прогресса и сводку",
    "batch_heal.error.donor_not_found": "Ошибка: Указанный файл донора не существует: {path}",
    "batch_heal.error.folder_not_found": "Ошибка: Каталог не существует: {folder}",
    "batch_heal.info.found_candidates": "Найдено кандидатов на восстановление: {count}. Запуск пакетного восстановления...",
    "batch_heal.info.no_candidates": "В каталоге {folder} не найдено поврежденных кандидатов.",
    "batch_heal.info.scanning": "Поиск кандидатов на восстановление в: {folder}",
    "batch_heal_arg_auto_donor": "Автоматически искать здорового донора в каталоге",
    "batch_heal_arg_donor": "Явный файл донора для использования для всех кандидатов",
    "batch_heal_arg_dry_run": "Симулировать пакетное восстановление без записи файлов",
    "batch_heal_arg_folder": "Каталог с поврежденными фотографиями",
    "batch_heal_arg_force": "Принудительно перезаписать существующие восстановленные файлы или копии",
    "batch_heal_arg_inplace": "Заменить исходные файлы на месте (создает копии .bak)",
    "batch_heal_arg_output": "Каталог для восстановленных фотографий",
    "batch_heal_arg_quiet": "Скрыть индикаторы прогресса и сводку",
    "batch_heal_error_donor_not_found": "Ошибка: Указанный файл донора не существует: {path}",
    "batch_heal_error_folder_not_found": "Ошибка: Каталог не существует: {folder}",
    "batch_heal_info_found_candidates": "Найдено кандидатов на восстановление: {count}. Запуск пакетного восстановления...",
    "batch_heal_info_no_candidates": "В каталоге {folder} не найдено поврежденных кандидатов.",
    "batch_heal_info_scanning": "Поиск кандидатов на восстановление в: {folder}",
    "carve.arg.dest": "Каталог для извлеченных превью (по умолчанию: _Previews)",
    "carve.arg.dry_run": "Симулировать извлечение без записи файлов",
    "carve.arg.force": "Принудительно перезаписывать существующие превью",
    "carve.arg.path": "Путь к файлу или папке для извлечения превью",
    "carve.arg.quiet": "Скрыть прогресс и сводку",
    "carve.dry_run.destination": "  Назначение : {name}",
    "carve.dry_run.header": "[ТЕСТ] Извлечение превью из {name}:",
    "carve.dry_run.resolution": "  Разрешение : {resolution}",
    "carve.dry_run.size": "  Размер     : {size}",
    "carve.dry_run.type": "  Тип        : {type}",
    "carve.error.output_exists": "Ошибка: Выходной файл уже существует: {path}. Используйте --force для перезаписи.",
    "carve.error.path_not_found": "Ошибка: Целевой путь не существует: {path}",
    "carve.error.write_failed": "Ошибка записи извлеченного превью: {error}",
    "carve.info.no_images": "В {path} не найдено файлов изображений.",
    "carve.info.no_preview": "В {name} не найдено встроенных превью или миниатюр",
    "carve.info.scanning": "Сканирование {count} файлов на наличие встроенных превью...",
    "carve.success.header": "[ИЗВЛЕЧЕНО] {src} -> {dst}",
    "carve_arg_dest": "Каталог для извлеченных превью (по умолчанию: _Previews)",
    "carve_arg_dry_run": "Симулировать извлечение без записи файлов",
    "carve_arg_force": "Принудительно перезаписывать существующие превью",
    "carve_arg_path": "Путь к файлу или папке для извлечения превью",
    "carve_arg_quiet": "Скрыть прогресс и сводку",
    "carve_dry_run_destination": "  Назначение : {name}",
    "carve_dry_run_header": "[ТЕСТ] Извлечение превью из {name}:",
    "carve_dry_run_resolution": "  Разрешение : {resolution}",
    "carve_dry_run_size": "  Размер     : {size}",
    "carve_dry_run_type": "  Тип        : {type}",
    "carve_error_output_exists": "Ошибка: Выходной файл уже существует: {path}. Используйте --force для перезаписи.",
    "carve_error_path_not_found": "Ошибка: Целевой путь не существует: {path}",
    "carve_error_write_failed": "Ошибка записи извлеченного превью: {error}",
    "carve_info_no_images": "В {path} не найдено файлов изображений.",
    "carve_info_no_preview": "В {name} не найдено встроенных превью или миниатюр",
    "carve_info_scanning": "Сканирование {count} файлов на наличие встроенных превью...",
    "carve_success_header": "[ИЗВЛЕЧЕНО] {src} -> {dst}",
    "classify.note.header_read_error": "Ошибка чтения заголовка: {error}",
    "classify.note.intact_magic": "Корректная сигнатура файла",
    "classify.note.read_error": "Ошибка чтения: {error}",
    "classify.note.stat_error": "Ошибка stat: {error}",
    "classify.note.trim_candidate": "Заголовок обнулен TRIM ({count} байт), живой поток со смещения {offset}",
    "classify.note.trim_zero": "100% нули, стертые TRIM ({size})",
    "classify.note.unexpected_magic": "Неожиданная сигнатура: {magic}",
    "classify.note.unknown_magic": "Неизвестная сигнатура для расширения {ext}",
    "classify.note.zero_length": "Файл нулевой длины",
    "classify_note_header_read_error": "Ошибка чтения заголовка: {error}",
    "classify_note_intact_magic": "Корректная сигнатура файла",
    "classify_note_read_error": "Ошибка чтения: {error}",
    "classify_note_stat_error": "Ошибка stat: {error}",
    "classify_note_trim_candidate": "Заголовок обнулен TRIM ({count} байт), живой поток со смещения {offset}",
    "classify_note_trim_zero": "100% нули, стертые TRIM ({size})",
    "classify_note_unexpected_magic": "Неожиданная сигнатура: {magic}",
    "classify_note_unknown_magic": "Неизвестная сигнатура для расширения {ext}",
    "classify_note_zero_length": "Файл нулевой длины",
    "cli.arg.lang": "Язык интерфейса (en, ru; по умолчанию: автоопределение системы)",
    "cli.arg.no_banner": "Отключить начальную заставку и ASCII-баннер",
    "cli.arg.verbose": "Включить подробный вывод и журнал криминалистического анализа",
    "cli.arg.version": "Показать версию программы и выйти",
    "cli.description": "Photo Healer - Инструмент криминалистического восстановления фотоархивов после повреждений SSD TRIM.",
    "cli.epilog": "Используйте 'photo-healer <команда> --help' для получения справки по каждой подкоманде.",
    "cli.metavar.command": "<команда>",
    "cli_arg_lang": "Язык интерфейса (en, ru; по умолчанию: автоопределение системы)",
    "cli_arg_no_banner": "Отключить начальную заставку и ASCII-баннер",
    "cli_arg_verbose": "Включить подробный вывод и журнал криминалистического анализа",
    "cli_arg_version": "Показать версию программы и выйти",
    "cli_description": "Photo Healer - Инструмент криминалистического восстановления фотоархивов после повреждений SSD TRIM.",
    "cli_epilog": "Используйте 'photo-healer <команда> --help' для получения справки по каждой подкоманде.",
    "cli_metavar_command": "<команда>",
    "cmd.batch_heal.desc": "Сканировать каталог на поврежденные TRIM кандидаты и восстановить их с донорскими заголовками.",
    "cmd.batch_heal.help": "Пакетное восстановление серии фото с автоподбором донора",
    "cmd.batch_heal.name": "batch-heal",
    "cmd.carve.desc": "Извлечь встроенные миниатюры JPEG, полноразмерные превью MPF или сырые потоки изображений.",
    "cmd.carve.help": "Извлечь встроенные превью (MPF, миниатюры EXIF, сырые потоки)",
    "cmd.carve.name": "carve",
    "cmd.heal.desc": "Пересадить маркеры JPEG донора (DQT, DHT, SOF, SOS) в поврежденный файл.",
    "cmd.heal.help": "Восстановить фотографию с помощью донорского заголовка JPEG",
    "cmd.heal.name": "heal",
    "cmd.quarantine.desc": "Переместить стертые TRIM файлы (0x00) из отчета triage для очистки архива.",
    "cmd.quarantine.help": "Безопасно переместить невосстановимые файлы с нулями TRIM в карантин",
    "cmd.quarantine.name": "quarantine",
    "cmd.triage.desc": "Высокоскоростной потоковый анализ архивов изображений и классификация повреждений TRIM.",
    "cmd.triage.help": "Сканировать и проверить каталог на повреждения TRIM и кандидатов",
    "cmd.triage.name": "triage",
    "cmd.gui.desc": "Запуск графического интерфейса с диагностикой, лечением и галереей превью.",
    "cmd.gui.help": "Запуск графического интерфейса Photo Healer",
    "cmd.gui.name": "gui",
    "gui.arg.folder": "Начальная папка архива для открытия в GUI",
    "cmd_batch_heal_desc": "Сканировать каталог на поврежденные TRIM кандидаты и восстановить их с донорскими заголовками.",
    "cmd_batch_heal_help": "Пакетное восстановление серии фото с автоподбором донора",
    "cmd_batch_heal_name": "batch-heal",
    "cmd_carve_desc": "Извлечь встроенные миниатюры JPEG, полноразмерные превью MPF или сырые потоки изображений.",
    "cmd_carve_help": "Извлечь встроенные превью (MPF, миниатюры EXIF, сырые потоки)",
    "cmd_carve_name": "carve",
    "cmd_heal_desc": "Пересадить маркеры JPEG донора (DQT, DHT, SOF, SOS) в поврежденный файл.",
    "cmd_heal_help": "Восстановить фотографию с помощью донорского заголовка JPEG",
    "cmd_heal_name": "heal",
    "cmd_quarantine_desc": "Переместить стертые TRIM файлы (0x00) из отчета triage для очистки архива.",
    "cmd_quarantine_help": "Безопасно переместить невосстановимые файлы с нулями TRIM в карантин",
    "cmd_quarantine_name": "quarantine",
    "cmd_triage_desc": "Высокоскоростной потоковый анализ архивов изображений и классификация повреждений TRIM.",
    "cmd_triage_help": "Сканировать и проверить каталог на повреждения TRIM и кандидатов",
    "cmd_triage_name": "triage",
    "heal.arg.broken_file": "Путь к поврежденному изображению JPEG",
    "heal.arg.donor": "Путь к целому донорскому изображению JPEG",
    "heal.arg.dry_run": "Симулировать восстановление без записи на диск",
    "heal.arg.force": "Принудительно перезаписать существующий файл назначения или копию",
    "heal.arg.inplace": "Заменить исходный файл на месте (создает копию .bak)",
    "heal.arg.output": "Путь к выходному файлу (по умолчанию: <имя>_HEALED.jpg)",
    "heal.arg.quiet": "Скрыть прогресс и информационные сообщения",
    "heal.dry_run.donor_header": "  Заголовок донора: {size} байт",
    "heal.dry_run.entropy_start": "  Начало энтропии : смещение {offset}",
    "heal.dry_run.header": "[ТЕСТ] Восстановление: {src} -> {dst}",
    "heal.dry_run.total_size": "  Общий размер  : {size}",
    "heal.dry_run.valid_jpeg_no": "  Валидный JPEG : Нет (предупреждения: {warnings})",
    "heal.dry_run.valid_jpeg_yes": "  Валидный JPEG : Да",
    "heal.error.backup_exists": "Ошибка: Файл копии уже существует: {path}. Используйте --force для перезаписи.",
    "heal.error.broken_not_found": "Ошибка: Поврежденный файл не существует: {path}",
    "heal.error.broken_read_failed": "Ошибка чтения поврежденного файла: {error}",
    "heal.error.destination_exists": "Ошибка: Файл назначения уже существует: {path}. Используйте --force для перезаписи.",
    "heal.error.donor_not_found": "Ошибка: Файл донора не существует: {path}",
    "heal.error.donor_parse_failed": "Ошибка: Не удалось разобрать заголовок донора из {name}: {error}",
    "heal.error.no_live_entropy": "Ошибка: В {name} не найдены живые данные энтропии (файл полностью очищен TRIM или разрушен).",
    "heal.error.splice_failed": "Ошибка соединения с заголовком донора: {error}",
    "heal.error.write_failed": "Ошибка записи выходного файла: {error}",
    "heal.success.backup": "  Копия      : {name}",
    "heal.success.geometry": "  Разрешение : {geometry}",
    "heal.success.header": "[ВОССТАНОВЛЕНО] {src} -> {dst}",
    "heal.success.total_size": "  Общий размер: {size}",
    "heal.val.unknown_resolution": "неизвестное разрешение",
    "heal_arg_broken_file": "Путь к поврежденному изображению JPEG",
    "heal_arg_donor": "Путь к целому донорскому изображению JPEG",
    "heal_arg_dry_run": "Симулировать восстановление без записи на диск",
    "heal_arg_force": "Принудительно перезаписать существующий файл назначения или копию",
    "heal_arg_inplace": "Заменить исходный файл на месте (создает копию .bak)",
    "heal_arg_output": "Путь к выходному файлу (по умолчанию: <имя>_HEALED.jpg)",
    "heal_arg_quiet": "Скрыть прогресс и информационные сообщения",
    "heal_dry_run_donor_header": "  Заголовок донора: {size} байт",
    "heal_dry_run_entropy_start": "  Начало энтропии : смещение {offset}",
    "heal_dry_run_header": "[ТЕСТ] Восстановление: {src} -> {dst}",
    "heal_dry_run_total_size": "  Общий размер  : {size}",
    "heal_dry_run_valid_jpeg_no": "  Валидный JPEG : Нет (предупреждения: {warnings})",
    "heal_dry_run_valid_jpeg_yes": "  Валидный JPEG : Да",
    "heal_error_backup_exists": "Ошибка: Файл копии уже существует: {path}. Используйте --force для перезаписи.",
    "heal_error_broken_not_found": "Ошибка: Поврежденный файл не существует: {path}",
    "heal_error_broken_read_failed": "Ошибка чтения поврежденного файла: {error}",
    "heal_error_destination_exists": "Ошибка: Файл назначения уже существует: {path}. Используйте --force для перезаписи.",
    "heal_error_donor_not_found": "Ошибка: Файл донора не существует: {path}",
    "heal_error_donor_parse_failed": "Ошибка: Не удалось разобрать заголовок донора из {name}: {error}",
    "heal_error_no_live_entropy": "Ошибка: В {name} не найдены живые данные энтропии (файл полностью очищен TRIM или разрушен).",
    "heal_error_splice_failed": "Ошибка соединения с заголовком донора: {error}",
    "heal_error_write_failed": "Ошибка записи выходного файла: {error}",
    "heal_success_backup": "  Копия      : {name}",
    "heal_success_geometry": "  Разрешение : {geometry}",
    "heal_success_header": "[ВОССТАНОВЛЕНО] {src} -> {dst}",
    "heal_success_total_size": "  Общий размер: {size}",
    "heal_val_unknown_resolution": "неизвестное разрешение",
    "metric.carved_dry_run": "Извлечено превью (тест)",
    "metric.carved_success": "Извлечено превью",
    "metric.disk_freed": "Освобождено места на диске",
    "metric.errors": "Ошибки",
    "metric.exif_thumbnails": "  - Миниатюры EXIF",
    "metric.healed_dry_run": "Восстановлено (тест)",
    "metric.healed_success": "Успешно восстановлено",
    "metric.mpf_previews": "  - MPF Full HD превью",
    "metric.quarantined_dry_run": "Помещено в карантин (тест)",
    "metric.quarantined_moved": "Помещено в карантин (перемещено)",
    "metric.raw_carved": "  - Извлечено из сырого потока",
    "metric.relocated_dry_run": "Перемещено (тест)",
    "metric.relocated_moved": "Перемещено в карантин",
    "metric.report_items": "Файлов в отчете",
    "metric.skipped_missing_or_exists": "Пропущено (отсутствует / существует)",
    "metric.skipped_no_donor_or_exists": "Пропущено (нет донора / файл существует)",
    "metric.skipped_no_preview_or_exists": "Пропущено (нет превью / существует)",
    "metric.total_candidates": "Всего кандидатов",
    "metric.total_extracted_volume": "Общий объем извлеченных данных",
    "metric.total_restored": "Всего восстановлено данных",
    "metric.total_scanned": "Всего просканировано файлов",
    "metric_carved_dry_run": "Извлечено превью (тест)",
    "metric_carved_success": "Извлечено превью",
    "metric_disk_freed": "Освобождено места на диске",
    "metric_errors": "Ошибки",
    "metric_exif_thumbnails": "  - Миниатюры EXIF",
    "metric_healed_dry_run": "Восстановлено (тест)",
    "metric_healed_success": "Успешно восстановлено",
    "metric_mpf_previews": "  - MPF Full HD превью",
    "metric_quarantined_dry_run": "Помещено в карантин (тест)",
    "metric_quarantined_moved": "Помещено в карантин (перемещено)",
    "metric_raw_carved": "  - Извлечено из сырого потока",
    "metric_relocated_dry_run": "Перемещено (тест)",
    "metric_relocated_moved": "Перемещено в карантин",
    "metric_report_items": "Файлов в отчете",
    "metric_skipped_missing_or_exists": "Пропущено (отсутствует / существует)",
    "metric_skipped_no_donor_or_exists": "Пропущено (нет донора / файл существует)",
    "metric_skipped_no_preview_or_exists": "Пропущено (нет превью / существует)",
    "metric_total_candidates": "Всего кандидатов",
    "metric_total_extracted_volume": "Общий объем извлеченных данных",
    "metric_total_restored": "Всего восстановлено данных",
    "metric_total_scanned": "Всего просканировано файлов",
    "progress.auditing_files": "Анализ файлов",
    "progress.batch_healing": "Пакетное восстановление",
    "progress.carving_previews": "Извлечение превью",
    "progress.default": "Обработка",
    "progress.quarantining": "Перемещение в карантин",
    "progress_auditing_files": "Анализ файлов",
    "progress_batch_healing": "Пакетное восстановление",
    "progress_carving_previews": "Извлечение превью",
    "progress_default": "Обработка",
    "progress_quarantining": "Перемещение в карантин",
    "quarantine.arg.dest": "Каталог назначения карантина",
    "quarantine.arg.dry_run": "Симулировать перемещение без физического переноса файлов",
    "quarantine.arg.force": "Принудительно перезаписать, если файл назначения уже существует",
    "quarantine.arg.quiet": "Скрыть прогресс и сводку",
    "quarantine.arg.report": "Путь к JSON-отчету, сгенерированному командой triage",
    "quarantine.error.report_not_found": "Ошибка: Файл отчета не найден: {path}",
    "quarantine.error.report_read_failed": "Ошибка чтения JSON-отчета: {error}",
    "quarantine.info.found_trim_zeros": "Найдено файлов с нулями TRIM: {count} ({size}) для перемещения в карантин.",
    "quarantine.info.no_trim_zeros": "В отчете не найдены файлы с нулями TRIM.",
    "quarantine.warn.failed_move": "Предупреждение: Не удалось переместить {name}: {error}",
    "quarantine_arg_dest": "Каталог назначения карантина",
    "quarantine_arg_dry_run": "Симулировать перемещение без физического переноса файлов",
    "quarantine_arg_force": "Принудительно перезаписать, если файл назначения уже существует",
    "quarantine_arg_quiet": "Скрыть прогресс и сводку",
    "quarantine_arg_report": "Путь к JSON-отчету, сгенерированному командой triage",
    "quarantine_error_report_not_found": "Ошибка: Файл отчета не найден: {path}",
    "quarantine_error_report_read_failed": "Ошибка чтения JSON-отчета: {error}",
    "quarantine_info_found_trim_zeros": "Найдено файлов с нулями TRIM: {count} ({size}) для перемещения в карантин.",
    "quarantine_info_no_trim_zeros": "В отчете не найдены файлы с нулями TRIM.",
    "quarantine_warn_failed_move": "Предупреждение: Не удалось переместить {name}: {error}",
    "status.empty": "Пустые файлы (0 байт)",
    "status.error": "Ошибки доступа / чтения",
    "status.healed_candidate": "Кандидаты на восстановление (живой поток)",
    "status.other": "Другие форматы / неизвестные данные",
    "status.trim_zero": "Очищены TRIM (заполнены нулями 0x00)",
    "status.valid": "Целые / неповрежденные фото",
    "status_empty": "Пустые файлы (0 байт)",
    "status_enum.CORRUPTED": "ОШИБКА ЧТЕНИЯ",
    "status_enum.DAMAGED": "ПОВРЕЖДЕН (ВОССТАНОВИМ)",
    "status_enum.EMPTY": "ПУСТОЙ ФАЙЛ",
    "status_enum.HEALTHY": "НЕПОВРЕЖДЕН",
    "status_enum.NON_IMAGE": "ДРУГОЙ ФОРМАТ",
    "status_enum.ZERO_FILL": "СТЕРТ TRIM (НУЛИ)",
    "status_enum_CORRUPTED": "ОШИБКА ЧТЕНИЯ",
    "status_enum_DAMAGED": "ПОВРЕЖДЕН (ВОССТАНОВИМ)",
    "status_enum_EMPTY": "ПУСТОЙ ФАЙЛ",
    "status_enum_HEALTHY": "НЕПОВРЕЖДЕН",
    "status_enum_NON_IMAGE": "ДРУГОЙ ФОРМАТ",
    "status_enum_ZERO_FILL": "СТЕРТ TRIM (НУЛИ)",
    "status_error": "Ошибки доступа / чтения",
    "status_healed_candidate": "Кандидаты на восстановление (живой поток)",
    "status_other": "Другие форматы / неизвестные данные",
    "status_trim_zero": "Очищены TRIM (заполнены нулями 0x00)",
    "status_valid": "Целые / неповрежденные фото",
    "table.empty": "=== {title} (Пусто) ===",
    "table.header.category_status": "Категория / Статус",
    "table.header.count": "Количество",
    "table.header.files": "Файлы",
    "table.header.metric": "Метрика",
    "table.header.size": "Размер",
    "table.title.batch_heal": "PHOTO HEALER — ИТОГИ ПАКЕТНОГО ВОССТАНОВЛЕНИЯ",
    "table.title.carve": "PHOTO HEALER — ИТОГИ ИЗВЛЕЧЕНИЯ ПРЕВЬЮ",
    "table.title.quarantine": "PHOTO HEALER — ИТОГИ КАРАНТИНА",
    "table.title.triage": "PHOTO HEALER — ОТЧЕТ О ПЕРВИЧНОМ АНАЛИЗЕ (TRIAGE)",
    "table_empty": "=== {title} (Пусто) ===",
    "table_header_category_status": "Категория / Статус",
    "table_header_count": "Количество",
    "table_header_files": "Файлы",
    "table_header_metric": "Метрика",
    "table_header_size": "Размер",
    "table_title_batch_heal": "PHOTO HEALER — ИТОГИ ПАКЕТНОГО ВОССТАНОВЛЕНИЯ",
    "table_title_carve": "PHOTO HEALER — ИТОГИ ИЗВЛЕЧЕНИЯ ПРЕВЬЮ",
    "table_title_quarantine": "PHOTO HEALER — ИТОГИ КАРАНТИНА",
    "table_title_triage": "PHOTO HEALER — ОТЧЕТ О ПЕРВИЧНОМ АНАЛИЗЕ (TRIAGE)",
    "triage.arg.dry_run": "Симулировать помещение в карантин без перемещения файлов",
    "triage.arg.ext": "Расширения файлов для включения (по умолчанию: все типы изображений)",
    "triage.arg.force": "Перезаписывать существующие файлы в каталоге карантина",
    "triage.arg.path": "Путь к каталогу для сканирования и анализа",
    "triage.arg.quarantine": "Переместить файлы с нулями TRIM в каталог карантина",
    "triage.arg.quiet": "Скрыть индикаторы прогресса и итоговые таблицы",
    "triage.arg.report": "Сохранить отчет анализа в указанный JSON-файл",
    "triage.error.not_a_directory": "Ошибка: Целевой путь не является каталогом: {path}",
    "triage.info.candidates_shortlist": "Список кандидатов на восстановление: {path} ({count} шт.)",
    "triage.info.ext_filter": "Фильтр расширений : {exts}",
    "triage.info.report_saved": "Отчет сохранен: {path}",
    "triage.info.scanning": "Сканирование каталога: {path}",
    "triage.warn.failed_move": "Предупреждение: Не удалось переместить {name}: {error}",
    "triage_arg_dry_run": "Симулировать помещение в карантин без перемещения файлов",
    "triage_arg_ext": "Расширения файлов для включения (по умолчанию: все типы изображений)",
    "triage_arg_force": "Перезаписывать существующие файлы в каталоге карантина",
    "triage_arg_path": "Путь к каталогу для сканирования и анализа",
    "triage_arg_quarantine": "Переместить файлы с нулями TRIM в каталог карантина",
    "triage_arg_quiet": "Скрыть индикаторы прогресса и итоговые таблицы",
    "triage_arg_report": "Сохранить отчет анализа в указанный JSON-файл",
    "triage_error_not_a_directory": "Ошибка: Целевой путь не является каталогом: {path}",
    "triage_info_candidates_shortlist": "Список кандидатов на восстановление: {path} ({count} шт.)",
    "triage_info_ext_filter": "Фильтр расширений : {exts}",
    "triage_info_report_saved": "Отчет сохранен: {path}",
    "triage_info_scanning": "Сканирование каталога: {path}",
    "triage_warn_failed_move": "Предупреждение: Не удалось переместить {name}: {error}",
    "units.b": "Б",
    "units.gb": "ГБ",
    "units.kb": "КБ",
    "units.mb": "МБ",
    "units.tb": "ТБ",
    "units_b": "Б",
    "units_gb": "ГБ",
    "units_kb": "КБ",
    "units_mb": "МБ",
    "units_tb": "ТБ",
    "cmd.update_check.desc": "Запрос к GitHub Releases API для проверки наличия свежей версии.",
    "cmd.update_check.help": "Проверить наличие обновлений Photo Healer на GitHub",
    "cmd_update_check_desc": "Запрос к GitHub Releases API для проверки наличия свежей версии.",
    "cmd_update_check_help": "Проверить наличие обновлений Photo Healer на GitHub",
    "update_check.arg.force": "Принудительная проверка без учета 24-часового кулдауна",
    "update_check.arg.quiet": "Подавить вывод, если установлена последняя версия",
    "update_check_arg_force": "Принудительная проверка без учета 24-часового кулдауна",
    "update_check_arg_quiet": "Подавить вывод, если установлена последняя версия",
    "updater.checking": "Проверка обновлений...",
    "updater.error": "Не удалось проверить обновления: {error}",
    "updater.new_version_notice": "Доступна новая версия {version}. Скачать: {url}",
    "updater.throttled": "Проверка обновлений пропущена (проверялась в течение последних 24 часов).",
    "updater.up_to_date": "У вас установлена самая свежая версия ({version}).",
    "updater.update_available": "Доступно обновление",
    "updater_checking": "Проверка обновлений...",
    "updater_error": "Не удалось проверить обновления: {error}",
    "updater_new_version_notice": "Доступна новая версия {version}. Скачать: {url}",
    "updater_throttled": "Проверка обновлений пропущена (проверялась в течение последних 24 часов).",
    "updater_up_to_date": "У вас установлена самая свежая версия ({version}).",
    "updater_update_available": "Доступно обновление"
}
}


def normalize_language(raw_code: str | None) -> str:
    """Normalize language / locale code to supported language ('en' or 'ru')."""
    if not raw_code:
        return DEFAULT_LANGUAGE
    code = str(raw_code).strip().lower()
    if code.startswith("ru") or code == "russian":
        return "ru"
    return "en"


def detect_language() -> str:
    """Detect preferred interface language following priority hierarchy.

    Hierarchy:
      1. PHOTO_HEALER_LANG environment variable
      2. LC_ALL, LC_MESSAGES, LANG environment variables
      3. locale.getlocale() / locale.getdefaultlocale()
      4. Windows API (GetUserDefaultUILanguage) and Registry (LocaleName)
      5. Fallback to DEFAULT_LANGUAGE ('en')
    """
    # 1. Dedicated application environment variable
    env_lang = os.environ.get("PHOTO_HEALER_LANG")
    if env_lang:
        return normalize_language(env_lang)

    # 2. Standard POSIX environment variables
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            norm = normalize_language(val)
            if norm == "ru":
                return "ru"
            if val.strip().lower().startswith("en"):
                return "en"

    # 3. Python locale module
    try:
        loc = locale.getlocale()
        if loc and loc[0]:
            norm = normalize_language(loc[0])
            if norm == "ru":
                return "ru"
            if str(loc[0]).strip().lower().startswith("en"):
                return "en"
    except Exception:
        pass

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            defloc = locale.getdefaultlocale()
        if defloc and defloc[0]:
            norm = normalize_language(defloc[0])
            if norm == "ru":
                return "ru"
            if str(defloc[0]).strip().lower().startswith("en"):
                return "en"
    except Exception:
        pass

    # 4. Windows native API / Registry
    if sys.platform == "win32":
        try:
            if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "kernel32"):
                lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
                if (lang_id & 0xFF) == 0x19:
                    return "ru"
                elif lang_id != 0:
                    return "en"
        except Exception:
            pass

        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
                locale_name, _ = winreg.QueryValueEx(key, "LocaleName")
                if locale_name:
                    return normalize_language(locale_name)
        except Exception:
            pass

    return DEFAULT_LANGUAGE


def detect_system_language() -> str:
    """Alias for detect_language for backward and API compatibility."""
    return detect_language()


def get_language() -> str:
    """Return currently active language code ('en' or 'ru')."""
    global _CURRENT_LANGUAGE
    if _CURRENT_LANGUAGE is None:
        _CURRENT_LANGUAGE = detect_language()
    return _CURRENT_LANGUAGE


def set_language(lang_code: str | None) -> str:
    """Explicitly set active language code."""
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = normalize_language(lang_code)
    return _CURRENT_LANGUAGE


def reset_language() -> None:
    """Reset active language to uninitialized state for fresh auto-detection."""
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = None


def t(key: str, default: str | None = None, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a message key into current or specified language with safe interpolation."""
    target_lang = normalize_language(lang) if lang is not None else get_language()

    translations = TRANSLATIONS.get(target_lang, TRANSLATIONS["en"])
    template = translations.get(key)
    if template is None and ("." in key or "_" in key):
        alt_key = key.replace(".", "_") if "." in key else key.replace("_", ".")
        template = translations.get(alt_key)

    if template is None:
        template = TRANSLATIONS["en"].get(key)
        if template is None and ("." in key or "_" in key):
            alt_key = key.replace(".", "_") if "." in key else key.replace("_", ".")
            template = TRANSLATIONS["en"].get(alt_key)

    if template is None:
        template = default if default is not None else key

    if not kwargs:
        return template

    try:
        return template.format(**kwargs)
    except KeyError:
        res = template
        for k, v in kwargs.items():
            res = res.replace(f"{{{k}}}", str(v))
        return res
    except Exception:
        return template


def ensure_windows_utf8() -> None:
    """Ensure sys.stdout and sys.stderr are configured for UTF-8 encoding in Windows consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


# Automatic execution on module load
ensure_windows_utf8()
