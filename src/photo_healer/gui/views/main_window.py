# -*- coding: utf-8 -*-
"""Main adaptive desktop window for Photo Healer.

Features unified single-window layout without modal clutter:
  - Top bar: archive folder selector (Browse + Drag-and-Drop) & RU/EN language switcher
  - Central tab widget: Diagnostics (triage), Recovery, and Preview Gallery
  - Bottom status bar: metrics (total volume, file count, scan status) & live progress bar
  - High-contrast native dark theme styling
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileInfo, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from photo_healer.gui.i18n import get_language, i18n, set_language, t
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.views.triage_view import TriageView
from photo_healer.gui.workers.triage_worker import TriageWorker

DARK_THEME_QSS: str = """
QMainWindow, QWidget#centralWidget {
    background-color: #121214;
    color: #f4f4f5;
}

/* ── Top Bar & Panels ── */
QFrame#topBar {
    background-color: #18181b;
    border-bottom: 1px solid #27272a;
    padding: 8px 12px;
}

QLineEdit {
    background-color: #202024;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #3b82f6;
}

QPushButton#btnBrowse {
    background-color: #27272a;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#btnBrowse:hover {
    background-color: #3f3f46;
}

QPushButton#btnScan {
    background-color: #3b82f6;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 7px 20px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton#btnScan:hover {
    background-color: #2563eb;
}
QPushButton#btnScan:pressed {
    background-color: #1d4ed8;
}

QPushButton#btnScan.running {
    background-color: #ef4444;
}
QPushButton#btnScan.running:hover {
    background-color: #dc2626;
}

QComboBox#langSwitcher {
    background-color: #202024;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 600;
}
QComboBox#langSwitcher::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #202024;
    color: #f4f4f5;
    selection-background-color: #3b82f6;
    selection-color: #ffffff;
    border: 1px solid #3f3f46;
}

/* ── Tab Widget ── */
QTabWidget::pane {
    border: 1px solid #27272a;
    background-color: #18181b;
    top: -1px;
}
QTabBar::tab {
    background-color: #121214;
    color: #a1a1aa;
    padding: 9px 20px;
    font-size: 13px;
    font-weight: 500;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    border: 1px solid transparent;
}
QTabBar::tab:hover {
    color: #f4f4f5;
    background-color: #18181b;
}
QTabBar::tab:selected {
    background-color: #18181b;
    color: #3b82f6;
    border: 1px solid #27272a;
    border-bottom: 1px solid #18181b;
    font-weight: bold;
}

/* ── Status Bar ── */
QStatusBar {
    background-color: #18181b;
    color: #a1a1aa;
    border-top: 1px solid #27272a;
    font-size: 12px;
}
QStatusBar QLabel {
    color: #a1a1aa;
    padding: 2px 8px;
}
QProgressBar {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 4px;
    text-align: center;
    color: #ffffff;
    font-size: 11px;
    height: 14px;
    max-height: 14px;
}
QProgressBar::chunk {
    background-color: #3b82f6;
    border-radius: 3px;
}

/* ── Scrollbars ── */
QScrollBar:vertical {
    background-color: #18181b;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #3f3f46;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #52525b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #18181b;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #3f3f46;
    border-radius: 5px;
    min-width: 20px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #52525b;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
"""


class MainWindow(QMainWindow):
    """Main application window for Photo Healer forensic suite."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker: TriageWorker | None = None
        self._total_bytes: int = 0
        self._total_files: int = 0
        self._selected_path: Path | None = None

        self.setWindowTitle(t("app.title"))
        self.resize(1080, 720)
        self.setMinimumSize(850, 520)

        # Enable Drag-and-Drop
        self.setAcceptDrops(True)

        self._init_ui()
        self._apply_styling()

        # Connect live reactive localization
        i18n.language_changed.connect(self._retranslate_ui)

    def _init_ui(self) -> None:
        central_widget = QWidget(self)
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── 1. Top Folder Selection Bar ───────────────────────────────────────
        self.top_bar = QFrame(self)
        self.top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(10)

        self.lbl_folder = QLabel(t("folder.label"))
        self.lbl_folder.setStyleSheet("font-size: 13px; font-weight: 600; color: #f4f4f5;")
        top_layout.addWidget(self.lbl_folder)

        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText(t("folder.placeholder"))
        self.txt_folder.textChanged.connect(self._on_folder_text_changed)
        top_layout.addWidget(self.txt_folder, 1)

        self.btn_browse = QPushButton(t("folder.browse"))
        self.btn_browse.setObjectName("btnBrowse")
        self.btn_browse.clicked.connect(self._browse_folder)
        top_layout.addWidget(self.btn_browse)

        self.btn_scan = QPushButton(t("folder.scan"))
        self.btn_scan.setObjectName("btnScan")
        self.btn_scan.clicked.connect(self._toggle_scan)
        top_layout.addWidget(self.btn_scan)

        # Language Switcher
        self.combo_lang = QComboBox()
        self.combo_lang.setObjectName("langSwitcher")
        self.combo_lang.addItem("RU", "ru")
        self.combo_lang.addItem("EN", "en")

        cur_lang = get_language()
        idx = self.combo_lang.findData(cur_lang)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._on_language_switched)
        top_layout.addWidget(self.combo_lang)

        root_layout.addWidget(self.top_bar)

        # ── 2. Central Tabbed Workspace ───────────────────────────────────────
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)

        # Tab 1: Diagnostics (Triage View)
        self.triage_view = TriageView(self)
        self.tabs.addTab(self.triage_view, t("tab.diagnostics"))

        # Tab 2: Recovery (Placeholder / Info view)
        self.recovery_tab = self._create_placeholder_tab(
            "recovery.title",
            "recovery.desc",
        )
        self.tabs.addTab(self.recovery_tab, t("tab.recovery"))

        # Tab 3: Preview Gallery (Placeholder / Info view)
        self.gallery_tab = self._create_placeholder_tab(
            "gallery.title",
            "gallery.desc",
        )
        self.tabs.addTab(self.gallery_tab, t("tab.gallery"))

        root_layout.addWidget(self.tabs, 1)

        # ── 3. Bottom Status Bar & Metrics ────────────────────────────────────
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        self.lbl_status = QLabel(t("status.ready"))
        self.lbl_status.setStyleSheet("color: #60a5fa; font-weight: 500;")
        self.status_bar.addWidget(self.lbl_status, 1)

        self.lbl_metric_files = QLabel(t("metric.files", count=0))
        self.status_bar.addPermanentWidget(self.lbl_metric_files)

        self.lbl_metric_size = QLabel(t("metric.total_size", size=format_size(0)))
        self.status_bar.addPermanentWidget(self.lbl_metric_size)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    def _create_placeholder_tab(self, title_key: str, desc_key: str) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        title_lbl = QLabel(t(title_key))
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #f4f4f5;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc_lbl = QLabel(t(desc_key))
        desc_lbl.setStyleSheet("font-size: 13px; color: #a1a1aa;")
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setWordWrap(True)

        layout.addWidget(title_lbl)
        layout.addWidget(desc_lbl)
        widget.setProperty("title_key", title_key)
        widget.setProperty("desc_key", desc_key)
        widget.setProperty("title_lbl", title_lbl)
        widget.setProperty("desc_lbl", desc_lbl)
        return widget

    def _apply_styling(self) -> None:
        self.setStyleSheet(DARK_THEME_QSS)

    def _retranslate_ui(self) -> None:
        """Reactive translation of all window components without restarting."""
        self.setWindowTitle(t("app.title"))
        self.lbl_folder.setText(t("folder.label"))
        self.txt_folder.setPlaceholderText(t("folder.placeholder"))
        self.btn_browse.setText(t("folder.browse"))

        if self.worker and self.worker.isRunning():
            self.btn_scan.setText(t("folder.cancel"))
        else:
            self.btn_scan.setText(t("folder.scan"))

        self.tabs.setTabText(0, t("tab.diagnostics"))
        self.tabs.setTabText(1, t("tab.recovery"))
        self.tabs.setTabText(2, t("tab.gallery"))

        # Update placeholder tabs
        for tab in (self.recovery_tab, self.gallery_tab):
            title_k = tab.property("title_key")
            desc_k = tab.property("desc_key")
            title_l = tab.property("title_lbl")
            desc_l = tab.property("desc_lbl")
            if title_k and title_l:
                title_l.setText(t(title_k))
            if desc_k and desc_l:
                desc_l.setText(t(desc_k))

        self.lbl_metric_files.setText(t("metric.files", count=self._total_files))
        self.lbl_metric_size.setText(t("metric.total_size", size=format_size(self._total_bytes)))

        # Update combo box active selection if needed without recursion
        cur_lang = get_language()
        idx = self.combo_lang.findData(cur_lang)
        if idx >= 0 and self.combo_lang.currentIndex() != idx:
            self.combo_lang.blockSignals(True)
            self.combo_lang.setCurrentIndex(idx)
            self.combo_lang.blockSignals(False)

    def _on_language_switched(self, index: int) -> None:
        lang_code = self.combo_lang.itemData(index)
        if lang_code:
            set_language(lang_code)

    def _on_folder_text_changed(self, text: str) -> None:
        clean = text.strip().strip('"').strip("'")
        p = Path(clean) if clean else None
        if p and p.is_dir():
            self._selected_path = p
            self.triage_view.set_archive_path(p)

    def _browse_folder(self) -> None:
        default_dir = str(self._selected_path) if self._selected_path else ""
        chosen = QFileDialog.getExistingDirectory(
            self,
            t("folder.dialog_title"),
            default_dir,
        )
        if chosen:
            self.txt_folder.setText(chosen)
            self._selected_path = Path(chosen)
            self.triage_view.set_archive_path(self._selected_path)

    # ── Drag and Drop Support ─────────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.is_dir():
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.is_dir():
                        self.txt_folder.setText(str(path))
                        self._selected_path = path
                        self.triage_view.set_archive_path(path)
                        event.acceptProposedAction()
                        return
        event.ignore()

    # ── Scan Control ──────────────────────────────────────────────────────────
    def _toggle_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self) -> None:
        raw_path = self.txt_folder.text().strip().strip('"').strip("'")
        if not raw_path:
            QMessageBox.warning(self, t("app.title"), t("folder.placeholder"))
            return

        folder = Path(raw_path)
        if not folder.is_dir():
            QMessageBox.warning(
                self,
                t("app.title"),
                f"Path is not a valid directory: {folder}",
            )
            return

        self._selected_path = folder
        self.triage_view.set_archive_path(folder)
        self.triage_view.reset_data()

        self._total_bytes = 0
        self._total_files = 0
        self.lbl_metric_files.setText(t("metric.files", count=0))
        self.lbl_metric_size.setText(t("metric.total_size", size=format_size(0)))

        # Update button state
        self.btn_scan.setText(t("folder.cancel"))
        self.btn_scan.setProperty("class", "running")
        self.btn_scan.setStyleSheet("background-color: #ef4444; color: white;")
        self.btn_browse.setEnabled(False)
        self.txt_folder.setEnabled(False)

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until total is known

        # Launch background worker
        self.worker = TriageWorker(folder, parent=self)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.file_found.connect(self._on_worker_file_found)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _stop_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            self.lbl_status.setText(t("status.cancelled", count=self._total_files))
            self.worker.stop()
            self.btn_scan.setEnabled(False)

    def _on_worker_progress(self, current: int, total: int, filename: str) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.lbl_status.setText(
            t(
                "status.scanning",
                current=current,
                total=total,
                filename=filename,
            )
        )

    def _on_worker_file_found(self, record: dict[str, Any]) -> None:
        self._total_files += 1
        self._total_bytes += record.get("size", 0)
        self.triage_view.add_file_record(record)
        self.lbl_metric_files.setText(t("metric.files", count=self._total_files))
        self.lbl_metric_size.setText(t("metric.total_size", size=format_size(self._total_bytes)))

    def _on_worker_finished(self, summary: dict[str, Any]) -> None:
        self.progress_bar.setVisible(False)
        self.btn_scan.setText(t("folder.scan"))
        self.btn_scan.setProperty("class", "")
        self.btn_scan.setStyleSheet("background-color: #3b82f6; color: white;")
        self.btn_scan.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.txt_folder.setEnabled(True)

        status = summary.get("status", "completed")
        scanned = summary.get("scanned_files", self._total_files)
        total_sz = summary.get("total_size", self._total_bytes)

        if status == "cancelled":
            self.lbl_status.setText(t("status.cancelled", count=scanned))
        elif status == "error":
            err = summary.get("error", "Unknown error")
            self.lbl_status.setText(t("status.error", error=err))
        else:
            self.lbl_status.setText(
                t(
                    "status.completed",
                    count=scanned,
                    size=format_size(total_sz),
                )
            )
