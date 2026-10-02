# -*- coding: utf-8 -*-
"""Compatibility module forwarding to photo_healer.gui.workers."""

from __future__ import annotations

from photo_healer.gui.workers.triage_worker import (
    CHUNK_SIZE,
    DEFAULT_EXTS,
    IMAGE_MAGICS,
    TriageWorker,
    audit_file_streaming,
)

__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_EXTS",
    "IMAGE_MAGICS",
    "TriageWorker",
    "audit_file_streaming",
]
