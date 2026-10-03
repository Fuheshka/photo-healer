# -*- coding: utf-8 -*-
"""Healing and reconstruction view for Photo Healer.

Features:
  - Horizontal QSplitter: Left queue & donor/settings control panel, Right interactive preview.
  - Candidate queue (QListWidget) with quick switching, adding files, and clearing.
  - Smart Auto-Donor discovery (same folder / matching camera model EXIF) & manual drag-and-drop donor box.
  - Restoration controls: pad_geometry (dummy restart interval injection) and backup (.bak).
  - Split-view comparison via SplitPreviewWidget with zoom & pan.
  - Single-file and batch healing with background QThread (HealWorker), progress bar, and operation log.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QFileInfo, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from photo_healer.core.validator import JpegValidator
from photo_healer.gui.i18n import i18n, t
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.widgets.split_preview import SplitPreviewWidget
from photo_healer.gui.workers.heal_worker import (
    HealWorker,
    extract_camera_info,
    find_matching_donor,
)


class DonorDropBox(QFrame):
    """Drop zone and card for donor JPEG files."""

    donor_dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("donorCard")
        self.setMinimumHeight(64)
        self._is_drag_active = False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for u in urls:
                p = Path(u.toLocalFile())
                if p.suffix.lower() in {".jpg", ".jpeg"}:
                    event.acceptProposedAction()
                    self._is_drag_active = True
                    self.update()
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._is_drag_active = False
        self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        self._is_drag_active = False
        urls = event.mimeData().urls()
        for u in urls:
            p = Path(u.toLocalFile())
            if p.suffix.lower() in {".jpg", ".jpeg"} and p.is_file():
                self.donor_dropped.emit(p)
                event.acceptProposedAction()
                self.update()
                return
        event.ignore()


class HealView(QWidget):
    """Main healing workspace view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.archive_root: Path | None = None
        self.current_candidate: Path | None = None
        self.manual_donor_path: Path | None = None
        self.worker: HealWorker | None = None

        self._init_ui()
        self._apply_styling()

        # Connect live reactive localization
        i18n.language_changed.connect(self._retranslate_ui)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Main Horizontal Splitter ──────────────────────────────────────────
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(2)

        # ── 1. Left Control Panel ─────────────────────────────────────────────
        self.left_panel = QWidget(self.splitter)
        self.left_panel.setObjectName("healLeftPanel")
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        # 1.1 Candidate Queue Box
        self.queue_header = QHBoxLayout()
        self.lbl_queue_title = QLabel(t("heal.queue.title"))
        self.lbl_queue_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #f4f4f5;")
        self.lbl_queue_count = QLabel(t("heal.queue.count", count=0))
        self.lbl_queue_count.setStyleSheet("font-size: 11px; color: #60a5fa; font-weight: 600;")
        self.queue_header.addWidget(self.lbl_queue_title)
        self.queue_header.addStretch(1)
        self.queue_header.addWidget(self.lbl_queue_count)
        left_layout.addLayout(self.queue_header)

        # Candidate List
        self.list_candidates = QListWidget()
        self.list_candidates.setObjectName("listCandidates")
        self.list_candidates.setMinimumHeight(130)
        self.list_candidates.currentItemChanged.connect(self._on_candidate_selection_changed)
        left_layout.addWidget(self.list_candidates, 2)

        # Queue buttons
        btn_queue_layout = QHBoxLayout()
        btn_queue_layout.setSpacing(8)
        self.btn_add_files = QPushButton(t("heal.queue.add"))
        self.btn_add_files.setObjectName("btnAddFiles")
        self.btn_add_files.clicked.connect(self._browse_add_candidates)
        btn_queue_layout.addWidget(self.btn_add_files)

        self.btn_clear_queue = QPushButton(t("heal.queue.clear"))
        self.btn_clear_queue.setObjectName("btnClearQueue")
        self.btn_clear_queue.clicked.connect(self.clear_candidates)
        btn_queue_layout.addWidget(self.btn_clear_queue)
        left_layout.addLayout(btn_queue_layout)

        # 1.2 Donor Selection Box
        self.donor_group = QFrame(self.left_panel)
        self.donor_group.setObjectName("settingsCard")
        donor_layout = QVBoxLayout(self.donor_group)
        donor_layout.setContentsMargins(12, 12, 12, 12)
        donor_layout.setSpacing(8)

        self.lbl_donor_section = QLabel(t("heal.donor.title"))
        self.lbl_donor_section.setStyleSheet("font-size: 12px; font-weight: bold; color: #e4e4e7;")
        donor_layout.addWidget(self.lbl_donor_section)

        # Auto-Donor Toggle
        self.chk_auto_donor = QCheckBox(t("heal.donor.auto"))
        self.chk_auto_donor.setChecked(True)
        self.chk_auto_donor.toggled.connect(self._on_donor_mode_toggled)
        donor_layout.addWidget(self.chk_auto_donor)

        # Drop Box Card for donor
        self.donor_drop_card = DonorDropBox(self.donor_group)
        drop_layout = QVBoxLayout(self.donor_drop_card)
        drop_layout.setContentsMargins(10, 8, 10, 8)
        drop_layout.setSpacing(4)

        self.lbl_donor_status = QLabel(t("heal.donor.none"))
        self.lbl_donor_status.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        self.lbl_donor_status.setWordWrap(True)
        drop_layout.addWidget(self.lbl_donor_status)

        self.lbl_donor_drop_hint = QLabel(t("heal.donor.drop_prompt"))
        self.lbl_donor_drop_hint.setStyleSheet("font-size: 10px; color: #71717a; font-style: italic;")
        drop_layout.addWidget(self.lbl_donor_drop_hint)
        self.donor_drop_card.donor_dropped.connect(self._on_donor_file_dropped)

        donor_layout.addWidget(self.donor_drop_card)

        # Browse Donor Button
        self.btn_browse_donor = QPushButton(t("heal.donor.browse"))
        self.btn_browse_donor.setObjectName("btnBrowseDonor")
        self.btn_browse_donor.clicked.connect(self._browse_donor_file)
        donor_layout.addWidget(self.btn_browse_donor)

        left_layout.addWidget(self.donor_group)

        # 1.3 Restoration Settings Box
        self.settings_group = QFrame(self.left_panel)
        self.settings_group.setObjectName("settingsCard")
        settings_layout = QVBoxLayout(self.settings_group)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setSpacing(8)

        self.lbl_settings_section = QLabel(t("heal.settings.title"))
        self.lbl_settings_section.setStyleSheet("font-size: 12px; font-weight: bold; color: #e4e4e7;")
        settings_layout.addWidget(self.lbl_settings_section)

        # pad_geometry checkbox
        self.chk_pad_geometry = QCheckBox(t("heal.settings.pad_geometry"))
        self.chk_pad_geometry.setToolTip(t("heal.settings.pad_geometry_tip"))
        self.chk_pad_geometry.toggled.connect(self._on_pad_geometry_toggled)
        settings_layout.addWidget(self.chk_pad_geometry)

        # strip_thumbnail checkbox
        self.chk_strip_thumbnail = QCheckBox(t("heal.settings.strip_thumbnail"))
        self.chk_strip_thumbnail.setChecked(True)
        self.chk_strip_thumbnail.setToolTip(t("heal.settings.strip_thumbnail_tip"))
        settings_layout.addWidget(self.chk_strip_thumbnail)

        # create_backup (.bak) checkbox
        self.chk_create_backup = QCheckBox(t("heal.settings.backup"))
        self.chk_create_backup.setChecked(True)
        self.chk_create_backup.setToolTip(t("heal.settings.backup_tip"))
        settings_layout.addWidget(self.chk_create_backup)

        left_layout.addWidget(self.settings_group)

        # 1.4 Action Buttons
        self.action_layout = QVBoxLayout()
        self.action_layout.setSpacing(8)

        self.btn_heal_single = QPushButton(t("heal.action.heal_single"))
        self.btn_heal_single.setObjectName("btnHealSingle")
        self.btn_heal_single.clicked.connect(self._heal_current_file)
        self.action_layout.addWidget(self.btn_heal_single)

        self.btn_heal_batch = QPushButton(t("heal.action.heal_batch", count=0))
        self.btn_heal_batch.setObjectName("btnHealBatch")
        self.btn_heal_batch.clicked.connect(self._start_batch_heal)
        self.action_layout.addWidget(self.btn_heal_batch)

        self.btn_stop_batch = QPushButton(t("heal.action.stop_batch"))
        self.btn_stop_batch.setObjectName("btnStopBatch")
        self.btn_stop_batch.setVisible(False)
        self.btn_stop_batch.clicked.connect(self._stop_batch_heal)
        self.action_layout.addWidget(self.btn_stop_batch)

        left_layout.addLayout(self.action_layout)

        # Batch Progress Bar
        self.batch_progress = QProgressBar()
        self.batch_progress.setFixedHeight(14)
        self.batch_progress.setTextVisible(False)
        self.batch_progress.setVisible(False)
        left_layout.addWidget(self.batch_progress)

        # Operation Log Console
        self.lbl_log_title = QLabel(t("heal.log.title"))
        self.lbl_log_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #71717a;")
        left_layout.addWidget(self.lbl_log_title)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(110)
        left_layout.addWidget(self.log_view, 1)

        self.splitter.addWidget(self.left_panel)

        # ── 2. Right Preview Panel ────────────────────────────────────────────
        self.right_panel = QWidget(self.splitter)
        self.right_panel.setObjectName("healRightPanel")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 2.1 Top Toolbar
        self.preview_toolbar = QFrame(self.right_panel)
        self.preview_toolbar.setObjectName("previewToolbar")
        tb_layout = QHBoxLayout(self.preview_toolbar)
        tb_layout.setContentsMargins(14, 8, 14, 8)
        tb_layout.setSpacing(8)

        # View Mode Switcher: Split / Before / After
        self.view_btn_group = QButtonGroup(self)
        self.btn_mode_split = QPushButton(t("heal.preview.split"))
        self.btn_mode_split.setCheckable(True)
        self.btn_mode_split.setChecked(True)
        self.btn_mode_split.setObjectName("btnMode")
        self.view_btn_group.addButton(self.btn_mode_split)
        tb_layout.addWidget(self.btn_mode_split)

        self.btn_mode_before = QPushButton(t("heal.preview.before"))
        self.btn_mode_before.setCheckable(True)
        self.btn_mode_before.setObjectName("btnMode")
        self.view_btn_group.addButton(self.btn_mode_before)
        tb_layout.addWidget(self.btn_mode_before)

        self.btn_mode_after = QPushButton(t("heal.preview.after"))
        self.btn_mode_after.setCheckable(True)
        self.btn_mode_after.setObjectName("btnMode")
        self.view_btn_group.addButton(self.btn_mode_after)
        tb_layout.addWidget(self.btn_mode_after)

        self.btn_mode_split.clicked.connect(lambda: self.preview_widget.set_view_mode("split"))
        self.btn_mode_before.clicked.connect(lambda: self.preview_widget.set_view_mode("before"))
        self.btn_mode_after.clicked.connect(lambda: self.preview_widget.set_view_mode("after"))

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #3f3f46;")
        tb_layout.addWidget(sep)

        # Zoom buttons
        self.btn_zoom_out = QPushButton("-")
        self.btn_zoom_out.setObjectName("btnZoom")
        self.btn_zoom_out.setToolTip(t("heal.preview.zoom_out"))
        self.btn_zoom_out.clicked.connect(self._on_zoom_out)
        tb_layout.addWidget(self.btn_zoom_out)

        self.lbl_zoom = QLabel("Fit")
        self.lbl_zoom.setStyleSheet("font-size: 11px; font-weight: bold; color: #a1a1aa; min-width: 44px;")
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb_layout.addWidget(self.lbl_zoom)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setObjectName("btnZoom")
        self.btn_zoom_in.setToolTip(t("heal.preview.zoom_in"))
        self.btn_zoom_in.clicked.connect(self._on_zoom_in)
        tb_layout.addWidget(self.btn_zoom_in)

        self.btn_zoom_fit = QPushButton(t("heal.preview.zoom_fit"))
        self.btn_zoom_fit.setObjectName("btnZoomAction")
        self.btn_zoom_fit.clicked.connect(self._on_zoom_fit)
        tb_layout.addWidget(self.btn_zoom_fit)

        self.btn_zoom_100 = QPushButton(t("heal.preview.zoom_100"))
        self.btn_zoom_100.setObjectName("btnZoomAction")
        self.btn_zoom_100.clicked.connect(self._on_zoom_100)
        tb_layout.addWidget(self.btn_zoom_100)

        tb_layout.addStretch(1)

        # Metadata pill
        self.lbl_stats_badge = QLabel("")
        self.lbl_stats_badge.setStyleSheet(
            "background-color: #202024; color: #60a5fa; border: 1px solid #27272a; "
            "border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600;"
        )
        self.lbl_stats_badge.setVisible(False)
        tb_layout.addWidget(self.lbl_stats_badge)

        right_layout.addWidget(self.preview_toolbar)

        # 2.2 Split Preview Canvas
        self.preview_widget = SplitPreviewWidget(self.right_panel)
        self.preview_widget.zoom_changed.connect(self._on_preview_zoom_changed)
        right_layout.addWidget(self.preview_widget, 1)

        # 2.3 Bottom File Info Bar
        self.info_bar = QFrame(self.right_panel)
        self.info_bar.setObjectName("infoBar")
        info_layout = QHBoxLayout(self.info_bar)
        info_layout.setContentsMargins(14, 6, 14, 6)
        info_layout.setSpacing(16)

        self.lbl_file_details = QLabel(t("heal.preview.no_selection"))
        self.lbl_file_details.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        info_layout.addWidget(self.lbl_file_details, 1)

        right_layout.addWidget(self.info_bar)

        self.splitter.addWidget(self.right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([340, 740])

        root_layout.addWidget(self.splitter)

    def _apply_styling(self) -> None:
        self.setStyleSheet("""
            QWidget#healLeftPanel {
                background-color: #18181b;
                border-right: 1px solid #27272a;
            }
            QWidget#healRightPanel {
                background-color: #121214;
            }
            QFrame#previewToolbar, QFrame#infoBar {
                background-color: #18181b;
                border-bottom: 1px solid #27272a;
            }
            QFrame#infoBar {
                border-bottom: none;
                border-top: 1px solid #27272a;
            }
            QFrame#settingsCard {
                background-color: #202024;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
            QFrame#donorCard {
                background-color: #18181b;
                border: 1px dashed #3f3f46;
                border-radius: 6px;
            }
            QFrame#donorCard:hover {
                border-color: #3b82f6;
            }
            QListWidget#listCandidates {
                background-color: #202024;
                border: 1px solid #27272a;
                border-radius: 6px;
                color: #f4f4f5;
                font-size: 12px;
                outline: none;
            }
            QListWidget#listCandidates::item {
                padding: 6px 10px;
                border-bottom: 1px solid #27272a;
            }
            QListWidget#listCandidates::item:hover {
                background-color: #27272a;
            }
            QListWidget#listCandidates::item:selected {
                background-color: #3b82f6;
                color: #ffffff;
                font-weight: bold;
            }
            QPushButton#btnHealSingle {
                background-color: #10b981;
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton#btnHealSingle:hover { background-color: #059669; }
            QPushButton#btnHealSingle:pressed { background-color: #047857; }

            QPushButton#btnHealBatch {
                background-color: #3b82f6;
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton#btnHealBatch:hover { background-color: #2563eb; }
            QPushButton#btnHealBatch:pressed { background-color: #1d4ed8; }

            QPushButton#btnStopBatch {
                background-color: #ef4444;
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton#btnStopBatch:hover { background-color: #dc2626; }

            QPushButton#btnAddFiles, QPushButton#btnClearQueue, QPushButton#btnBrowseDonor {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#btnAddFiles:hover, QPushButton#btnClearQueue:hover, QPushButton#btnBrowseDonor:hover {
                background-color: #3f3f46;
            }
            QPushButton#btnMode, QPushButton#btnZoom, QPushButton#btnZoomAction {
                background-color: #202024;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#btnMode:hover, QPushButton#btnZoom:hover, QPushButton#btnZoomAction:hover {
                background-color: #27272a;
                color: #f4f4f5;
            }
            QPushButton#btnMode:checked {
                background-color: #3b82f6;
                color: #ffffff;
                font-weight: bold;
                border-color: #60a5fa;
            }
            QPlainTextEdit#logView {
                background-color: #121214;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 6px;
                font-family: Consolas, monospace;
                font-size: 10px;
            }
            QCheckBox {
                color: #f4f4f5;
                font-size: 12px;
            }
        """)

    def _retranslate_ui(self) -> None:
        """Update localized UI text across buttons and labels."""
        self.lbl_queue_title.setText(t("heal.queue.title"))
        self.lbl_queue_count.setText(t("heal.queue.count", count=self.list_candidates.count()))
        self.btn_add_files.setText(t("heal.queue.add"))
        self.btn_clear_queue.setText(t("heal.queue.clear"))
        self.lbl_donor_section.setText(t("heal.donor.title"))
        self.chk_auto_donor.setText(t("heal.donor.auto"))
        self.btn_browse_donor.setText(t("heal.donor.browse"))
        self.lbl_donor_drop_hint.setText(t("heal.donor.drop_prompt"))
        self.lbl_settings_section.setText(t("heal.settings.title"))
        self.chk_pad_geometry.setText(t("heal.settings.pad_geometry"))
        self.chk_pad_geometry.setToolTip(t("heal.settings.pad_geometry_tip"))
        self.chk_strip_thumbnail.setText(t("heal.settings.strip_thumbnail"))
        self.chk_strip_thumbnail.setToolTip(t("heal.settings.strip_thumbnail_tip"))
        self.chk_create_backup.setText(t("heal.settings.backup"))
        self.chk_create_backup.setToolTip(t("heal.settings.backup_tip"))
        self.btn_heal_single.setText(t("heal.action.heal_single"))
        self.btn_heal_batch.setText(t("heal.action.heal_batch", count=self.list_candidates.count()))
        self.btn_stop_batch.setText(t("heal.action.stop_batch"))
        self.lbl_log_title.setText(t("heal.log.title"))
        self.btn_mode_split.setText(t("heal.preview.split"))
        self.btn_mode_before.setText(t("heal.preview.before"))
        self.btn_mode_after.setText(t("heal.preview.after"))
        self.btn_zoom_fit.setText(t("heal.preview.zoom_fit"))
        self.btn_zoom_100.setText(t("heal.preview.zoom_100"))

        self._refresh_donor_label()

    # ── Candidate Queue Management ───────────────────────────────────────────

    def set_archive_path(self, path: Path | str | None) -> None:
        self.archive_root = Path(path) if path else None

    def add_candidate(self, file_path: Path | str, select: bool = True) -> None:
        """Add a single candidate file to the queue."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return

        # Check duplicates
        for i in range(self.list_candidates.count()):
            item = self.list_candidates.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == str(p):
                if select:
                    self.list_candidates.setCurrentItem(item)
                return

        size_str = format_size(p.stat().st_size)
        item = QListWidgetItem(f"{p.name} ({size_str})")
        item.setData(Qt.ItemDataRole.UserRole, str(p))
        self.list_candidates.addItem(item)

        self.lbl_queue_count.setText(t("heal.queue.count", count=self.list_candidates.count()))
        self.btn_heal_batch.setText(t("heal.action.heal_batch", count=self.list_candidates.count()))

        if select:
            self.list_candidates.setCurrentItem(item)

    def set_candidates(self, candidates: Sequence[Path | str | dict[str, Any]]) -> None:
        """Batch set candidate files into the queue."""
        self.clear_candidates()
        for cand in candidates:
            if isinstance(cand, dict):
                p = cand.get("path")
            else:
                p = str(cand)
            if p:
                self.add_candidate(p, select=False)

        if self.list_candidates.count() > 0:
            self.list_candidates.setCurrentRow(0)

    def clear_candidates(self) -> None:
        """Clear candidate queue and preview."""
        self.list_candidates.clear()
        self.current_candidate = None
        self.lbl_queue_count.setText(t("heal.queue.count", count=0))
        self.btn_heal_batch.setText(t("heal.action.heal_batch", count=0))
        self.preview_widget.clear()
        self.lbl_stats_badge.setVisible(False)
        self.lbl_file_details.setText(t("heal.preview.no_selection"))
        self._refresh_donor_label()

    def _browse_add_candidates(self) -> None:
        """Browse dialog to add damaged JPEG candidates."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            t("heal.dialog.add_candidates"),
            str(self.archive_root) if self.archive_root else "",
            "JPEG Images (*.jpg *.jpeg *.JPG *.JPEG);;All Files (*.*)",
        )
        if files:
            for f in files:
                self.add_candidate(f, select=False)
            if self.list_candidates.count() > 0 and self.current_candidate is None:
                self.list_candidates.setCurrentRow(0)

    # ── Donor Management ──────────────────────────────────────────────────────

    def _on_donor_mode_toggled(self, checked: bool) -> None:
        """Toggle Auto-donor vs manual donor mode."""
        if checked:
            self.manual_donor_path = None
        self._refresh_donor_label()
        self._reload_preview()

    def _on_donor_file_dropped(self, donor_path: Path) -> None:
        """Handler for donor file dropped into drop card."""
        self.manual_donor_path = donor_path
        self.chk_auto_donor.setChecked(False)
        self._refresh_donor_label()
        self._reload_preview()

    def _browse_donor_file(self) -> None:
        """Browse dialog to pick manual donor JPEG file."""
        init_dir = (
            str(self.current_candidate.parent)
            if self.current_candidate
            else (str(self.archive_root) if self.archive_root else "")
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t("heal.dialog.select_donor"),
            init_dir,
            "JPEG Images (*.jpg *.jpeg *.JPG *.JPEG);;All Files (*.*)",
        )
        if file_path:
            self.manual_donor_path = Path(file_path)
            self.chk_auto_donor.setChecked(False)
            self._refresh_donor_label()
            self._reload_preview()

    def get_effective_donor(self) -> Path | None:
        """Resolve effective donor file based on auto/manual mode and current candidate."""
        if self.manual_donor_path and self.manual_donor_path.is_file():
            return self.manual_donor_path

        if self.chk_auto_donor.isChecked() and self.current_candidate:
            return find_matching_donor(self.current_candidate, archive_root=self.archive_root)

        return None

    def _refresh_donor_label(self) -> None:
        """Update donor card display text and metadata."""
        donor = self.get_effective_donor()
        if donor is None:
            self.lbl_donor_status.setText(t("heal.donor.none"))
            self.lbl_donor_status.setStyleSheet("font-size: 11px; color: #a1a1aa;")
            return

        make, model, dim = extract_camera_info(donor)
        dim_str = f"{dim[0]}x{dim[1]}" if dim[0] > 0 else "JPEG"
        cam_info = f" • {make} {model}".strip() if (make or model) else ""

        if self.manual_donor_path:
            text = t("heal.donor.manual", name=donor.name, geometry=f"{dim_str}{cam_info}")
        else:
            text = t("heal.donor.auto_found", name=donor.name, geometry=f"{dim_str}{cam_info}")

        self.lbl_donor_status.setText(text)
        self.lbl_donor_status.setStyleSheet("font-size: 11px; color: #10b981; font-weight: 600;")

    # ── Candidate Selection and Preview Reload ────────────────────────────────

    def _on_candidate_selection_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            self.current_candidate = None
            self.preview_widget.clear()
            self.lbl_file_details.setText(t("heal.preview.no_selection"))
            self.lbl_stats_badge.setVisible(False)
            self._refresh_donor_label()
            return

        cand_str = current.data(Qt.ItemDataRole.UserRole)
        self.current_candidate = Path(cand_str)
        self._refresh_donor_label()
        self._reload_preview()

    def _on_pad_geometry_toggled(self, checked: bool) -> None:
        """Immediate on-the-fly preview reload when pad_geometry is toggled."""
        self._reload_preview()

    def _reload_preview(self) -> None:
        """Trigger on-the-fly reconstruction and render in SplitPreviewWidget."""
        if not self.current_candidate or not self.current_candidate.is_file():
            self.preview_widget.clear()
            return

        donor = self.get_effective_donor()
        pad = self.chk_pad_geometry.isChecked()

        success, status_desc = self.preview_widget.load_comparison(
            candidate_path=self.current_candidate,
            donor_path=donor,
            pad_geometry=pad,
        )

        size_b = self.current_candidate.stat().st_size
        donor_str = donor.name if donor else "None"
        pad_str = "Pad: ON" if pad else "Pad: OFF"

        self.lbl_file_details.setText(
            f"{self.current_candidate.name} ({format_size(size_b)}) • Donor: {donor_str} • {pad_str} • {status_desc}"
        )

        if success:
            self.lbl_stats_badge.setText(status_desc)
            self.lbl_stats_badge.setVisible(True)
        else:
            self.lbl_stats_badge.setVisible(False)

    # ── Zoom UI Callbacks ─────────────────────────────────────────────────────

    def _on_zoom_in(self) -> None:
        self.preview_widget.zoom_in(1.25)

    def _on_zoom_out(self) -> None:
        self.preview_widget.zoom_out(0.8)

    def _on_zoom_fit(self) -> None:
        self.preview_widget.fit_to_view()

    def _on_zoom_100(self) -> None:
        self.preview_widget.reset_zoom()

    def _on_preview_zoom_changed(self, scale: float) -> None:
        pct = int(scale * 100)
        self.lbl_zoom.setText(f"{pct}%")

    # ── Healing Operations ────────────────────────────────────────────────────

    def _heal_current_file(self) -> None:
        """Single file restoration action."""
        if not self.current_candidate or not self.current_candidate.is_file():
            QMessageBox.information(self, t("app.title"), t("heal.preview.no_selection"))
            return

        donor = self.get_effective_donor()
        if donor is None:
            QMessageBox.warning(self, t("app.title"), t("heal.donor.none"))
            return

        default_dest = self.current_candidate.parent / f"{self.current_candidate.stem}_HEALED.jpg"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            t("heal.dialog.save_healed"),
            str(default_dest),
            "JPEG Images (*.jpg *.jpeg *.JPG *.JPEG)",
        )
        if not save_path:
            return

        out_dest = Path(save_path)
        worker = HealWorker(
            candidates=[self.current_candidate],
            donor_path=donor,
            auto_donor=False,
            pad_geometry=self.chk_pad_geometry.isChecked(),
            strip_thumbnail=self.chk_strip_thumbnail.isChecked(),
            create_backup=self.chk_create_backup.isChecked(),
            output_dir=out_dest.parent,
            archive_root=self.archive_root,
            parent=self,
        )

        def on_single_finished(summary: dict):
            if summary.get("healed", 0) > 0:
                self.log_view.appendPlainText(f"[OK] Healed: {out_dest.name}")
                QMessageBox.information(
                    self,
                    t("app.title"),
                    t("heal.log.healed", name=self.current_candidate.name, output=out_dest.name, size=format_size(out_dest.stat().st_size)),
                )
            else:
                QMessageBox.warning(self, t("app.title"), "Healing failed. Check log for details.")

        worker.log_message.connect(self.log_view.appendPlainText)
        worker.finished.connect(on_single_finished)
        worker.start()

    def _start_batch_heal(self) -> None:
        """Batch healing process for all queued candidates."""
        count = self.list_candidates.count()
        if count == 0:
            QMessageBox.information(self, t("app.title"), t("heal.queue.empty"))
            return

        candidates: list[Path] = []
        for i in range(count):
            p_str = self.list_candidates.item(i).data(Qt.ItemDataRole.UserRole)
            candidates.append(Path(p_str))

        donor = self.manual_donor_path if not self.chk_auto_donor.isChecked() else None

        self.btn_heal_batch.setEnabled(False)
        self.btn_heal_single.setEnabled(False)
        self.btn_stop_batch.setVisible(True)
        self.batch_progress.setVisible(True)
        self.batch_progress.setRange(0, count)
        self.batch_progress.setValue(0)

        self.worker = HealWorker(
            candidates=candidates,
            donor_path=donor,
            auto_donor=self.chk_auto_donor.isChecked(),
            pad_geometry=self.chk_pad_geometry.isChecked(),
            strip_thumbnail=self.chk_strip_thumbnail.isChecked(),
            create_backup=self.chk_create_backup.isChecked(),
            archive_root=self.archive_root,
            parent=self,
        )

        self.worker.progress.connect(self._on_batch_progress)
        self.worker.log_message.connect(self.log_view.appendPlainText)
        self.worker.finished.connect(self._on_batch_finished)
        self.worker.start()

    def _on_batch_progress(self, current: int, total: int, filename: str) -> None:
        self.batch_progress.setValue(current)

    def _stop_batch_heal(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop_batch.setEnabled(False)

    def _on_batch_finished(self, summary: dict) -> None:
        self.btn_heal_batch.setEnabled(True)
        self.btn_heal_single.setEnabled(True)
        self.btn_stop_batch.setVisible(False)
        self.btn_stop_batch.setEnabled(True)
        self.batch_progress.setVisible(False)

        healed = summary.get("healed", 0)
        skipped = summary.get("skipped", 0)
        errors = summary.get("errors", 0)
        restored = format_size(summary.get("restored_bytes", 0))

        QMessageBox.information(
            self,
            t("app.title"),
            t("heal.log.batch_done", healed=healed, skipped=skipped, errors=errors)
            + f"\n{restored} total restored data.",
        )
