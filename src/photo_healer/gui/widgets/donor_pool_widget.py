# -*- coding: utf-8 -*-
"""Donor Pool Manager widget with multi-mode donor selection and background indexing.

Features:
  - Mode 'file' (legacy): Single JPEG donor selection via Drag & Drop or file dialog.
  - Mode 'folder': Manage list of donor folders with background asynchronous indexing.
  - Mode 'auto': Automated discovery of camera photo folders with candidate metrics & selection.
  - Live compatibility indicator evaluating best donor match for active candidate.
  - Inter-session persistence of pool configuration and indexed donor cache via QSettings / settings.json.
  - Full RU/EN localization reactivity via i18n.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QFileInfo, QPoint, QSettings, QSize, Qt, Signal, Slot
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
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from photo_healer.core.donor_discovery import DonorDiscovery, DonorFolderCandidate
from photo_healer.core.donor_pool import DonorIndex, DonorMatch, extract_filename_prefix
from photo_healer.gui.i18n import i18n, t
from photo_healer.gui.workers.donor_discover_worker import DonorDiscoverWorker
from photo_healer.gui.workers.donor_index_worker import DonorIndexWorker
from photo_healer.gui.workers.heal_worker import extract_camera_info

SETTINGS_DIR = Path.home() / ".photo-healer"
SETTINGS_FILE = SETTINGS_DIR / "donor_pool_settings.json"
DEFAULT_CACHE_POOL = SETTINGS_DIR / "donor_pool.json"


class DonorDropBox(QFrame):
    """Drop zone and card for single donor JPEG files."""

    donor_dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("donorCard")
        self.setMinimumHeight(56)
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


class DonorPoolWidget(QFrame):
    """Panel for managing donor files, folders, and automated pool discovery."""

    donor_changed = Signal()
    pool_updated = Signal(int, int)  # (donors_count, folders_count)

    def __init__(self, parent: QWidget | None = None, autoload_settings: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("donorPoolWidget")
        self._is_initialized: bool = False

        # State
        self.current_mode: str = "folder"  # 'file', 'folder', 'auto'
        self.manual_donor_path: Path | None = None
        self.current_candidate: Path | None = None
        self.archive_root: Path | None = None
        self.folder_paths: list[Path] = []
        self.donor_index: DonorIndex = DonorIndex()

        # Background workers
        self._index_worker: DonorIndexWorker | None = None
        self._discover_worker: DonorDiscoverWorker | None = None

        self._init_ui()
        self._apply_styling()

        # Connect live reactive localization
        i18n.language_changed.connect(self.retranslate_ui)

        # Restore previous session settings
        if autoload_settings:
            self.load_settings()
        self._is_initialized = True

    @property
    def chk_auto_donor(self) -> QCheckBox:
        """Compatibility property for legacy tests accessing view.chk_auto_donor."""
        return self._compat_chk_auto

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── 1. Header & Mode Switcher ─────────────────────────────────────────
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.lbl_title = QLabel(t("donor.pool.title"))
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #f4f4f5;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch(1)

        # Mode button group
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        self.btn_mode_file = QPushButton(t("donor.mode.file"))
        self.btn_mode_file.setCheckable(True)
        self.btn_mode_file.setObjectName("btnModeSegment")
        self.btn_mode_file.setToolTip(t("donor.mode.file_tip"))
        self.mode_group.addButton(self.btn_mode_file, 0)
        header_layout.addWidget(self.btn_mode_file)

        self.btn_mode_folder = QPushButton(t("donor.mode.folder"))
        self.btn_mode_folder.setCheckable(True)
        self.btn_mode_folder.setObjectName("btnModeSegment")
        self.btn_mode_folder.setToolTip(t("donor.mode.folder_tip"))
        self.mode_group.addButton(self.btn_mode_folder, 1)
        header_layout.addWidget(self.btn_mode_folder)

        self.btn_mode_auto = QPushButton(t("donor.mode.auto"))
        self.btn_mode_auto.setCheckable(True)
        self.btn_mode_auto.setObjectName("btnModeSegment")
        self.btn_mode_auto.setToolTip(t("donor.mode.auto_tip"))
        self.mode_group.addButton(self.btn_mode_auto, 2)
        header_layout.addWidget(self.btn_mode_auto)

        main_layout.addLayout(header_layout)

        # Hidden checkbox for backward compatibility with legacy tests
        self._compat_chk_auto = QCheckBox(self)
        self._compat_chk_auto.setVisible(False)
        self._compat_chk_auto.setChecked(True)
        self._compat_chk_auto.toggled.connect(self._on_compat_chk_toggled)

        # ── 2. Stacked Content Pages ──────────────────────────────────────────
        self.stack = QStackedWidget(self)

        # 2.1 File Mode Page
        self.page_file = QWidget()
        page_file_layout = QVBoxLayout(self.page_file)
        page_file_layout.setContentsMargins(0, 0, 0, 0)
        page_file_layout.setSpacing(6)

        self.donor_drop_card = DonorDropBox(self.page_file)
        drop_layout = QVBoxLayout(self.donor_drop_card)
        drop_layout.setContentsMargins(10, 8, 10, 8)
        drop_layout.setSpacing(4)

        self.lbl_file_donor_status = QLabel(t("heal.donor.none"))
        self.lbl_file_donor_status.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        self.lbl_file_donor_status.setWordWrap(True)
        drop_layout.addWidget(self.lbl_file_donor_status)

        self.lbl_donor_drop_hint = QLabel(t("heal.donor.drop_prompt"))
        self.lbl_donor_drop_hint.setStyleSheet("font-size: 10px; color: #71717a; font-style: italic;")
        drop_layout.addWidget(self.lbl_donor_drop_hint)
        self.donor_drop_card.donor_dropped.connect(self.set_manual_donor_file)

        page_file_layout.addWidget(self.donor_drop_card)

        self.btn_browse_donor = QPushButton(t("heal.donor.browse"))
        self.btn_browse_donor.setObjectName("btnBrowseDonor")
        self.btn_browse_donor.clicked.connect(self._browse_donor_file)
        page_file_layout.addWidget(self.btn_browse_donor)

        self.stack.addWidget(self.page_file)

        # 2.2 Folder Mode Page
        self.page_folder = QWidget()
        page_folder_layout = QVBoxLayout(self.page_folder)
        page_folder_layout.setContentsMargins(0, 0, 0, 0)
        page_folder_layout.setSpacing(6)

        folder_btn_bar = QHBoxLayout()
        folder_btn_bar.setSpacing(6)

        self.btn_add_donor_folder = QPushButton(t("donor.btn.add_folder"))
        self.btn_add_donor_folder.setObjectName("btnActionSecondary")
        self.btn_add_donor_folder.clicked.connect(self._browse_add_donor_folder)
        folder_btn_bar.addWidget(self.btn_add_donor_folder)

        self.btn_remove_donor_folder = QPushButton(t("donor.btn.remove_folder"))
        self.btn_remove_donor_folder.setObjectName("btnActionSecondary")
        self.btn_remove_donor_folder.clicked.connect(self._remove_selected_folder)
        folder_btn_bar.addWidget(self.btn_remove_donor_folder)

        self.btn_clear_pool = QPushButton(t("donor.btn.clear_pool"))
        self.btn_clear_pool.setObjectName("btnActionDanger")
        self.btn_clear_pool.clicked.connect(self._clear_entire_pool)
        folder_btn_bar.addWidget(self.btn_clear_pool)

        page_folder_layout.addLayout(folder_btn_bar)

        self.list_folders = QListWidget(self.page_folder)
        self.list_folders.setObjectName("listDonorFolders")
        self.list_folders.setMinimumHeight(90)
        self.list_folders.setMaximumHeight(140)
        page_folder_layout.addWidget(self.list_folders)

        self.stack.addWidget(self.page_folder)

        # 2.3 Auto Mode Page
        self.page_auto = QWidget()
        page_auto_layout = QVBoxLayout(self.page_auto)
        page_auto_layout.setContentsMargins(0, 0, 0, 0)
        page_auto_layout.setSpacing(6)

        auto_btn_bar = QHBoxLayout()
        auto_btn_bar.setSpacing(6)

        self.btn_auto_discover = QPushButton(f"🔍 {t('donor.btn.auto_discover')}")
        self.btn_auto_discover.setObjectName("btnActionPrimary")
        self.btn_auto_discover.clicked.connect(self.start_auto_discovery)
        auto_btn_bar.addWidget(self.btn_auto_discover)

        self.btn_cancel_discover = QPushButton(t("donor.btn.cancel_discover"))
        self.btn_cancel_discover.setObjectName("btnActionDanger")
        self.btn_cancel_discover.setVisible(False)
        self.btn_cancel_discover.clicked.connect(self.cancel_auto_discovery)
        auto_btn_bar.addWidget(self.btn_cancel_discover)

        page_auto_layout.addLayout(auto_btn_bar)

        self.list_discovered = QListWidget(self.page_auto)
        self.list_discovered.setObjectName("listDiscoveredCandidates")
        self.list_discovered.setMinimumHeight(100)
        self.list_discovered.setMaximumHeight(150)
        self.list_discovered.itemChanged.connect(self._on_discovered_item_changed)
        page_auto_layout.addWidget(self.list_discovered)

        discovered_actions_bar = QHBoxLayout()
        discovered_actions_bar.setSpacing(6)

        self.btn_select_all = QPushButton(t("donor.btn.select_all"))
        self.btn_select_all.setObjectName("btnSmallSecondary")
        self.btn_select_all.clicked.connect(lambda: self._set_all_discovered_checked(True))
        discovered_actions_bar.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton(t("donor.btn.deselect_all"))
        self.btn_deselect_all.setObjectName("btnSmallSecondary")
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_discovered_checked(False))
        discovered_actions_bar.addWidget(self.btn_deselect_all)

        self.btn_add_selected = QPushButton(t("donor.btn.add_selected", count=0))
        self.btn_add_selected.setObjectName("btnActionPrimary")
        self.btn_add_selected.clicked.connect(self._add_selected_discovered_to_pool)
        discovered_actions_bar.addWidget(self.btn_add_selected)

        page_auto_layout.addLayout(discovered_actions_bar)

        self.stack.addWidget(self.page_auto)

        main_layout.addWidget(self.stack)

        # ── 3. Indexing Status & Progress Bar ─────────────────────────────────
        status_bar_layout = QHBoxLayout()
        status_bar_layout.setSpacing(6)

        self.lbl_pool_status = QLabel(t("donor.pool.status_empty"))
        self.lbl_pool_status.setStyleSheet("font-size: 11px; color: #a1a1aa; font-weight: 500;")
        status_bar_layout.addWidget(self.lbl_pool_status, 1)

        main_layout.addLayout(status_bar_layout)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("donorProgressBar")
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        main_layout.addWidget(self.progress_bar)

        # ── 4. Candidate Compatibility Card ───────────────────────────────────
        self.compat_card = QFrame(self)
        self.compat_card.setObjectName("compatCard")
        compat_layout = QVBoxLayout(self.compat_card)
        compat_layout.setContentsMargins(10, 8, 10, 8)
        compat_layout.setSpacing(4)

        self.lbl_compat_indicator = QLabel(t("donor.compat.no_candidate"))
        self.lbl_compat_indicator.setWordWrap(True)
        self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        compat_layout.addWidget(self.lbl_compat_indicator)

        main_layout.addWidget(self.compat_card)

        # Connect mode buttons
        self.btn_mode_file.clicked.connect(lambda: self.set_mode("file", save=True))
        self.btn_mode_folder.clicked.connect(lambda: self.set_mode("folder", save=True))
        self.btn_mode_auto.clicked.connect(lambda: self.set_mode("auto", save=True))

        self.set_mode("folder", save=False)

    def _apply_styling(self) -> None:
        self.setStyleSheet("""
            QFrame#donorPoolWidget {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
            QPushButton#btnModeSegment {
                background-color: #202024;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#btnModeSegment:hover {
                background-color: #27272a;
                color: #f4f4f5;
            }
            QPushButton#btnModeSegment:checked {
                background-color: #3b82f6;
                color: #ffffff;
                border-color: #60a5fa;
            }
            QFrame#donorCard {
                background-color: #121214;
                border: 1px dashed #3f3f46;
                border-radius: 6px;
            }
            QFrame#donorCard:hover {
                border-color: #60a5fa;
                background-color: #18181b;
            }
            QFrame#compatCard {
                background-color: #121214;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
            QListWidget {
                background-color: #121214;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                font-size: 11px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #202024;
            }
            QListWidget::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QPushButton#btnActionPrimary {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#btnActionPrimary:hover { background-color: #1d4ed8; }
            QPushButton#btnActionSecondary {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#btnActionSecondary:hover { background-color: #3f3f46; }
            QPushButton#btnSmallSecondary {
                background-color: #202024;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 10px;
            }
            QPushButton#btnSmallSecondary:hover { color: #f4f4f5; background-color: #27272a; }
            QPushButton#btnActionDanger {
                background-color: #27272a;
                color: #ef4444;
                border: 1px solid #3f3f46;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#btnActionDanger:hover {
                background-color: #ef4444;
                color: #ffffff;
                border-color: #ef4444;
            }
            QProgressBar#donorProgressBar {
                background-color: #202024;
                border: 1px solid #27272a;
                border-radius: 3px;
                height: 6px;
                text-align: center;
            }
            QProgressBar#donorProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 3px;
            }
        """)

    # ── Mode Management ───────────────────────────────────────────────────────

    def set_mode(self, mode: str, save: bool = True) -> None:
        """Switch active donor mode ('file', 'folder', or 'auto')."""
        if mode not in {"file", "folder", "auto"}:
            return
        self.current_mode = mode

        # Block signals to prevent recursive toggling
        self.btn_mode_file.blockSignals(True)
        self.btn_mode_folder.blockSignals(True)
        self.btn_mode_auto.blockSignals(True)
        self._compat_chk_auto.blockSignals(True)

        self.btn_mode_file.setChecked(mode == "file")
        self.btn_mode_folder.setChecked(mode == "folder")
        self.btn_mode_auto.setChecked(mode == "auto")

        if mode == "file":
            self.stack.setCurrentIndex(0)
            self._compat_chk_auto.setChecked(False)
        elif mode == "folder":
            self.stack.setCurrentIndex(1)
            self._compat_chk_auto.setChecked(True)
        elif mode == "auto":
            self.stack.setCurrentIndex(2)
            self._compat_chk_auto.setChecked(True)

        self.btn_mode_file.blockSignals(False)
        self.btn_mode_folder.blockSignals(False)
        self.btn_mode_auto.blockSignals(False)
        self._compat_chk_auto.blockSignals(False)

        self._refresh_pool_status_label()
        self.update_compatibility()
        self.donor_changed.emit()
        if save and self._is_initialized:
            self.save_settings()

    def _on_compat_chk_toggled(self, checked: bool) -> None:
        """Synchronize with legacy view.chk_auto_donor toggles."""
        if checked:
            self.manual_donor_path = None
            if self.current_mode == "file":
                self.set_mode("folder")
            else:
                self.update_compatibility()
                self.donor_changed.emit()
        else:
            if self.current_mode != "file":
                self.set_mode("file")

    # ── Manual Single Donor (File Mode) ───────────────────────────────────────

    def set_manual_donor_file(self, file_path: Path | str | None) -> None:
        """Set a single manual donor JPEG file."""
        if file_path:
            p = Path(file_path).resolve()
            if p.is_file():
                self.manual_donor_path = p
            else:
                self.manual_donor_path = None
        else:
            self.manual_donor_path = None

        self._refresh_file_donor_display()
        self.update_compatibility()
        self.donor_changed.emit()
        self.save_settings()

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
            self.set_manual_donor_file(Path(file_path))

    def _refresh_file_donor_display(self) -> None:
        """Refresh label in single-file drop box."""
        if not self.manual_donor_path or not self.manual_donor_path.is_file():
            self.lbl_file_donor_status.setText(t("heal.donor.none"))
            self.lbl_file_donor_status.setStyleSheet("font-size: 11px; color: #a1a1aa;")
            return

        make, model, dim = extract_camera_info(self.manual_donor_path)
        dim_str = f"{dim[0]}x{dim[1]}" if dim[0] > 0 else "JPEG"
        cam_info = f" • {make} {model}".strip() if (make or model) else ""
        text = t("heal.donor.manual", name=self.manual_donor_path.name, geometry=f"{dim_str}{cam_info}")
        self.lbl_file_donor_status.setText(text)
        self.lbl_file_donor_status.setStyleSheet("font-size: 11px; color: #10b981; font-weight: 600;")

    # ── Folder Pool Operations ────────────────────────────────────────────────

    def _browse_add_donor_folder(self) -> None:
        """Open directory dialog to add one or more donor folders to pool."""
        init_dir = str(self.archive_root) if self.archive_root else ""
        folder = QFileDialog.getExistingDirectory(
            self,
            t("donor.dialog.add_folder"),
            init_dir,
        )
        if folder:
            self.add_donor_folder(Path(folder))

    def add_donor_folder(self, folder: Path | str) -> None:
        """Add and index a donor folder asynchronously."""
        folder_path = Path(folder).resolve()
        if not folder_path.is_dir():
            return

        if folder_path in self.folder_paths:
            return

        self.folder_paths.append(folder_path)
        self._refresh_folder_list_widget()
        self.index_folders([folder_path])

    def add_donor_folders(self, folders: Sequence[Path | str]) -> None:
        """Batch add and index donor folders."""
        to_index: list[Path] = []
        for f in folders:
            p = Path(f).resolve()
            if p.is_dir() and p not in self.folder_paths:
                self.folder_paths.append(p)
                to_index.append(p)

        if to_index:
            self._refresh_folder_list_widget()
            self.index_folders(to_index)

    def _remove_selected_folder(self) -> None:
        """Remove selected folder from pool and index."""
        current_row = self.list_folders.currentRow()
        if current_row < 0 or current_row >= len(self.folder_paths):
            return

        removed_folder = self.folder_paths.pop(current_row)
        self.donor_index.remove_folder(removed_folder)
        self._refresh_folder_list_widget()
        self._refresh_pool_status_label()
        self.update_compatibility()
        self.donor_changed.emit()
        self.save_settings()

    def _clear_entire_pool(self) -> None:
        """Prompt and clear all folders from pool."""
        if not self.folder_paths and len(self.donor_index) == 0:
            return

        ans = QMessageBox.question(
            self,
            t("donor.pool.title"),
            t("donor.dialog.confirm_clear"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.folder_paths.clear()
        self.donor_index = DonorIndex()
        self._refresh_folder_list_widget()
        self._refresh_pool_status_label()
        self.update_compatibility()
        self.donor_changed.emit()
        self.save_settings()

    def _refresh_folder_list_widget(self) -> None:
        """Update items in the folder list widget."""
        self.list_folders.clear()
        for f in self.folder_paths:
            # Count JPEGs indexed from this folder
            count = len(self.donor_index._by_folder.get(str(f), []))
            item_text = f"📁 {f.name} ({f.parent}) • {count} JPEGs"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self.list_folders.addItem(item)

    def _refresh_pool_status_label(self) -> None:
        """Update status line showing total indexed donors and folders."""
        donors_count = len(self.donor_index)
        folders_count = len(self.folder_paths)
        if donors_count == 0 and folders_count == 0:
            self.lbl_pool_status.setText(t("donor.pool.status_empty"))
            self.lbl_pool_status.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        else:
            self.lbl_pool_status.setText(
                t("donor.pool.status_indexed", donors=donors_count, folders=folders_count)
            )
            self.lbl_pool_status.setStyleSheet("font-size: 11px; color: #60a5fa; font-weight: 600;")
        self.pool_updated.emit(donors_count, folders_count)

    # ── Background Indexing ───────────────────────────────────────────────────

    def index_folders(self, folders: Sequence[Path | str]) -> None:
        """Launch background DonorIndexWorker to index specified folders."""
        if self._index_worker and self._index_worker.isRunning():
            self._index_worker.stop()
            self._index_worker.wait()

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._index_worker = DonorIndexWorker(
            folders=folders,
            existing_index=self.donor_index,
            parent=self,
        )
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.folder_indexed.connect(self._on_folder_indexed)
        self._index_worker.finished.connect(self._on_indexing_finished)
        self._index_worker.start()

    def _on_index_progress(self, current: int, total: int, folder_name: str) -> None:
        pct = int((current / max(total, 1)) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_pool_status.setText(t("donor.pool.indexing", folder=folder_name))

    def _on_folder_indexed(self, folder: Path, added: int) -> None:
        self._refresh_folder_list_widget()

    def _on_indexing_finished(self, new_index: DonorIndex) -> None:
        self.donor_index = new_index
        self.progress_bar.setVisible(False)
        self._refresh_folder_list_widget()
        self._refresh_pool_status_label()
        self.update_compatibility()
        self.donor_changed.emit()
        self.save_settings()

    # ── Background Auto-Discovery ─────────────────────────────────────────────

    def start_auto_discovery(self) -> None:
        """Start auto-discovery worker across system drives & camera locations."""
        if self._discover_worker and self._discover_worker.isRunning():
            return

        self.list_discovered.clear()
        self.btn_auto_discover.setVisible(False)
        self.btn_cancel_discover.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_pool_status.setText(t("donor.discover.searching"))

        # Extract target prefixes from candidate if available
        prefixes: set[str] | None = None
        if self.current_candidate:
            pfx = extract_filename_prefix(self.current_candidate.name)
            if pfx:
                prefixes = {pfx}

        self._discover_worker = DonorDiscoverWorker(
            max_depth=3,
            target_prefixes=prefixes,
            archive_root=self.archive_root,
            parent=self,
        )
        self._discover_worker.folder_found.connect(self._on_discovered_folder_found)
        self._discover_worker.progress.connect(self._on_discover_progress)
        self._discover_worker.finished.connect(self._on_discover_finished)
        self._discover_worker.start()

    def cancel_auto_discovery(self) -> None:
        """Stop active discovery scan."""
        if self._discover_worker and self._discover_worker.isRunning():
            self._discover_worker.stop()
            self._discover_worker.wait()
            self._on_discover_finished([])

    def _on_discover_progress(self, current: int, total: int, path_name: str) -> None:
        pct = int((current / max(total, 1)) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_pool_status.setText(f"{t('donor.discover.searching')} ({path_name})")

    def _on_discovered_folder_found(self, cand: DonorFolderCandidate) -> None:
        """Add candidate folder item with checkbox to discovered list."""
        health_pct = int(cand.healthy_ratio * 100)
        pfx_str = cand.prefix_summary or "-"
        metrics_text = t(
            "donor.discover.metrics",
            count=cand.jpeg_count,
            health=health_pct,
            prefixes=pfx_str,
        )
        score_badge = t("donor.discover.score_badge", score=f"{cand.priority_score:.2f}")
        item_text = f"📁 {cand.name} ({metrics_text}) [{score_badge}]"

        item = QListWidgetItem(item_text)
        item.setData(Qt.ItemDataRole.UserRole, cand)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # Check by default if high score or DCIM
        is_already_in_pool = cand.path in self.folder_paths
        if not is_already_in_pool and cand.priority_score >= 0.35:
            item.setCheckState(Qt.CheckState.Checked)
        else:
            item.setCheckState(Qt.CheckState.Unchecked)

        self.list_discovered.addItem(item)
        self._update_add_selected_button_text()

    def _on_discovered_item_changed(self, item: QListWidgetItem) -> None:
        self._update_add_selected_button_text()

    def _set_all_discovered_checked(self, checked: bool) -> None:
        st = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list_discovered.count()):
            self.list_discovered.item(i).setCheckState(st)
        self._update_add_selected_button_text()

    def _update_add_selected_button_text(self) -> None:
        count = sum(
            1 for i in range(self.list_discovered.count())
            if self.list_discovered.item(i).checkState() == Qt.CheckState.Checked
        )
        self.btn_add_selected.setText(t("donor.btn.add_selected", count=count))
        self.btn_add_selected.setEnabled(count > 0)

    def _on_discover_finished(self, candidates: list[DonorFolderCandidate]) -> None:
        self.btn_auto_discover.setVisible(True)
        self.btn_cancel_discover.setVisible(False)
        self.progress_bar.setVisible(False)
        total = self.list_discovered.count()
        if total == 0:
            self.lbl_pool_status.setText(t("donor.discover.empty"))
        else:
            self.lbl_pool_status.setText(t("donor.discover.found_count", count=total))
        self._update_add_selected_button_text()

    def _add_selected_discovered_to_pool(self) -> None:
        """Add all checked discovered candidate folders into donor pool."""
        folders_to_add: list[Path] = []
        for i in range(self.list_discovered.count()):
            item = self.list_discovered.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                cand: DonorFolderCandidate = item.data(Qt.ItemDataRole.UserRole)
                if cand and cand.path not in self.folder_paths:
                    folders_to_add.append(cand.path)

        if folders_to_add:
            self.add_donor_folders(folders_to_add)

    # ── Compatibility Evaluation ──────────────────────────────────────────────

    def set_current_candidate(self, candidate_path: Path | str | None) -> None:
        """Update current candidate photo and recalculate best donor."""
        self.current_candidate = Path(candidate_path).resolve() if candidate_path else None
        self.update_compatibility()

    def set_archive_root(self, archive_root: Path | str | None) -> None:
        """Update archive root folder for proximity heuristics."""
        self.archive_root = Path(archive_root).resolve() if archive_root else None

    def update_compatibility(self) -> None:
        """Calculate and display compatibility match for current candidate."""
        if self.current_mode == "file":
            if self.manual_donor_path and self.manual_donor_path.is_file():
                make, model, dim = extract_camera_info(self.manual_donor_path)
                dim_str = f"{dim[0]}x{dim[1]}" if dim[0] > 0 else "JPEG"
                cam_info = f" • {make} {model}".strip() if (make or model) else ""
                self.lbl_compat_indicator.setText(
                    t("donor.compat.manual_active", name=self.manual_donor_path.name, geometry=f"{dim_str}{cam_info}")
                )
                self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #10b981; font-weight: 600;")
            else:
                self.lbl_compat_indicator.setText(t("heal.donor.none"))
                self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #a1a1aa;")
            return

        # Folder or Auto mode
        if not self.current_candidate or not self.current_candidate.is_file():
            self.lbl_compat_indicator.setText(t("donor.compat.no_candidate"))
            self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #a1a1aa;")
            return

        if len(self.donor_index) == 0:
            self.lbl_compat_indicator.setText(t("donor.compat.none"))
            self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #f59e0b;")
            return

        matches = self.donor_index.find_best_donor(self.current_candidate)
        if matches:
            best = matches[0]
            score_str = f"{best.score:.2f}"
            self.lbl_compat_indicator.setText(
                t("donor.compat.best", name=best.entry.resolved_path.name, score=score_str, reason=best.match_reason)
            )
            self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #10b981; font-weight: 600;")
        else:
            self.lbl_compat_indicator.setText(t("donor.compat.none"))
            self.lbl_compat_indicator.setStyleSheet("font-size: 11px; color: #ef4444;")

    def get_effective_donor(self, candidate: Path | None = None) -> Path | None:
        """Resolve effective donor JPEG for restoration and preview."""
        if self.current_mode == "file":
            if self.manual_donor_path and self.manual_donor_path.is_file():
                return self.manual_donor_path
            return None

        # Mode folder or auto
        cand = candidate or self.current_candidate
        if cand and len(self.donor_index) > 0:
            matches = self.donor_index.find_best_donor(cand)
            if matches:
                return matches[0].path

        # Fallback to manual if set
        if self.manual_donor_path and self.manual_donor_path.is_file():
            return self.manual_donor_path

        return None

    # ── Persistence (Settings & Cache) ────────────────────────────────────────

    def save_settings(self) -> None:
        """Save donor pool configuration and cached index to QSettings & settings.json."""
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

        settings = QSettings("PhotoHealer", "PhotoHealer")
        settings.setValue("donor_pool/mode", self.current_mode)
        settings.setValue("donor_pool/folders", [str(p) for p in self.folder_paths])
        if self.manual_donor_path:
            settings.setValue("donor_pool/manual_file", str(self.manual_donor_path))
        else:
            settings.remove("donor_pool/manual_file")
        settings.sync()

        # JSON fallback file
        data = {
            "mode": self.current_mode,
            "folders": [str(p) for p in self.folder_paths],
            "manual_file": str(self.manual_donor_path) if self.manual_donor_path else None,
            "cache_file": str(DEFAULT_CACHE_POOL),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

        # Save indexed pool cache
        if len(self.donor_index) > 0:
            try:
                self.donor_index.save_to_json(DEFAULT_CACHE_POOL)
            except OSError:
                pass

    def load_settings(self) -> None:
        """Restore donor pool folders, active mode, and cached index on startup."""
        loaded_folders: list[Path] = []
        loaded_mode = "folder"
        loaded_manual: Path | None = None

        settings = QSettings("PhotoHealer", "PhotoHealer")
        settings.sync()
        raw_folders = settings.value("donor_pool/folders")
        if isinstance(raw_folders, str):
            raw_folders = [raw_folders]
        if isinstance(raw_folders, (list, tuple)):
            loaded_folders = [Path(p) for p in raw_folders if Path(p).is_dir()]
        raw_mode = settings.value("donor_pool/mode")
        if isinstance(raw_mode, str) and raw_mode in {"file", "folder", "auto"}:
            loaded_mode = raw_mode
        raw_manual = settings.value("donor_pool/manual_file")
        if raw_manual and Path(str(raw_manual)).is_file():
            loaded_manual = Path(str(raw_manual))

        # Fallback to settings.json if QSettings was empty
        if not loaded_folders and SETTINGS_FILE.is_file():
            try:
                file_data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                loaded_folders = [Path(p) for p in file_data.get("folders", []) if Path(p).is_dir()]
                if not raw_mode and file_data.get("mode") in {"file", "folder", "auto"}:
                    loaded_mode = file_data["mode"]
                if not loaded_manual and file_data.get("manual_file"):
                    mf = Path(file_data["manual_file"])
                    if mf.is_file():
                        loaded_manual = mf
            except Exception:
                pass

        self.folder_paths = loaded_folders
        self.manual_donor_path = loaded_manual

        # Try to load cached donor index
        if DEFAULT_CACHE_POOL.is_file():
            try:
                cached_index = DonorIndex.load_from_json(DEFAULT_CACHE_POOL)
                if cached_index.is_cache_valid() and len(cached_index) > 0:
                    self.donor_index = cached_index
            except Exception:
                pass

        self._refresh_folder_list_widget()
        self._refresh_file_donor_display()
        self._refresh_pool_status_label()
        self.set_mode(loaded_mode, save=False)

    # ── Localization Retranslation ────────────────────────────────────────────

    def retranslate_ui(self) -> None:
        """Update strings when language changes at runtime."""
        self.lbl_title.setText(t("donor.pool.title"))
        self.btn_mode_file.setText(t("donor.mode.file"))
        self.btn_mode_file.setToolTip(t("donor.mode.file_tip"))
        self.btn_mode_folder.setText(t("donor.mode.folder"))
        self.btn_mode_folder.setToolTip(t("donor.mode.folder_tip"))
        self.btn_mode_auto.setText(t("donor.mode.auto"))
        self.btn_mode_auto.setToolTip(t("donor.mode.auto_tip"))

        self.btn_browse_donor.setText(t("heal.donor.browse"))
        self.lbl_donor_drop_hint.setText(t("heal.donor.drop_prompt"))

        self.btn_add_donor_folder.setText(t("donor.btn.add_folder"))
        self.btn_remove_donor_folder.setText(t("donor.btn.remove_folder"))
        self.btn_clear_pool.setText(t("donor.btn.clear_pool"))

        self.btn_auto_discover.setText(f"🔍 {t('donor.btn.auto_discover')}")
        self.btn_cancel_discover.setText(t("donor.btn.cancel_discover"))
        self.btn_select_all.setText(t("donor.btn.select_all"))
        self.btn_deselect_all.setText(t("donor.btn.deselect_all"))

        self._refresh_file_donor_display()
        self._refresh_pool_status_label()
        self._update_add_selected_button_text()
        self.update_compatibility()

    def closeEvent(self, event) -> None:
        """Cleanly shutdown any background threads and persist settings."""
        if self._index_worker and self._index_worker.isRunning():
            self._index_worker.stop()
            self._index_worker.wait()
        if self._discover_worker and self._discover_worker.isRunning():
            self._discover_worker.stop()
            self._discover_worker.wait()
        self.save_settings()
        super().closeEvent(event)
