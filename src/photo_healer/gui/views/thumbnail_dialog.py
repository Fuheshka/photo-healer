# -*- coding: utf-8 -*-
"""Dialog for batch thumbnail stripping, rebuilding, and Windows icon cache reset.

Features:
  - Target folder selection with Browse dialog and prefill support
  - Mode selection:
      * Fast strip of donor thumbnails (recommended, streaming, zero re-encode)
      * Rebuild genuine thumbnails from photo raster (Pillow IFD1 downsampling)
  - Windows Explorer thumbnail and icon cache safe flush without system restart
  - Asynchronous non-blocking execution via ThumbnailWorker with live progress and cancel
  - Dark-mode theme styling and 100% reactive bilingual UI localization (EN/RU)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from photo_healer.core.thumbnail import clear_windows_thumbnail_cache
from photo_healer.gui.i18n import i18n, t
from photo_healer.gui.icon import get_app_icon
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.workers.thumbnail_worker import ThumbnailWorker

DIALOG_STYLE_QSS: str = """
QDialog {
    background-color: #121214;
    color: #f4f4f5;
}

QFrame#cardFrame {
    background-color: #18181b;
    border: 1px solid #27272a;
    border-radius: 8px;
    padding: 12px;
}

QLineEdit {
    background-color: #202024;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}
QLineEdit:focus {
    border-color: #3b82f6;
}

QPushButton {
    background-color: #27272a;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3f3f46;
}
QPushButton:pressed {
    background-color: #52525b;
}

QPushButton#btnPrimary {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btnPrimary:hover {
    background-color: #1d4ed8;
}
QPushButton#btnPrimary:pressed {
    background-color: #1e40af;
}
QPushButton#btnPrimary:disabled {
    background-color: #3f3f46;
    color: #71717a;
}

QPushButton#btnDanger {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btnDanger:hover {
    background-color: #dc2626;
}

QPushButton#btnResetCache {
    background-color: #202024;
    color: #38bdf8;
    border: 1px solid #0369a1;
}
QPushButton#btnResetCache:hover {
    background-color: #075985;
    color: #f0f9ff;
}

QRadioButton {
    color: #f4f4f5;
    font-size: 12px;
    font-weight: 600;
    spacing: 6px;
}
QRadioButton::indicator {
    width: 14px;
    height: 14px;
}

QCheckBox {
    color: #f4f4f5;
    font-size: 12px;
    spacing: 6px;
}

QProgressBar {
    background-color: #202024;
    border: 1px solid #27272a;
    border-radius: 4px;
    text-align: center;
    color: #f4f4f5;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #3b82f6;
    border-radius: 3px;
}
"""


class ThumbnailFixDialog(QDialog):
    """Modal or modeless dialog to configure and run thumbnail fixing operations."""

    def __init__(
        self,
        target_folder: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.target_folder: Path | None = Path(target_folder) if target_folder else None
        self.worker: ThumbnailWorker | None = None

        self.setWindowTitle(t("thumb_dialog.title"))
        self.setMinimumWidth(540)
        self.resize(560, 420)

        app_icon = get_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._init_ui()
        self._apply_styling()

        if self.target_folder:
            self.txt_folder.setText(str(self.target_folder))

        i18n.language_changed.connect(self._retranslate_ui)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        # ── 1. Target Folder Selection ────────────────────────────────────────
        folder_card = QFrame(self)
        folder_card.setObjectName("cardFrame")
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(10, 10, 10, 10)
        folder_layout.setSpacing(6)

        self.lbl_folder = QLabel(t("thumb_dialog.folder_label"))
        self.lbl_folder.setStyleSheet("font-size: 12px; font-weight: bold; color: #f4f4f5;")
        folder_layout.addWidget(self.lbl_folder)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)

        self.txt_folder = QLineEdit(self)
        self.txt_folder.setPlaceholderText(t("folder.placeholder"))
        picker_row.addWidget(self.txt_folder, 1)

        self.btn_browse = QPushButton(t("thumb_dialog.folder_browse"), self)
        self.btn_browse.clicked.connect(self._on_browse_clicked)
        picker_row.addWidget(self.btn_browse)

        folder_layout.addLayout(picker_row)
        root_layout.addWidget(folder_card)

        # ── 2. Mode Selection Card ────────────────────────────────────────────
        mode_card = QFrame(self)
        mode_card.setObjectName("cardFrame")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(10, 10, 10, 10)
        mode_layout.setSpacing(10)

        self.lbl_mode_title = QLabel(t("thumb_dialog.mode_title"))
        self.lbl_mode_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #f4f4f5;")
        mode_layout.addWidget(self.lbl_mode_title)

        self.mode_group = QButtonGroup(self)

        # Mode A: Strip
        strip_row = QVBoxLayout()
        strip_row.setSpacing(2)
        self.radio_strip = QRadioButton(t("thumb_dialog.mode_strip"), self)
        self.radio_strip.setChecked(True)
        self.mode_group.addButton(self.radio_strip, 0)
        strip_row.addWidget(self.radio_strip)

        self.lbl_strip_desc = QLabel(t("thumb_dialog.mode_strip_desc"), self)
        self.lbl_strip_desc.setStyleSheet("font-size: 11px; color: #a1a1aa; padding-left: 20px;")
        self.lbl_strip_desc.setWordWrap(True)
        strip_row.addWidget(self.lbl_strip_desc)
        mode_layout.addLayout(strip_row)

        # Mode B: Rebuild
        rebuild_row = QVBoxLayout()
        rebuild_row.setSpacing(2)
        self.radio_rebuild = QRadioButton(t("thumb_dialog.mode_rebuild"), self)
        self.mode_group.addButton(self.radio_rebuild, 1)
        rebuild_row.addWidget(self.radio_rebuild)

        self.lbl_rebuild_desc = QLabel(t("thumb_dialog.mode_rebuild_desc"), self)
        self.lbl_rebuild_desc.setStyleSheet("font-size: 11px; color: #a1a1aa; padding-left: 20px;")
        self.lbl_rebuild_desc.setWordWrap(True)
        rebuild_row.addWidget(self.lbl_rebuild_desc)
        mode_layout.addLayout(rebuild_row)

        # Backup checkbox
        self.chk_backup = QCheckBox(t("thumb_dialog.backup"), self)
        self.chk_backup.setChecked(False)
        self.chk_backup.setToolTip(t("thumb_dialog.backup_tip"))
        mode_layout.addWidget(self.chk_backup)

        root_layout.addWidget(mode_card)

        # ── 3. Progress and Status Section ────────────────────────────────────
        status_box = QVBoxLayout()
        status_box.setSpacing(6)

        self.lbl_status = QLabel(t("thumb_dialog.status_ready"), self)
        self.lbl_status.setStyleSheet("font-size: 12px; color: #60a5fa;")
        self.lbl_status.setWordWrap(True)
        status_box.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setVisible(False)
        status_box.addWidget(self.progress_bar)

        root_layout.addLayout(status_box)

        # ── 4. Bottom Action Buttons ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        # Windows icon cache flush button
        self.btn_reset_cache = QPushButton(t("thumb_dialog.btn_reset_cache"), self)
        self.btn_reset_cache.setObjectName("btnResetCache")
        self.btn_reset_cache.setToolTip(t("thumb_dialog.btn_reset_cache_tip"))
        self.btn_reset_cache.clicked.connect(self._on_reset_cache_clicked)
        btn_row.addWidget(self.btn_reset_cache)

        btn_row.addStretch()

        self.btn_stop = QPushButton(t("thumb_dialog.btn_stop"), self)
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(self.btn_stop)

        self.btn_start = QPushButton(t("thumb_dialog.btn_start"), self)
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(self.btn_start)

        self.btn_close = QPushButton(t("thumb_dialog.btn_close"), self)
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)

        root_layout.addLayout(btn_row)

    def _apply_styling(self) -> None:
        self.setStyleSheet(DIALOG_STYLE_QSS)

    def _retranslate_ui(self) -> None:
        """Reactive localization update."""
        self.setWindowTitle(t("thumb_dialog.title"))
        self.lbl_folder.setText(t("thumb_dialog.folder_label"))
        self.txt_folder.setPlaceholderText(t("folder.placeholder"))
        self.btn_browse.setText(t("thumb_dialog.folder_browse"))
        self.lbl_mode_title.setText(t("thumb_dialog.mode_title"))
        self.radio_strip.setText(t("thumb_dialog.mode_strip"))
        self.lbl_strip_desc.setText(t("thumb_dialog.mode_strip_desc"))
        self.radio_rebuild.setText(t("thumb_dialog.mode_rebuild"))
        self.lbl_rebuild_desc.setText(t("thumb_dialog.mode_rebuild_desc"))
        self.chk_backup.setText(t("thumb_dialog.backup"))
        self.chk_backup.setToolTip(t("thumb_dialog.backup_tip"))
        self.btn_reset_cache.setText(t("thumb_dialog.btn_reset_cache"))
        self.btn_reset_cache.setToolTip(t("thumb_dialog.btn_reset_cache_tip"))
        self.btn_start.setText(t("thumb_dialog.btn_start"))
        self.btn_stop.setText(t("thumb_dialog.btn_stop"))
        self.btn_close.setText(t("thumb_dialog.btn_close"))

    def _on_browse_clicked(self) -> None:
        initial_dir = self.txt_folder.text().strip()
        folder = QFileDialog.getExistingDirectory(
            self,
            t("thumb_dialog.dialog_select_folder"),
            initial_dir if initial_dir else "",
        )
        if folder:
            self.txt_folder.setText(folder)
            self.target_folder = Path(folder)

    def _on_reset_cache_clicked(self) -> None:
        """Invokes safe Windows Explorer icon & thumbnail cache reset."""
        res = clear_windows_thumbnail_cache()
        if res.get("success", False):
            msg = t("thumb_dialog.cache_success")
            self.lbl_status.setText(msg)
            QMessageBox.information(self, t("app.title"), msg)
        else:
            err = "; ".join(res.get("errors", ["Unknown error"]))
            msg = t("thumb_dialog.cache_error", error=err)
            self.lbl_status.setText(msg)
            QMessageBox.warning(self, t("app.title"), msg)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self.txt_folder.setEnabled(enabled)
        self.btn_browse.setEnabled(enabled)
        self.radio_strip.setEnabled(enabled)
        self.radio_rebuild.setEnabled(enabled)
        self.chk_backup.setEnabled(enabled)
        self.btn_start.setEnabled(enabled)
        self.btn_start.setVisible(enabled)
        self.btn_stop.setVisible(not enabled)
        self.btn_close.setEnabled(enabled)

    def _on_start_clicked(self) -> None:
        folder_str = self.txt_folder.text().strip()
        if not folder_str:
            QMessageBox.warning(self, t("app.title"), t("thumb_dialog.no_folder"))
            return

        folder = Path(folder_str)
        if not folder.is_dir():
            QMessageBox.warning(self, t("app.title"), t("thumb_dialog.no_folder"))
            return

        self.target_folder = folder
        mode = "strip" if self.radio_strip.isChecked() else "rebuild"
        backup = self.chk_backup.isChecked()

        self._set_inputs_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until count is known
        self.lbl_status.setText(t("thumb_dialog.status_processing", current=0, total="...", filename="..."))

        self.worker = ThumbnailWorker(
            folder_path=self.target_folder,
            mode=mode,
            backup=backup,
            clear_cache=True,  # automatically flush cache to reflect changes in Explorer
            parent=self,
        )
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_stop_clicked(self) -> None:
        if self.worker and self.worker.isRunning():
            self.lbl_status.setText(t("thumb_dialog.status_cancelled"))
            self.worker.stop()

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.lbl_status.setText(
            t("thumb_dialog.status_processing", current=current, total=total, filename=filename)
        )

    def _on_worker_finished(self, summary: dict[str, Any]) -> None:
        self._set_inputs_enabled(True)
        self.progress_bar.setVisible(False)

        status = summary.get("status", "completed")
        total = summary.get("total", 0)

        if total == 0:
            msg = t("thumb_dialog.no_files")
            self.lbl_status.setText(msg)
            QMessageBox.information(self, t("app.title"), msg)
            return

        if status == "cancelled":
            msg = t("thumb_dialog.status_cancelled")
            self.lbl_status.setText(msg)
            return

        processed = summary.get("processed", 0)
        stripped = summary.get("stripped", 0)
        rebuilt = summary.get("rebuilt", 0)
        errors = summary.get("errors", 0)
        freed = format_size(summary.get("bytes_freed", 0))

        done_msg = t(
            "thumb_dialog.status_done",
            processed=processed,
            stripped=stripped,
            rebuilt=rebuilt,
            errors=errors,
            freed=freed,
        )
        self.lbl_status.setText(done_msg)
        QMessageBox.information(self, t("app.title"), done_msg)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        super().closeEvent(event)
