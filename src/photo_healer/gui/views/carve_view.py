# -*- coding: utf-8 -*-
"""Preview gallery view (APP1/APP2 Carver) for Photo Healer.

Features:
  - Asynchronous non-blocking background extraction via CarveWorker.
  - Adaptive visual grid of preview cards:
      * Scaled thumbnail preview
      * Format badge (APP2 MPF Full HD / APP1 EXIF Thumb / Raw Carved Stream)
      * Frame resolution
      * File size in KB
      * Source filename
      * Interactive selection checkbox
  - Filter controls: type selector (All, MPF, EXIF, Raw) and filename search.
  - Export actions: «Экспортировать выбранные» and «Экспортировать все найденные превью».
  - Full reactive bilingual UI localization (EN/RU).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QByteArray, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from photo_healer.gui.i18n import i18n, t
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.workers.carve_worker import CarveWorker

CARD_WIDTH: int = 210
CARD_HEIGHT: int = 245
CARD_SPACING: int = 12


class PreviewCardWidget(QFrame):
    """Visual card widget representing a single carved preview or thumbnail."""

    selection_changed = Signal(bool)

    def __init__(self, item_data: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_data = item_data
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._is_selected: bool = False
        self._init_ui()
        self._apply_styling()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Top bar: Checkbox + Type Badge
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.chk_select = QCheckBox(self)
        self.chk_select.toggled.connect(self._on_check_toggled)
        top_row.addWidget(self.chk_select)

        preview_type = self.item_data.get("preview_type", "")
        type_label = self.item_data.get("type_label", preview_type.upper())
        self.lbl_badge = QLabel(type_label, self)
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_badge.setObjectName(f"badge_{preview_type}")
        self.lbl_badge.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_row.addWidget(self.lbl_badge)

        layout.addLayout(top_row)

        # 2. Thumbnail image
        self.lbl_thumbnail = QLabel(self)
        self.lbl_thumbnail.setFixedHeight(120)
        self.lbl_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumbnail.setStyleSheet(
            "background-color: #141416; border-radius: 4px; border: 1px solid #27272a;"
        )

        data = self.item_data.get("data", b"")
        pix = QPixmap()
        if data:
            pix.loadFromData(data)

        if not pix.isNull():
            scaled = pix.scaled(
                CARD_WIDTH - 20,
                116,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_thumbnail.setPixmap(scaled)
        else:
            self.lbl_thumbnail.setText("No Preview")
            self.lbl_thumbnail.setStyleSheet("color: #71717a; font-size: 11px;")

        layout.addWidget(self.lbl_thumbnail)

        # 3. Resolution & Size Info
        res_str = self.item_data.get("resolution", "Unknown")
        size_str = self.item_data.get("size_str", "")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)

        self.lbl_resolution = QLabel(res_str, self)
        self.lbl_resolution.setStyleSheet("color: #f4f4f5; font-size: 11px; font-weight: 600;")
        info_row.addWidget(self.lbl_resolution)

        info_row.addStretch()

        self.lbl_size = QLabel(size_str, self)
        self.lbl_size.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        info_row.addWidget(self.lbl_size)

        layout.addLayout(info_row)

        # 4. Source filename
        source_name = self.item_data.get("source_name", "")
        self.lbl_source = QLabel(source_name, self)
        self.lbl_source.setStyleSheet("color: #71717a; font-size: 10px;")
        self.lbl_source.setToolTip(self.item_data.get("source_path", source_name))
        layout.addWidget(self.lbl_source)

    def _apply_styling(self) -> None:
        border_color = "#3b82f6" if self._is_selected else "#27272a"
        bg_color = "#1f2937" if self._is_selected else "#18181b"

        self.setStyleSheet(f"""
            PreviewCardWidget {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            PreviewCardWidget:hover {{
                border: 1px solid #52525b;
            }}
            QLabel#badge_mpf {{
                background-color: #0369a1;
                color: #e0f2fe;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }}
            QLabel#badge_exif_thumb {{
                background-color: #4f46e5;
                color: #e0e7ff;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }}
            QLabel#badge_raw_carved {{
                background-color: #b45309;
                color: #fef3c7;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }}
        """)

    def _on_check_toggled(self, checked: bool) -> None:
        self._is_selected = checked
        self._apply_styling()
        self.selection_changed.emit(checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chk_select.toggle()
        super().mousePressEvent(event)

    def is_selected(self) -> bool:
        return self._is_selected

    def set_selected(self, selected: bool) -> None:
        self.chk_select.setChecked(selected)

    def get_item_data(self) -> dict[str, Any]:
        return self.item_data


class CarveView(QWidget):
    """Interactive visual grid gallery for carved previews and thumbnails."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._archive_path: Path | None = None
        self._worker: CarveWorker | None = None
        self._cards: list[PreviewCardWidget] = []
        self._total_bytes: int = 0

        self._init_ui()
        self._apply_styling()

        # Connect live reactive localization
        i18n.language_changed.connect(self._retranslate_ui)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(12)

        # ── 1. Header Toolbar ─────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Scan / Stop Button
        self.btn_scan = QPushButton(t("carve.btn.extract"))
        self.btn_scan.setObjectName("btnExtract")
        self.btn_scan.clicked.connect(self._toggle_scan)
        toolbar.addWidget(self.btn_scan)

        # Type Filter
        self.combo_filter = QComboBox()
        self.combo_filter.setObjectName("comboFilter")
        self.combo_filter.addItem(t("carve.filter.all"), "all")
        self.combo_filter.addItem(t("carve.filter.mpf"), "mpf")
        self.combo_filter.addItem(t("carve.filter.exif"), "exif_thumb")
        self.combo_filter.addItem(t("carve.filter.raw"), "raw_carved")
        self.combo_filter.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.combo_filter)

        # Filename Search Box
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(t("carve.search.placeholder"))
        self.txt_search.textChanged.connect(self._on_filter_changed)
        self.txt_search.setFixedWidth(200)
        toolbar.addWidget(self.txt_search)

        # Counter / Status
        self.lbl_status = QLabel(t("carve.status.ready"))
        self.lbl_status.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        toolbar.addWidget(self.lbl_status)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedWidth(130)
        self.progress_bar.setVisible(False)
        toolbar.addWidget(self.progress_bar)

        toolbar.addStretch()

        # Selection Buttons
        self.btn_select_all = QPushButton(t("carve.btn.select_all"))
        self.btn_select_all.setObjectName("btnActionSec")
        self.btn_select_all.clicked.connect(lambda: self.select_all(True))
        toolbar.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton(t("carve.btn.deselect_all"))
        self.btn_deselect_all.setObjectName("btnActionSec")
        self.btn_deselect_all.clicked.connect(lambda: self.select_all(False))
        toolbar.addWidget(self.btn_deselect_all)

        # Export Buttons
        self.btn_export_selected = QPushButton(t("carve.btn.export_selected"))
        self.btn_export_selected.setObjectName("btnExportSelected")
        self.btn_export_selected.clicked.connect(self.on_export_selected)
        toolbar.addWidget(self.btn_export_selected)

        self.btn_export_all = QPushButton(t("carve.btn.export_all"))
        self.btn_export_all.setObjectName("btnExportAll")
        self.btn_export_all.clicked.connect(self.on_export_all)
        toolbar.addWidget(self.btn_export_all)

        # Fix Previews Button
        self.btn_fix_previews = QPushButton(t("carve.btn.fix_previews"))
        self.btn_fix_previews.setObjectName("btnActionSec")
        self.btn_fix_previews.clicked.connect(self._open_fix_previews_dialog)
        toolbar.addWidget(self.btn_fix_previews)

        root_layout.addLayout(toolbar)

        # ── 2. Scrollable Grid Area ───────────────────────────────────────────
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("galleryScrollArea")

        self.container_widget = QWidget()
        self.container_widget.setObjectName("galleryContainer")
        self.grid_layout = QGridLayout(self.container_widget)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setSpacing(CARD_SPACING)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Empty state label
        self.lbl_empty = QLabel(t("carve.status.empty"), self.container_widget)
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet("color: #71717a; font-size: 14px; padding: 60px;")
        self.grid_layout.addWidget(self.lbl_empty, 0, 0, 1, 1, Qt.AlignmentFlag.AlignCenter)

        self.scroll_area.setWidget(self.container_widget)
        root_layout.addWidget(self.scroll_area, 1)

    def _apply_styling(self) -> None:
        self.setStyleSheet("""
            QWidget#galleryContainer {
                background-color: #121214;
            }
            QScrollArea#galleryScrollArea {
                background-color: #121214;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
            QPushButton#btnExtract {
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#btnExtract:hover {
                background-color: #2563eb;
            }
            QPushButton#btnExtract.running {
                background-color: #ef4444;
            }
            QPushButton#btnExtract.running:hover {
                background-color: #dc2626;
            }
            QPushButton#btnActionSec {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton#btnActionSec:hover {
                background-color: #3f3f46;
            }
            QPushButton#btnExportSelected {
                background-color: #10b981;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#btnExportSelected:hover {
                background-color: #059669;
            }
            QPushButton#btnExportAll {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#btnExportAll:hover {
                background-color: #0369a1;
            }
            QComboBox#comboFilter {
                background-color: #202024;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
            }
        """)

    def set_archive_path(self, path: Path | str | None) -> None:
        """Set active archive directory path."""
        self._archive_path = Path(path) if path else None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_cards()

    def _relayout_cards(self) -> None:
        """Rearrange visible cards into adaptive grid columns based on viewport width."""
        visible_cards = [c for c in self._cards if not c.isHidden()]

        if not visible_cards:
            self.lbl_empty.setVisible(len(self._cards) == 0)
            return

        self.lbl_empty.setVisible(False)

        viewport_width = self.scroll_area.viewport().width()
        available_width = max(viewport_width - 24, CARD_WIDTH)
        num_cols = max(1, available_width // (CARD_WIDTH + CARD_SPACING))

        for idx, card in enumerate(visible_cards):
            row = idx // num_cols
            col = idx % num_cols
            self.grid_layout.addWidget(card, row, col)

    def _on_filter_changed(self) -> None:
        """Filter cards by type and search query."""
        selected_type = self.combo_filter.currentData()
        search_text = self.txt_search.text().strip().lower()

        for card in self._cards:
            data = card.get_item_data()
            type_match = (selected_type == "all") or (data.get("preview_type") == selected_type)
            search_match = (not search_text) or (search_text in data.get("source_name", "").lower())
            card.setHidden(not (type_match and search_match))

        self._relayout_cards()

    # ── Scanning & Worker Management ──────────────────────────────────────────
    def _toggle_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self.btn_scan.setText(t("carve.btn.extract"))
            self.btn_scan.setProperty("class", "")
            self.btn_scan.style().unpolish(self.btn_scan)
            self.btn_scan.style().polish(self.btn_scan)
        else:
            self.start_scan()

    def start_scan(self, folder: Path | str | None = None) -> None:
        """Start asynchronous preview extraction."""
        target_folder = Path(folder) if folder else self._archive_path
        if not target_folder or not target_folder.is_dir():
            QMessageBox.warning(
                self,
                t("app.title"),
                t("batch_heal.error.folder_not_found", folder=str(target_folder)),
            )
            return

        self.clear()
        self.btn_scan.setText(t("carve.btn.stop"))
        self.btn_scan.setProperty("class", "running")
        self.btn_scan.style().unpolish(self.btn_scan)
        self.btn_scan.style().polish(self.btn_scan)

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_status.setText(t("carve.status.ready"))

        self._worker = CarveWorker(folder_path=target_folder, parent=self)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.preview_found.connect(self.add_preview)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.lbl_status.setText(
            t("carve.status.scanning", current=current, total=total, filename=filename)
        )

    def _on_worker_finished(self, summary: dict[str, Any]) -> None:
        self.btn_scan.setText(t("carve.btn.extract"))
        self.btn_scan.setProperty("class", "")
        self.btn_scan.style().unpolish(self.btn_scan)
        self.btn_scan.style().polish(self.btn_scan)
        self.progress_bar.setVisible(False)

        count = len(self._cards)
        size_str = format_size(self._total_bytes)
        self.lbl_status.setText(t("carve.status.found_summary", count=count, size=size_str))

    # ── Card Management ───────────────────────────────────────────────────────
    def add_preview(self, item_dict: dict[str, Any]) -> None:
        """Add a carved preview record to the gallery grid."""
        card = PreviewCardWidget(item_dict, self.container_widget)
        self._cards.append(card)
        self._total_bytes += item_dict.get("size_bytes", 0)

        # Apply current filter visibility
        selected_type = self.combo_filter.currentData()
        search_text = self.txt_search.text().strip().lower()
        type_match = (selected_type == "all") or (item_dict.get("preview_type") == selected_type)
        search_match = (not search_text) or (search_text in item_dict.get("source_name", "").lower())
        card.setHidden(not (type_match and search_match))

        self._relayout_cards()

        count = len(self._cards)
        size_str = format_size(self._total_bytes)
        self.lbl_status.setText(t("carve.status.found_summary", count=count, size=size_str))

    def card_count(self) -> int:
        return len(self._cards)

    def clear(self) -> None:
        """Clear all cards from the gallery."""
        for card in self._cards:
            self.grid_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._total_bytes = 0
        self.lbl_empty.setVisible(True)
        self.lbl_status.setText(t("carve.status.ready"))

    def select_all(self, selected: bool = True) -> None:
        """Select or deselect all visible preview cards."""
        for card in self._cards:
            if not card.isHidden():
                card.set_selected(selected)

    def get_selected_previews(self) -> list[dict[str, Any]]:
        """Return item data for all selected cards."""
        return [c.get_item_data() for c in self._cards if c.is_selected()]

    def get_all_previews(self) -> list[dict[str, Any]]:
        """Return item data for all cards."""
        return [c.get_item_data() for c in self._cards]

    # ── Export Operations ─────────────────────────────────────────────────────
    def export_previews(
        self,
        items: Sequence[dict[str, Any]],
        dest_dir: Path | str,
    ) -> list[Path]:
        """Save selected or all previews to specified destination directory."""
        destination = Path(dest_dir)
        destination.mkdir(parents=True, exist_ok=True)

        exported_paths: list[Path] = []
        for item in items:
            data = item.get("data", b"")
            if not data:
                continue

            src_name = item.get("source_name", "preview.jpg")
            stem = Path(src_name).stem
            ptype = item.get("preview_type", "preview")

            filename = f"{stem}_{ptype}.jpg"
            out_path = destination / filename
            counter = 1
            while out_path.exists():
                out_path = destination / f"{stem}_{ptype}_{counter}.jpg"
                counter += 1

            out_path.write_bytes(data)
            exported_paths.append(out_path)

        return exported_paths

    def on_export_selected(self) -> None:
        """Handle «Экспортировать выбранные» action."""
        selected = self.get_selected_previews()
        if not selected:
            QMessageBox.information(
                self,
                t("app.title"),
                t("carve.export.no_selection"),
            )
            return

        default_dest = str(self._archive_path / "_Previews") if self._archive_path else ""
        chosen = QFileDialog.getExistingDirectory(
            self,
            t("carve.export.dialog_title"),
            default_dest,
        )
        if not chosen:
            return

        exported = self.export_previews(selected, chosen)
        QMessageBox.information(
            self,
            t("app.title"),
            t("carve.export.success", count=len(exported), path=chosen),
        )

    def on_export_all(self) -> None:
        """Handle «Экспортировать все найденные превью» action."""
        all_items = self.get_all_previews()
        if not all_items:
            QMessageBox.information(
                self,
                t("app.title"),
                t("carve.export.no_selection"),
            )
            return

        default_dest = str(self._archive_path / "_Previews") if self._archive_path else ""
        chosen = QFileDialog.getExistingDirectory(
            self,
            t("carve.export.dialog_title"),
            default_dest,
        )
        if not chosen:
            return

        exported = self.export_previews(all_items, chosen)
        QMessageBox.information(
            self,
            t("app.title"),
            t("carve.export.success", count=len(exported), path=chosen),
        )

    def _open_fix_previews_dialog(self) -> None:
        """Open the thumbnail fix dialog with the current archive folder prefilled."""
        from photo_healer.gui.views.thumbnail_dialog import ThumbnailFixDialog
        dlg = ThumbnailFixDialog(target_folder=self._archive_path, parent=self)
        dlg.exec()

    def _retranslate_ui(self) -> None:
        """Reactively translate all labels and buttons on language change."""
        if self._worker and self._worker.isRunning():
            self.btn_scan.setText(t("carve.btn.stop"))
        else:
            self.btn_scan.setText(t("carve.btn.extract"))

        self.btn_select_all.setText(t("carve.btn.select_all"))
        self.btn_deselect_all.setText(t("carve.btn.deselect_all"))
        self.btn_export_selected.setText(t("carve.btn.export_selected"))
        self.btn_export_all.setText(t("carve.btn.export_all"))
        self.btn_fix_previews.setText(t("carve.btn.fix_previews"))

        self.txt_search.setPlaceholderText(t("carve.search.placeholder"))
        self.lbl_empty.setText(t("carve.status.empty"))

        # Update filter items without resetting selection
        current_data = self.combo_filter.currentData()
        self.combo_filter.blockSignals(True)
        self.combo_filter.clear()
        self.combo_filter.addItem(t("carve.filter.all"), "all")
        self.combo_filter.addItem(t("carve.filter.mpf"), "mpf")
        self.combo_filter.addItem(t("carve.filter.exif"), "exif_thumb")
        self.combo_filter.addItem(t("carve.filter.raw"), "raw_carved")
        idx = self.combo_filter.findData(current_data)
        if idx >= 0:
            self.combo_filter.setCurrentIndex(idx)
        self.combo_filter.blockSignals(False)

        count = len(self._cards)
        if count > 0:
            size_str = format_size(self._total_bytes)
            self.lbl_status.setText(t("carve.status.found_summary", count=count, size=size_str))
        else:
            self.lbl_status.setText(t("carve.status.ready"))
