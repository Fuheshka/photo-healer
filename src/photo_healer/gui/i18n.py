# -*- coding: utf-8 -*-
"""Bilingual (English & Russian) UI localization for Photo Healer GUI.

Follows app-i18n-localization standard with zero-dependency lightweight dictionary,
automatic runtime system locale detection via QLocale / locale fallback to English,
and live QObject signal-based language switching without restarting the application.
"""

from __future__ import annotations

import locale
import os
from typing import Any

from PySide6.QtCore import QLocale, QObject, Signal

SUPPORTED_LANGUAGES: tuple[str, ...] = ("ru", "en")
DEFAULT_LANGUAGE: str = "en"


def detect_system_language() -> str:
    """Detect system language via PHOTO_HEALER_LANG, QLocale, or locale fallback.

    Returns:
        'ru' if Russian is detected, otherwise 'en'.
    """
    # 1. Environment variable override
    env_lang = os.environ.get("PHOTO_HEALER_LANG", "").strip().lower()
    if env_lang.startswith("ru"):
        return "ru"
    if env_lang.startswith("en"):
        return "en"

    # 2. QLocale detection
    try:
        sys_loc = QLocale.system()
        # Check language enum or locale name
        if sys_loc.language() == QLocale.Language.Russian:
            return "ru"
        name = sys_loc.name().lower()
        if name.startswith("ru"):
            return "ru"
    except Exception:
        pass

    # 3. Standard library locale fallback
    try:
        loc_tuple = locale.getlocale()
        if loc_tuple and loc_tuple[0]:
            if loc_tuple[0].lower().startswith("ru"):
                return "ru"
    except Exception:
        pass

    try:
        def_loc = locale.getdefaultlocale()
        if def_loc and def_loc[0]:
            if def_loc[0].lower().startswith("ru"):
                return "ru"
    except Exception:
        pass

    return DEFAULT_LANGUAGE


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # App & Window
        "app.title": "Photo Healer - SSD TRIM Photo Forensics",
        "app.subtitle": "Forensic repair and triage for damaged photo archives",
        # Language Switcher
        "lang.switch": "Language",
        "lang.en": "English",
        "lang.ru": "Русский",
        # Folder selection bar
        "folder.label": "Archive folder:",
        "folder.placeholder": "Select or drag and drop archive folder here...",
        "folder.browse": "Browse...",
        "folder.scan": "Scan",
        "folder.cancel": "Stop",
        "folder.dialog_title": "Select Archive Folder",
        # Tabs
        "tab.diagnostics": "Diagnostics",
        "tab.recovery": "Recovery",
        "tab.gallery": "Preview Gallery",
        # Status Bar & Metrics
        "status.ready": "Ready to scan",
        "status.scanning": "Scanning: {current} / {total} files ({filename})",
        "status.completed": "Scan completed: {count} files processed ({size})",
        "status.cancelled": "Scan stopped by user ({count} files processed)",
        "status.error": "Scan error: {error}",
        "metric.files": "Files: {count}",
        "metric.total_size": "Total size: {size}",
        "metric.status": "Status: {status}",
        # Table Columns
        "table.col.name": "Filename",
        "table.col.status": "Status",
        "table.col.size": "Size",
        "table.col.path": "Path",
        # Status Badges
        "status.healed_candidate": "Heal candidate",
        "status.trim_zero": "TRIM dummy",
        "status.valid": "Intact",
        "status.error": "Error",
        "status.empty": "Empty",
        "status.other": "Unknown",
        # Filter Buttons
        "filter.all": "All ({count})",
        "filter.candidates": "Candidates ({count})",
        "filter.dummies": "Dummies ({count})",
        "filter.intact": "Intact ({count})",
        # Quick Actions
        "action.quarantine": "Quarantine Dummies",
        "action.export_json": "Export Report (JSON)",
        # Quarantine Dialog
        "quarantine.title": "Quarantine TRIM Dummies",
        "quarantine.confirm_prompt": "Found {count} TRIM dummy files ({size}). Relocate them to quarantine folder?",
        "quarantine.dest_label": "Destination quarantine folder:",
        "quarantine.dry_run": "Safe simulation (dry-run)",
        "quarantine.browse": "Browse...",
        "quarantine.btn_ok": "Proceed",
        "quarantine.btn_cancel": "Cancel",
        "quarantine.no_items": "No TRIM dummy files found to quarantine.",
        "quarantine.success": "Quarantine complete! Moved: {moved}, Skipped: {skipped}, Space freed: {freed}",
        "quarantine.dry_run_success": "[Simulation] Would move: {moved} files. Space freed: {freed}",
        "quarantine.dest_dialog_title": "Select Quarantine Directory",
        # Export Report Dialog
        "export.title": "Save Triage Report",
        "export.filter": "JSON Files (*.json)",
        "export.success": "Triage report successfully saved to:\n{path}",
        "export.error": "Failed to save triage report: {error}",
        "export.no_data": "No scanned file data to export. Please scan an archive folder first.",
        # Placeholder Tabs
        "recovery.title": "Header Transplantation & Bitstream Resynchronization",
        "recovery.desc": "Select candidates on the Diagnostics tab to perform forensic repair with donor JPEGs.",
        "gallery.title": "Embedded Thumbnail & MPF Carver",
        "gallery.desc": "Quick visual inspection and extraction of preview streams from damaged raw data.",
        # Preview Gallery (Carve View)
        "carve.btn.extract": "Extract Previews",
        "carve.btn.stop": "Stop",
        "carve.btn.export_selected": "Export Selected",
        "carve.btn.export_all": "Export All Previews",
        "carve.btn.select_all": "Select All",
        "carve.btn.deselect_all": "Deselect All",
        "carve.filter.all": "All types",
        "carve.filter.mpf": "MPF Full HD",
        "carve.filter.exif": "EXIF thumbnails",
        "carve.filter.raw": "Raw streams",
        "carve.search.placeholder": "Filter by filename...",
        "carve.status.ready": "Ready to extract previews",
        "carve.status.scanning": "Extracting: {current} / {total} files ({filename})",
        "carve.status.found_summary": "{count} previews found ({size})",
        "carve.status.empty": "No embedded previews found yet. Click 'Extract Previews' to scan.",
        "carve.export.dialog_title": "Select Destination Folder for Previews",
        "carve.export.no_selection": "No previews selected for export.",
        "carve.export.success": "Successfully exported {count} previews to:\n{path}",
        "carve.badge.mpf": "APP2 MPF Full HD",
        "carve.badge.exif": "APP1 EXIF Thumb",
        "carve.badge.raw": "Raw Carved Stream",
        # Heal View & Queue
        "heal.queue.title": "Candidate Queue",
        "heal.queue.add": "Add Files...",
        "heal.queue.clear": "Clear",
        "heal.queue.count": "{count} candidates",
        "heal.queue.empty": "No candidates in queue. Drag & drop damaged files or scan an archive in Diagnostics.",
        "heal.queue.col_name": "File",
        "heal.queue.col_size": "Size",
        "heal.queue.col_offset": "Offset",
        # Donor Selection
        "heal.donor.title": "Donor JPEG File",
        "heal.donor.auto": "Auto-detect donor (folder / camera model)",
        "heal.donor.browse": "Browse Donor...",
        "heal.donor.drop_prompt": "Drop donor JPEG here or click 'Browse Donor...'",
        "heal.donor.none": "No donor selected",
        "heal.donor.auto_found": "Auto donor: {name} ({geometry})",
        "heal.donor.manual": "Manual donor: {name} ({geometry})",
        # Restoration Settings
        "heal.settings.title": "Restoration Settings",
        "heal.settings.pad_geometry": "Align frame geometry (pad_geometry)",
        "heal.settings.pad_geometry_tip": "Inject dummy restart intervals (RST) via StreamResync to compensate for lost scanlines and prevent vertical shift",
        "heal.settings.backup": "Create backup copy (.bak)",
        "heal.settings.backup_tip": "Create .bak backup copy before overwriting existing files",
        "heal.settings.output_mode": "Output destination:",
        "heal.settings.output_alongside": "Alongside original (*_HEALED.jpg)",
        "heal.settings.output_custom": "Custom folder...",
        "heal.settings.inplace": "Overwrite original (inplace)",
        # Actions
        "heal.action.heal_single": "Heal Current File",
        "heal.action.heal_batch": "Batch Heal All ({count})",
        "heal.action.stop_batch": "Stop Batch",
        # Preview Toolbar & Badges
        "heal.preview.title": "Interactive Comparison",
        "heal.preview.split": "Split",
        "heal.preview.before": "Before",
        "heal.preview.after": "After",
        "heal.preview.zoom_in": "Zoom In",
        "heal.preview.zoom_out": "Zoom Out",
        "heal.preview.zoom_fit": "Fit View",
        "heal.preview.zoom_100": "100%",
        "heal.preview.zoom_reset": "Reset",
        "heal.preview.no_selection": "Select a candidate from the queue to preview",
        "heal.preview.decoding_error": "Decoding error: {error}",
        "heal.preview.trim_zero_notice": "TRIM Zeroed Header ({offset})",
        # Logs & Dialogs
        "heal.log.title": "Operation Log",
        "heal.log.start_batch": "Starting healing of {count} candidates...",
        "heal.log.healed": "[OK] {name} -> {output} ({size})",
        "heal.log.skipped": "[SKIP] {name}: {reason}",
        "heal.log.error": "[ERROR] {name}: {error}",
        "heal.log.cancelled": "Operation cancelled by user.",
        "heal.log.batch_done": "Batch healing complete: {healed} healed, {skipped} skipped, {errors} errors.",
        "heal.dialog.select_donor": "Select Donor JPEG File",
        "heal.dialog.save_healed": "Save Healed JPEG",
        "heal.dialog.select_output_dir": "Select Output Directory",
        "heal.dialog.add_candidates": "Select Damaged JPEG Candidates",
        "heal.status.ready": "Ready to heal",
        "heal.status.healing": "Healing {name} ({current}/{total})...",
    },
    "ru": {
        # App & Window
        "app.title": "Photo Healer — Восстановление поврежденных фото",
        "app.subtitle": "Криминалистический анализ и восстановление фотоархивов после TRIM",
        # Language Switcher
        "lang.switch": "Язык",
        "lang.en": "English",
        "lang.ru": "Русский",
        # Folder selection bar
        "folder.label": "Папка архива:",
        "folder.placeholder": "Выберите или перетащите папку архива сюда...",
        "folder.browse": "Обзор...",
        "folder.scan": "Сканировать",
        "folder.cancel": "Остановить",
        "folder.dialog_title": "Выбор папки архива",
        # Tabs
        "tab.diagnostics": "Диагностика",
        "tab.recovery": "Восстановление",
        "tab.gallery": "Галерея превью",
        # Status Bar & Metrics
        "status.ready": "Готов к сканированию",
        "status.scanning": "Сканирование: {current} из {total} ({filename})",
        "status.completed": "Сканирование завершено: обработано файлов: {count} ({size})",
        "status.cancelled": "Сканирование остановлено (обработано файлов: {count})",
        "status.error": "Ошибка сканирования: {error}",
        "metric.files": "Файлов: {count}",
        "metric.total_size": "Общий объем: {size}",
        "metric.status": "Статус: {status}",
        # Table Columns
        "table.col.name": "Имя файла",
        "table.col.status": "Статус",
        "table.col.size": "Размер",
        "table.col.path": "Путь",
        # Status Badges
        "status.healed_candidate": "Кандидат на лечение",
        "status.trim_zero": "TRIM-пустышка",
        "status.valid": "Целый",
        "status.error": "Ошибка",
        "status.empty": "Пустой",
        "status.other": "Другое",
        # Filter Buttons
        "filter.all": "Все ({count})",
        "filter.candidates": "Кандидаты ({count})",
        "filter.dummies": "Пустышки ({count})",
        "filter.intact": "Целые ({count})",
        # Quick Actions
        "action.quarantine": "Карантин пустышек",
        "action.export_json": "Экспорт отчета (JSON)",
        # Quarantine Dialog
        "quarantine.title": "Карантин TRIM-пустышек",
        "quarantine.confirm_prompt": "Обнаружено {count} TRIM-пустышек ({size}). Переместить их в папку карантина?",
        "quarantine.dest_label": "Папка назначения для карантина:",
        "quarantine.dry_run": "Безопасное моделирование (dry-run)",
        "quarantine.browse": "Обзор...",
        "quarantine.btn_ok": "Выполнить",
        "quarantine.btn_cancel": "Отмена",
        "quarantine.no_items": "В текущем архиве нет TRIM-пустышек для перемещения в карантин.",
        "quarantine.success": "Карантин завершен! Перемещено: {moved}, пропущено: {skipped}, освобождено: {freed}",
        "quarantine.dry_run_success": "[Моделирование] Будет перемещено файлов: {moved}. Будет освобождено: {freed}",
        "quarantine.dest_dialog_title": "Выберите папку для карантина",
        # Export Report Dialog
        "export.title": "Сохранение отчета диагностики",
        "export.filter": "Файлы JSON (*.json)",
        "export.success": "Отчет диагностики успешно сохранен в:\n{path}",
        "export.error": "Не удалось сохранить отчет: {error}",
        "export.no_data": "Нет данных для экспорта. Сначала выполните сканирование папки.",
        # Placeholder Tabs
        "recovery.title": "Трансплантация заголовков и ресинхронизация потока",
        "recovery.desc": "Выберите файлы-кандидаты на вкладке диагностики для криминалистического восстановления с донорами.",
        "gallery.title": "Извлечение миниатюр и превью (MPF / EXIF)",
        "gallery.desc": "Быстрый визуальный контроль и извлечение встроенных превью из поврежденных данных.",
        # Preview Gallery (Carve View)
        "carve.btn.extract": "Извлечь превью",
        "carve.btn.stop": "Остановить",
        "carve.btn.export_selected": "Экспортировать выбранные",
        "carve.btn.export_all": "Экспортировать все найденные превью",
        "carve.btn.select_all": "Выбрать все",
        "carve.btn.deselect_all": "Снять выбор",
        "carve.filter.all": "Все типы",
        "carve.filter.mpf": "MPF Full HD",
        "carve.filter.exif": "Миниатюры EXIF",
        "carve.filter.raw": "Сырые потоки",
        "carve.search.placeholder": "Фильтр по имени файла...",
        "carve.status.ready": "Готов к извлечению превью",
        "carve.status.scanning": "Извлечение: {current} из {total} ({filename})",
        "carve.status.found_summary": "Найдено превью: {count} ({size})",
        "carve.status.empty": "Превью пока не извлечены. Нажмите «Извлечь превью» для сканирования.",
        "carve.export.dialog_title": "Выбор папки для сохранения превью",
        "carve.export.no_selection": "Не выбрано ни одного превью для экспорта.",
        "carve.export.success": "Успешно экспортировано превью: {count} в папку:\n{path}",
        "carve.badge.mpf": "APP2 MPF Full HD",
        "carve.badge.exif": "APP1 EXIF Thumb",
        "carve.badge.raw": "Raw Carved Stream",
        # Heal View & Queue
        "heal.queue.title": "Очередь кандидатов",
        "heal.queue.add": "Добавить файлы...",
        "heal.queue.clear": "Очистить",
        "heal.queue.count": "{count} кандидатов",
        "heal.queue.empty": "Очередь пуста. Перетащите поврежденные файлы или запустите сканирование во вкладке «Диагностика».",
        "heal.queue.col_name": "Файл",
        "heal.queue.col_size": "Размер",
        "heal.queue.col_offset": "Смещение",
        # Donor Selection
        "heal.donor.title": "Файл-донор JPEG",
        "heal.donor.auto": "Авто-подбор донора (папка / модель камеры)",
        "heal.donor.browse": "Выбрать донор...",
        "heal.donor.drop_prompt": "Перетащите файл-донор сюда или нажмите «Выбрать донор...»",
        "heal.donor.none": "Донор не выбран",
        "heal.donor.auto_found": "Авто-донор: {name} ({geometry})",
        "heal.donor.manual": "Ручной донор: {name} ({geometry})",
        # Restoration Settings
        "heal.settings.title": "Настройки восстановления",
        "heal.settings.pad_geometry": "Выравнивать геометрию кадра (pad_geometry)",
        "heal.settings.pad_geometry_tip": "Вставка dummy-интервалов перезапуска (RST) через StreamResync для компенсации потерянных строк и устранения вертикального сдвига",
        "heal.settings.backup": "Создавать резервную копию (.bak)",
        "heal.settings.backup_tip": "Создавать резервную копию .bak перед перезаписью существующих файлов",
        "heal.settings.output_mode": "Куда сохранять:",
        "heal.settings.output_alongside": "Рядом с оригиналом (*_HEALED.jpg)",
        "heal.settings.output_custom": "В отдельную папку...",
        "heal.settings.inplace": "Перезаписать оригинал (inplace)",
        # Actions
        "heal.action.heal_single": "Вылечить этот файл",
        "heal.action.heal_batch": "Пакетное лечение всех ({count})",
        "heal.action.stop_batch": "Остановить пакет",
        # Preview Toolbar & Badges
        "heal.preview.title": "Интерактивное сравнение",
        "heal.preview.split": "Сплит",
        "heal.preview.before": "До",
        "heal.preview.after": "После",
        "heal.preview.zoom_in": "Увеличить",
        "heal.preview.zoom_out": "Уменьшить",
        "heal.preview.zoom_fit": "По размеру окна",
        "heal.preview.zoom_100": "100%",
        "heal.preview.zoom_reset": "Сброс",
        "heal.preview.no_selection": "Выберите файл-кандидат из списка для предпросмотра",
        "heal.preview.decoding_error": "Ошибка декодирования: {error}",
        "heal.preview.trim_zero_notice": "Заголовок занулен TRIM ({offset})",
        # Logs & Dialogs
        "heal.log.title": "Журнал операций",
        "heal.log.start_batch": "Запуск лечения {count} кандидатов...",
        "heal.log.healed": "[OK] {name} -> {output} ({size})",
        "heal.log.skipped": "[ПРОПУСК] {name}: {reason}",
        "heal.log.error": "[ОШИБКА] {name}: {error}",
        "heal.log.cancelled": "Операция остановлена пользователем.",
        "heal.log.batch_done": "Пакетное лечение завершено: вылечено {healed}, пропущено {skipped}, ошибок {errors}.",
        "heal.dialog.select_donor": "Выбор файла-донора JPEG",
        "heal.dialog.save_healed": "Сохранить восстановленный JPEG",
        "heal.dialog.select_output_dir": "Выбор папки для сохранения",
        "heal.dialog.add_candidates": "Выбор поврежденных файлов-кандидатов",
        "heal.status.ready": "Готов к лечению",
        "heal.status.healing": "Лечение {name} ({current} из {total})...",
    },
}


class I18nManager(QObject):
    """Localization manager providing runtime translation and reactive language change signals."""

    language_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current_language: str = detect_system_language()

    def get_language(self) -> str:
        """Get currently active language code ('ru' or 'en')."""
        return self._current_language

    def set_language(self, lang: str) -> None:
        """Set active language and emit language_changed signal if changed."""
        normalized = lang.strip().lower()
        if normalized not in SUPPORTED_LANGUAGES:
            normalized = DEFAULT_LANGUAGE

        if self._current_language != normalized:
            self._current_language = normalized
            self.language_changed.emit(normalized)

    def t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        """Look up localized string with interpolation support."""
        lang_dict = TRANSLATIONS.get(self._current_language, TRANSLATIONS[DEFAULT_LANGUAGE])
        template = lang_dict.get(key)

        if template is None:
            # Fallback to English
            template = TRANSLATIONS[DEFAULT_LANGUAGE].get(key)

        if template is None:
            template = default if default is not None else key

        if kwargs:
            try:
                return template.format(**kwargs)
            except (KeyError, ValueError, IndexError):
                return template
        return template


# Singleton instance
i18n: I18nManager = I18nManager()


def t(key: str, default: str | None = None, **kwargs: Any) -> str:
    """Convenience module-level translator using singleton I18nManager."""
    return i18n.t(key, default=default, **kwargs)


def get_language() -> str:
    """Get active language code."""
    return i18n.get_language()


def set_language(lang: str) -> None:
    """Set active language code."""
    i18n.set_language(lang)
