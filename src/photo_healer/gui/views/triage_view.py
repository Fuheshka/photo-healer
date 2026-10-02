# -*- coding: utf-8 -*-
"""Diagnostics and triage view for Photo Healer.

Provides virtualized QTableView file inspection, category filtering buttons,
TRIM-dummy quarantine dialog with safe dry-run simulation, and JSON report export.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from photo_healer.gui.i18n import i18n, t
from photo_healer.gui.models.file_table_model import (
    FileFilterProxyModel,
    FileTableModel,
    StatusBadgeDelegate,
    format_size,
)


class QuarantineDialog(QDialog):
    """Confirmation and options dialog for relocating TRIM-zero dummy files."""

    def __init__(
        self,
        dummy_count: int,
        dummy_size: int,
        default_dest: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dummy_count = dummy_count
        self.dummy_size = dummy_size
        self.default_dest = default_dest

        self.setWindowTitle(t("quarantine.title"))
        self.setMinimumWidth(540)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # Prompt label
        prompt_text = t(
            "quarantine.confirm_prompt",
            count=self.dummy_count,
            size=format_size(self.dummy_size),
        )
        self.prompt_label = QLabel(prompt_text)
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #f4f4f5;")
        layout.addWidget(self.prompt_label)

        # Destination folder selection
        dest_label = QLabel(t("quarantine.dest_label"))
        dest_label.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        layout.addWidget(dest_label)

        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit(str(self.default_dest))
        self.dest_edit.setStyleSheet(
            "background-color: #27272a; color: #f4f4f5; border: 1px solid #3f3f46; "
            "border-radius: 6px; padding: 6px 10px; font-size: 12px;"
        )
        self.btn_browse = QPushButton(t("quarantine.browse"))
        self.btn_browse.setStyleSheet(
            "background-color: #3f3f46; color: #f4f4f5; border: none; "
            "border-radius: 6px; padding: 6px 14px; font-weight: 500;"
        )
        self.btn_browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.dest_edit)
        dest_row.addWidget(self.btn_browse)
        layout.addLayout(dest_row)

        # Dry-run checkbox
        self.chk_dry_run = QCheckBox(t("quarantine.dry_run"))
        self.chk_dry_run.setChecked(True)  # Default safe mode
        self.chk_dry_run.setStyleSheet("color: #60a5fa; font-size: 12px; font-weight: 500;")
        layout.addWidget(self.chk_dry_run)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton(t("quarantine.btn_cancel"))
        self.btn_cancel.setStyleSheet(
            "background-color: #3f3f46; color: #f4f4f5; border: none; "
            "border-radius: 6px; padding: 7px 18px; font-size: 12px;"
        )
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_ok = QPushButton(t("quarantine.btn_ok"))
        self.btn_ok.setStyleSheet(
            "background-color: #ef4444; color: #ffffff; border: none; "
            "border-radius: 6px; padding: 7px 20px; font-size: 12px; font-weight: bold;"
        )
        self.btn_ok.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_ok)
        layout.addLayout(btn_layout)

    def _browse_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            t("quarantine.dest_dialog_title"),
            self.dest_edit.text(),
        )
        if folder:
            self.dest_edit.setText(folder)

    def is_dry_run(self) -> bool:
        return self.chk_dry_run.isChecked()

    def get_destination(self) -> Path:
        return Path(self.dest_edit.text().strip())


class TriageView(QWidget):
    """Triage audit table view with category filter pills and quick actions."""

    quarantine_performed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_archive_path: Path | None = None

        self._counts = {
            "all": 0,
            "candidates": 0,
            "dummies": 0,
            "intact": 0,
            "errors": 0,
        }
        self._dummy_size: int = 0

        self._init_models()
        self._init_ui()
        i18n.language_changed.connect(self._retranslate_ui)

    def _init_models(self) -> None:
        self.table_model = FileTableModel(self)
        self.proxy_model = FileFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── Toolbar: Filter Pills & Action Buttons ────────────────────────────
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(8)

        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)

        self.btn_filter_all = QPushButton()
        self.btn_filter_all.setCheckable(True)
        self.btn_filter_all.setChecked(True)
        self.filter_group.addButton(self.btn_filter_all, 0)

        self.btn_filter_cand = QPushButton()
        self.btn_filter_cand.setCheckable(True)
        self.filter_group.addButton(self.btn_filter_cand, 1)

        self.btn_filter_dummies = QPushButton()
        self.btn_filter_dummies.setCheckable(True)
        self.filter_group.addButton(self.btn_filter_dummies, 2)

        self.btn_filter_intact = QPushButton()
        self.btn_filter_intact.setCheckable(True)
        self.filter_group.addButton(self.btn_filter_intact, 3)

        self.filter_buttons = [
            (self.btn_filter_all, "all"),
            (self.btn_filter_cand, "candidates"),
            (self.btn_filter_dummies, "dummies"),
            (self.btn_filter_intact, "intact"),
        ]

        for btn, cat in self.filter_buttons:
            btn.setStyleSheet(self._filter_btn_style())
            btn.clicked.connect(lambda _, c=cat: self._on_filter_clicked(c))
            toolbar_layout.addWidget(btn)

        toolbar_layout.addStretch()

        # Action Buttons
        self.btn_quarantine = QPushButton()
        self.btn_quarantine.setStyleSheet(
            "QPushButton {"
            "  background-color: #ef4444; color: #ffffff; border: none; "
            "  border-radius: 6px; padding: 7px 14px; font-weight: bold; font-size: 12px;"
            "}"
            "QPushButton:hover { background-color: #dc2626; }"
            "QPushButton:pressed { background-color: #b91c1c; }"
            "QPushButton:disabled { background-color: #3f3f46; color: #71717a; }"
        )
        self.btn_quarantine.clicked.connect(self._handle_quarantine)
        toolbar_layout.addWidget(self.btn_quarantine)

        self.btn_export = QPushButton()
        self.btn_export.setStyleSheet(
            "QPushButton {"
            "  background-color: #27272a; color: #f4f4f5; border: 1px solid #3f3f46; "
            "  border-radius: 6px; padding: 7px 14px; font-weight: 500; font-size: 12px;"
            "}"
            "QPushButton:hover { background-color: #3f3f46; border-color: #52525b; }"
            "QPushButton:pressed { background-color: #18181b; }"
            "QPushButton:disabled { background-color: #27272a; color: #71717a; }"
        )
        self.btn_export.clicked.connect(self._handle_export_json)
        toolbar_layout.addWidget(self.btn_export)

        main_layout.addLayout(toolbar_layout)

        # ── Virtualized TableView ─────────────────────────────────────────────
        self.table_view = QTableView(self)
        self.table_view.setModel(self.proxy_model)
        self.table_view.setItemDelegateForColumn(1, StatusBadgeDelegate(self.table_view))

        # Performance optimizations for 50,000+ items
        self.table_view.verticalHeader().setDefaultSectionSize(28)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.setSortingEnabled(True)
        self.table_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 240)
        header.resizeSection(1, 160)
        header.resizeSection(2, 110)

        self.table_view.setStyleSheet(
            "QTableView {"
            "  background-color: #18181b; color: #f4f4f5; gridline-color: #27272a;"
            "  border: 1px solid #27272a; border-radius: 6px; selection-background-color: #27272a;"
            "  selection-color: #ffffff; outline: none; font-size: 12px;"
            "}"
            "QTableView::item { padding: 4px; border-bottom: 1px solid #202024; }"
            "QTableView::item:selected { background-color: #27272a; }"
            "QHeaderView::section {"
            "  background-color: #202024; color: #a1a1aa; font-weight: 600; font-size: 11px;"
            "  padding: 6px 8px; border: none; border-bottom: 1px solid #3f3f46;"
            "}"
        )

        main_layout.addWidget(self.table_view)
        self._retranslate_ui()

    def _filter_btn_style(self) -> str:
        return (
            "QPushButton {"
            "  background-color: #202024; color: #a1a1aa; border: 1px solid #27272a;"
            "  border-radius: 6px; padding: 6px 12px; font-size: 12px; font-weight: 500;"
            "}"
            "QPushButton:hover { background-color: #27272a; color: #f4f4f5; }"
            "QPushButton:checked {"
            "  background-color: #3b82f6; color: #ffffff; border: 1px solid #60a5fa; font-weight: bold;"
            "}"
        )

    def _retranslate_ui(self) -> None:
        """Update localized button text and filter counts."""
        self.btn_filter_all.setText(t("filter.all", count=self._counts["all"]))
        self.btn_filter_cand.setText(t("filter.candidates", count=self._counts["candidates"]))
        self.btn_filter_dummies.setText(t("filter.dummies", count=self._counts["dummies"]))
        self.btn_filter_intact.setText(t("filter.intact", count=self._counts["intact"]))

        self.btn_quarantine.setText(t("action.quarantine"))
        self.btn_export.setText(t("action.export_json"))

    def _on_filter_clicked(self, category: str) -> None:
        self.proxy_model.set_category_filter(category)

    def set_archive_path(self, path: Path | str | None) -> None:
        self.current_archive_path = Path(path) if path else None

    def add_file_record(self, record: dict[str, Any]) -> None:
        """Add single record and update live counters."""
        self.table_model.add_item(record)
        status = record.get("status", "error")
        size = record.get("size", 0)

        self._counts["all"] += 1
        if status == "healed_candidate":
            self._counts["candidates"] += 1
        elif status == "trim_zero":
            self._counts["dummies"] += 1
            self._dummy_size += size
        elif status == "valid":
            self._counts["intact"] += 1
        else:
            self._counts["errors"] += 1

        self._retranslate_ui()

    def reset_data(self) -> None:
        """Clear model data and reset counters."""
        self.table_model.clear()
        self._counts = {
            "all": 0,
            "candidates": 0,
            "dummies": 0,
            "intact": 0,
            "errors": 0,
        }
        self._dummy_size = 0
        self._retranslate_ui()

    def _handle_quarantine(self) -> None:
        """Handle quarantine dialog and execution."""
        all_items = self.table_model.get_all_items()
        dummy_items = [it for it in all_items if it.get("status") == "trim_zero"]

        if not dummy_items:
            QMessageBox.information(
                self,
                t("quarantine.title"),
                t("quarantine.no_items"),
            )
            return

        total_dummy_size = sum(it.get("size", 0) for it in dummy_items)
        default_dest = (
            self.current_archive_path / "_Quarantine"
            if self.current_archive_path
            else Path.cwd() / "_Quarantine"
        )

        dlg = QuarantineDialog(
            dummy_count=len(dummy_items),
            dummy_size=total_dummy_size,
            default_dest=default_dest,
            parent=self,
        )

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        dest_dir = dlg.get_destination()
        is_dry_run = dlg.is_dry_run()

        moved = 0
        skipped = 0
        freed = 0

        if not is_dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)

        for item in dummy_items:
            src = Path(item["path"])
            size = item.get("size", 0)

            if not src.exists():
                skipped += 1
                continue

            # Determine destination path preserving relative structure if possible
            if self.current_archive_path:
                try:
                    rel = src.relative_to(self.current_archive_path)
                except ValueError:
                    rel = Path(src.name)
            else:
                rel = Path(src.name)

            dst = dest_dir / rel

            if is_dry_run:
                moved += 1
                freed += size
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                    moved += 1
                    freed += size
                    item["path"] = str(dst.resolve())
                    item["status"] = "quarantined"
                except OSError:
                    skipped += 1

        if is_dry_run:
            msg = t(
                "quarantine.dry_run_success",
                moved=moved,
                freed=format_size(freed),
            )
        else:
            msg = t(
                "quarantine.success",
                moved=moved,
                skipped=skipped,
                freed=format_size(freed),
            )
            # Refresh view
            self.table_model._on_language_changed(i18n.get_language())

        QMessageBox.information(self, t("quarantine.title"), msg)
        self.quarantine_performed.emit(
            {"moved": moved, "skipped": skipped, "freed": freed, "dry_run": is_dry_run}
        )

    def _handle_export_json(self) -> None:
        """Export current scan results into JSON report file."""
        all_items = self.table_model.get_all_items()
        if not all_items:
            QMessageBox.warning(
                self,
                t("export.title"),
                t("export.no_data"),
            )
            return

        default_file = (
            str(self.current_archive_path / "triage_report.json")
            if self.current_archive_path
            else "triage_report.json"
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            t("export.title"),
            default_file,
            t("export.filter"),
        )
        if not save_path:
            return

        report_payload = {
            "title": "Photo Healer Triage Report",
            "generated_at": datetime.datetime.now().isoformat(),
            "archive_folder": str(self.current_archive_path) if self.current_archive_path else None,
            "summary": {
                "total_files": len(all_items),
                "counts": self._counts,
                "dummy_size_bytes": self._dummy_size,
            },
            "files": all_items,
        }

        try:
            with open(save_path, "w", encoding="utf-8") as fh:
                json.dump(report_payload, fh, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self,
                t("export.title"),
                t("export.success", path=save_path),
            )
        except OSError as e:
            QMessageBox.critical(
                self,
                t("export.title"),
                t("export.error", error=e),
            )
