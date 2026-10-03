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
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QIcon
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
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from photo_healer.gui.i18n import get_language, i18n, set_language, t
from photo_healer.gui.icon import get_app_icon
from photo_healer.gui.models.file_table_model import format_size
from photo_healer.gui.views.carve_view import CarveView
from photo_healer.gui.views.heal_view import HealView
from photo_healer.gui.views.triage_view import TriageView
from photo_healer.gui.workers.triage_worker import TriageWorker

DARK_THEME_QSS: str = """
QMainWindow, QWidget#centralWidget {
    background-color: #121214;
    color: #f4f4f5;
}

/* ── Menu Bar & Dropdowns ── */
QMenuBar {
    background-color: #18181b;
    color: #f4f4f5;
    border-bottom: 1px solid #27272a;
    padding: 2px 6px;
    font-size: 12px;
}
QMenuBar::item {
    background-color: transparent;
    padding: 4px 8px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #27272a;
}
QMenu {
    background-color: #18181b;
    color: #f4f4f5;
    border: 1px solid #27272a;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 18px;
    border-radius: 4px;
    font-size: 12px;
}
QMenu::item:selected {
    background-color: #2563eb;
    color: #ffffff;
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

    def __init__(self, initial_folder: Path | str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker: TriageWorker | None = None
        self._total_bytes: int = 0
        self._total_files: int = 0
        self._selected_path: Path | None = None

        self.setWindowTitle(t("app.title"))
        self.resize(1080, 720)
        self.setMinimumSize(850, 520)

        # Set window icon
        app_icon = get_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        # Enable Drag-and-Drop
        self.setAcceptDrops(True)

        self._init_ui()
        self._apply_styling()

        if initial_folder:
            self.set_folder(initial_folder)

        # Connect live reactive localization
        i18n.language_changed.connect(self._retranslate_ui)
        self._update_responsive_layout(self.width())

    def _init_ui(self) -> None:
        # ── 0. Top Menu Bar ───────────────────────────────────────────────────
        self.menu_bar = self.menuBar()
        self.menu_tools = self.menu_bar.addMenu(t("menu.tools"))
        self.act_fix_previews = self.menu_tools.addAction(t("tools.fix_previews"))
        self.act_fix_previews.triggered.connect(self._open_fix_previews_dialog)
        self.act_clear_cache = self.menu_tools.addAction(t("tools.clear_cache"))
        self.act_clear_cache.triggered.connect(self._on_clear_cache_triggered)

        # Language Menu (shown when window width < 700)
        self.menu_language = self.menu_bar.addMenu(t("lang.switch"))
        self.lang_action_group = QActionGroup(self)
        self.lang_action_group.setExclusive(True)

        self.act_lang_ru = self.menu_language.addAction("Русский (RU)")
        self.act_lang_ru.setCheckable(True)
        self.lang_action_group.addAction(self.act_lang_ru)
        self.act_lang_ru.triggered.connect(lambda: self._set_language_from_menu("ru"))

        self.act_lang_en = self.menu_language.addAction("English (EN)")
        self.act_lang_en.setCheckable(True)
        self.lang_action_group.addAction(self.act_lang_en)
        self.act_lang_en.triggered.connect(lambda: self._set_language_from_menu("en"))

        self._sync_language_menu()
        self.menu_language.menuAction().setVisible(False)

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
        self.lbl_folder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top_layout.addWidget(self.lbl_folder)

        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText(t("folder.placeholder"))
        self.txt_folder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_folder.setMinimumWidth(120)
        self.txt_folder.textChanged.connect(self._on_folder_text_changed)
        top_layout.addWidget(self.txt_folder, 1)

        self.btn_browse = QPushButton(t("folder.browse"))
        self.btn_browse.setObjectName("btnBrowse")
        self.btn_browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_browse.clicked.connect(self._browse_folder)
        top_layout.addWidget(self.btn_browse)

        self.btn_scan = QPushButton(t("folder.scan"))
        self.btn_scan.setObjectName("btnScan")
        self.btn_scan.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_scan.clicked.connect(self._toggle_scan)
        top_layout.addWidget(self.btn_scan)

        # Language Switcher (in top bar for width >= 700)
        self.combo_lang = QComboBox()
        self.combo_lang.setObjectName("langSwitcher")
        self.combo_lang.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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

        # Tab 2: Recovery (Heal View)
        self.heal_view = HealView(self)
        self.recovery_tab = self.heal_view  # Retain attribute for backward compatibility
        self.tabs.addTab(self.heal_view, t("tab.recovery"))

        # Tab 3: Preview Gallery (Carve View)
        self.carve_view = CarveView(self)
        self.gallery_tab = self.carve_view
        self.tabs.addTab(self.carve_view, t("tab.gallery"))

        # Double-click on candidate in triage switches to heal tab
        self.triage_view.table_view.doubleClicked.connect(self._on_triage_row_double_clicked)

        root_layout.addWidget(self.tabs, 1)

        # ── 3. Bottom Status Bar & Metrics ────────────────────────────────────
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        self.lbl_status = QLabel(t("status.ready"))
        self.lbl_status.setStyleSheet("color: #60a5fa; font-weight: 500;")
        self.lbl_status.setMinimumWidth(80)
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.status_bar.addWidget(self.lbl_status, 1)

        self.lbl_metric_files = QLabel(t("metric.files", count=0))
        self.lbl_metric_files.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.status_bar.addPermanentWidget(self.lbl_metric_files)

        self.lbl_metric_size = QLabel(t("metric.total_size", size=format_size(0)))
        self.lbl_metric_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.status_bar.addPermanentWidget(self.lbl_metric_size)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimumWidth(100)
        self.progress_bar.setMaximumWidth(180)
        self.progress_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
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

        self.menu_tools.setTitle(t("menu.tools"))
        self.act_fix_previews.setText(t("tools.fix_previews"))
        self.act_clear_cache.setText(t("tools.clear_cache"))

        self.lbl_metric_files.setText(t("metric.files", count=self._total_files))
        self.lbl_metric_size.setText(t("metric.total_size", size=format_size(self._total_bytes)))

        # Update combo box active selection if needed without recursion
        cur_lang = get_language()
        idx = self.combo_lang.findData(cur_lang)
        if idx >= 0 and self.combo_lang.currentIndex() != idx:
            self.combo_lang.blockSignals(True)
            self.combo_lang.setCurrentIndex(idx)
            self.combo_lang.blockSignals(False)
        self._sync_language_menu()

    def _set_language_from_menu(self, lang: str) -> None:
        """Set language from menu action and ensure menu checks are synced."""
        set_language(lang)
        self._sync_language_menu()

    def _sync_language_menu(self) -> None:
        """Keep the menu bar language items in sync with active language."""
        cur_lang = get_language()
        if hasattr(self, "act_lang_ru"):
            self.act_lang_ru.blockSignals(True)
            self.act_lang_ru.setChecked(cur_lang == "ru")
            self.act_lang_ru.blockSignals(False)
        if hasattr(self, "act_lang_en"):
            self.act_lang_en.blockSignals(True)
            self.act_lang_en.setChecked(cur_lang == "en")
            self.act_lang_en.blockSignals(False)
        if hasattr(self, "menu_language"):
            self.menu_language.setTitle(t("lang.switch"))

    def resizeEvent(self, event) -> None:
        """Handle adaptive UI transitions on window resize."""
        super().resizeEvent(event)
        self._update_responsive_layout(self.width())

    def _update_responsive_layout(self, width: int) -> None:
        """Adapt top bar and menu items based on window width."""
        is_compact = width < 700
        if hasattr(self, "combo_lang"):
            self.combo_lang.setVisible(not is_compact)
        if hasattr(self, "menu_language"):
            self.menu_language.menuAction().setVisible(is_compact)

    def _open_fix_previews_dialog(self) -> None:
        """Open the thumbnail fix dialog."""
        from photo_healer.gui.views.thumbnail_dialog import ThumbnailFixDialog
        dlg = ThumbnailFixDialog(target_folder=self._selected_path, parent=self)
        dlg.exec()

    def _on_clear_cache_triggered(self) -> None:
        """Trigger instant Windows Explorer icon and thumbnail cache reset."""
        from photo_healer.core.thumbnail import clear_windows_thumbnail_cache
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

    def _on_language_switched(self, index: int) -> None:
        lang_code = self.combo_lang.itemData(index)
        if lang_code:
            set_language(lang_code)

    def set_folder(self, folder_path: Path | str) -> None:
        """Set the active archive folder programmatically."""
        p = Path(folder_path)
        if p.is_dir():
            self.txt_folder.setText(str(p.resolve()))
            self._on_folder_text_changed(str(p.resolve()))

    def _on_folder_text_changed(self, text: str) -> None:
        clean = text.strip().strip('"').strip("'")
        p = Path(clean) if clean else None
        if p and p.is_dir():
            self._selected_path = p
            self.triage_view.set_archive_path(p)
            self.heal_view.set_archive_path(p)
            self.carve_view.set_archive_path(p)

    def _on_triage_row_double_clicked(self, index) -> None:
        """Double click on triage table candidate jumps to heal tab."""
        source_index = self.triage_view.proxy_model.mapToSource(index)
        row = source_index.row()
        item = self.triage_view.table_model.get_item(row)
        if item and item.get("status") == "healed_candidate":
            self.heal_view.add_candidate(item["path"], select=True)
            self.tabs.setCurrentIndex(1)

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
            self.heal_view.set_archive_path(self._selected_path)
            self.carve_view.set_archive_path(self._selected_path)

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
                        self.heal_view.set_archive_path(path)
                        self.carve_view.set_archive_path(path)
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
        self.heal_view.set_archive_path(folder)
        self.heal_view.clear_candidates()

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

        # Suspend dynamic sorting on triage table while loading large volume of files
        self.triage_view.proxy_model.setDynamicSortFilter(False)

        # Launch background worker
        self.worker = TriageWorker(folder, parent=self)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.batch_found.connect(self._on_worker_batch_found)
        self.worker.discovering.connect(self._on_worker_discovering)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _stop_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            self.lbl_status.setText(t("status.cancelled", count=self._total_files))
            self.worker.stop()
            self.btn_scan.setEnabled(False)
            self.triage_view.proxy_model.setDynamicSortFilter(True)
            self.triage_view.proxy_model.invalidate()

    def _on_worker_discovering(self, count: int, current_dir: str) -> None:
        self.lbl_status.setText(t("status.discovering", count=count, folder=current_dir))
        self.lbl_metric_files.setText(t("metric.files", count=count))

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
        self._on_worker_batch_found([record])

    def _on_worker_batch_found(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        self._total_files += len(batch)
        batch_size = sum(rec.get("size", 0) for rec in batch)
        self._total_bytes += batch_size

        self.triage_view.add_file_records(batch)

        for record in batch:
            if record.get("status") == "healed_candidate":
                self.heal_view.add_candidate(
                    record["path"],
                    select=(self.heal_view.current_candidate is None),
                )

        self.lbl_metric_files.setText(t("metric.files", count=self._total_files))
        self.lbl_metric_size.setText(t("metric.total_size", size=format_size(self._total_bytes)))

    def _on_worker_finished(self, summary: dict[str, Any]) -> None:
        # Re-enable dynamic sorting and refresh proxy view
        self.triage_view.proxy_model.setDynamicSortFilter(True)
        self.triage_view.proxy_model.invalidate()

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
