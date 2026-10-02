# -*- coding: utf-8 -*-
"""Photo Healer GUI package."""

from __future__ import annotations

from photo_healer.gui.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    I18nManager,
    detect_system_language,
    get_language,
    i18n,
    set_language,
    t,
)
from photo_healer.gui.icon import get_app_icon, get_app_icon_path
from photo_healer.gui.models.file_table_model import (
    COLUMN_KEYS,
    STATUS_COLORS,
    FileFilterProxyModel,
    FileTableModel,
    StatusBadgeDelegate,
    format_size,
)
from photo_healer.gui.workers.triage_worker import (
    CHUNK_SIZE,
    DEFAULT_EXTS,
    IMAGE_MAGICS,
    TriageWorker,
    audit_file_streaming,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "TRANSLATIONS",
    "I18nManager",
    "detect_system_language",
    "get_language",
    "i18n",
    "set_language",
    "t",
    "get_app_icon",
    "get_app_icon_path",
    "COLUMN_KEYS",
    "STATUS_COLORS",
    "FileFilterProxyModel",
    "FileTableModel",
    "StatusBadgeDelegate",
    "format_size",
    "CHUNK_SIZE",
    "DEFAULT_EXTS",
    "IMAGE_MAGICS",
    "TriageWorker",
    "audit_file_streaming",
]
