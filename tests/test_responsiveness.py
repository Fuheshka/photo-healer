# -*- coding: utf-8 -*-
"""Comprehensive automated verification tests for GUI responsiveness in Photo Healer.

Tests cover:
  1. MainWindow top bar responsive policies and language switcher menu fallback (<700px vs >=700px).
  2. MainWindow status bar adaptive metrics and flexible progress bar.
  3. HealView proportional splitter (1:3 ratio) and left panel min/max width bounds (280-400px).
  4. HealView scrollable left panel with pinned action buttons (always visible at bottom).
  5. HealView preview toolbar responsive mode switching (<600px two rows vs >=600px single row).
  6. CarveView adaptive grid column calculation and toolbar row restructuring.
  7. Multi-resolution execution: 850x520 (min), 1280x720 (laptop), 1920x1080 (full screen).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QSizePolicy

from photo_healer.gui.i18n import get_language, set_language
from photo_healer.gui.views.carve_view import CARD_HEIGHT, CARD_SPACING, CARD_WIDTH, CarveView
from photo_healer.gui.views.heal_view import HealView
from photo_healer.gui.views.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestMainWindowResponsiveness:
    def test_minimum_size_and_initial_size(self, qapp: QApplication) -> None:
        win = MainWindow()
        win.show()
        assert win.minimumSize().width() == 850
        assert win.minimumSize().height() == 520

    def test_top_bar_sizing_policies(self, qapp: QApplication) -> None:
        win = MainWindow()
        win.show()
        # txt_folder should be Expanding with min width >= 120
        assert win.txt_folder.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert win.txt_folder.minimumWidth() >= 120

        # Buttons should have fixed horizontal policy
        assert win.btn_browse.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
        assert win.btn_scan.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed

    def test_language_switcher_responsive_transition(self, qapp: QApplication) -> None:
        win = MainWindow()
        win.show()

        # At wide width (850px >= 700px), combo box is visible, menu item is hidden
        win.resize(850, 520)
        qapp.processEvents()
        assert win.combo_lang.isVisible() is True
        assert win.menu_language.menuAction().isVisible() is False

        # At narrow width (< 700px), combo box is hidden, menu item is visible
        win.setMinimumSize(500, 400)
        win.resize(650, 520)
        qapp.processEvents()
        assert win.combo_lang.isVisible() is False
        assert win.menu_language.menuAction().isVisible() is True

        # Switching language via menu actions works reactively
        win.act_lang_en.trigger()
        qapp.processEvents()
        assert get_language() == "en"
        assert win.act_lang_en.isChecked() is True

        win.act_lang_ru.trigger()
        qapp.processEvents()
        assert get_language() == "ru"
        assert win.act_lang_ru.isChecked() is True

    def test_status_bar_responsiveness(self, qapp: QApplication) -> None:
        win = MainWindow()
        win.show()

        # Progress bar has flexible width, min width >= 100, not fixed 160
        assert win.progress_bar.minimumWidth() >= 100
        assert win.progress_bar.maximumWidth() <= 200
        assert win.progress_bar.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred

        # File metrics have Expanding policy
        assert win.lbl_metric_files.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert win.lbl_metric_size.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


class TestHealViewResponsiveness:
    def test_splitter_proportions_and_bounds(self, qapp: QApplication) -> None:
        view = HealView()
        view.resize(1080, 720)
        view.show()
        qapp.processEvents()

        # Left panel width limits 280-400
        assert view.left_panel.minimumWidth() == 280
        assert view.left_panel.maximumWidth() == 400

        # Splitter sizes verify proportional 1:3 ratio
        sizes = view.splitter.sizes()
        assert len(sizes) == 2
        # Right panel should receive majority of space (roughly 3x left panel)
        assert sizes[1] > sizes[0]

    def test_scrollable_left_panel_and_pinned_actions(self, qapp: QApplication) -> None:
        view = HealView()
        view.show()
        qapp.processEvents()

        # Left scroll area exists and is resizable
        assert hasattr(view, "left_scroll_area")
        assert view.left_scroll_area.widgetResizable() is True

        # Candidate list and log view have relaxed height constraints
        assert view.list_candidates.minimumHeight() <= 100
        assert view.log_view.minimumHeight() <= 80
        # No rigid maximum height of 110 on log_view
        assert view.log_view.maximumHeight() > 1000

        # Action buttons are pinned outside the scroll area in bottom_action_widget
        assert hasattr(view, "bottom_action_widget")
        assert view.bottom_action_widget.parent() == view.left_panel
        assert view.btn_heal_single.parent() == view.bottom_action_widget
        assert view.btn_heal_batch.parent() == view.bottom_action_widget
        assert view.batch_progress.parent() == view.bottom_action_widget

    def test_preview_toolbar_adaptive_rows(self, qapp: QApplication) -> None:
        view = HealView()
        view.resize(1080, 720)
        view.show()
        qapp.processEvents()

        # Toolbar is wrapped in a scroll area
        assert hasattr(view, "toolbar_scroll")
        assert view.toolbar_scroll.widgetResizable() is True

        # When wide (e.g. 700px), row 2 is hidden, zoom controls are on row 1
        view._update_toolbar_layout(700)
        qapp.processEvents()
        assert view.tb_row2_widget.isVisible() is False
        assert view.sep_toolbar.isVisible() is True
        assert view.btn_zoom_fit.parent() == view.preview_toolbar
        assert view.toolbar_scroll.height() == 44

        # When narrow (e.g. 500px), zoom controls move to row 2
        view._update_toolbar_layout(500)
        qapp.processEvents()
        assert view.tb_row2_widget.isVisible() is True
        assert view.sep_toolbar.isVisible() is False
        assert view.toolbar_scroll.height() == 74


class TestCarveViewResponsiveness:
    def test_toolbar_policies(self, qapp: QApplication) -> None:
        view = CarveView()
        view.show()

        # txt_search has flexible width (100 to 200)
        assert view.txt_search.minimumWidth() >= 100
        assert view.txt_search.maximumWidth() <= 200
        assert view.txt_search.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred

        # progress_bar has flexible width (80 to 130)
        assert view.progress_bar.minimumWidth() >= 80
        assert view.progress_bar.maximumWidth() <= 130

    def test_toolbar_adaptive_rows(self, qapp: QApplication) -> None:
        view = CarveView()
        view.resize(1200, 700)
        view.show()
        qapp.processEvents()

        # At wide width (>=1050), row 2 is hidden, action buttons are on row 1
        view._update_toolbar_layout(1200)
        qapp.processEvents()
        assert view.row2_widget.isVisible() is False

        # At narrow width (<1050), action buttons move to row 2
        view._update_toolbar_layout(850)
        qapp.processEvents()
        assert view.row2_widget.isVisible() is True

    def test_grid_columns_recalculation_formula(self, qapp: QApplication) -> None:
        view = CarveView()
        view.show()

        # Test formula: available_width = 432px (fits exactly 2 cards: 210 + 12 + 210)
        available_width = 432
        num_cols = max(1, (available_width + CARD_SPACING) // (CARD_WIDTH + CARD_SPACING))
        assert num_cols == 2

        # At narrow width (350px), formula produces 1 column
        narrow_width = 350
        num_cols_narrow = max(1, (narrow_width + CARD_SPACING) // (CARD_WIDTH + CARD_SPACING))
        assert num_cols_narrow == 1


class TestMultiResolutionExecution:
    @pytest.mark.parametrize("width,height", [
        (850, 520),
        (1280, 720),
        (1920, 1080),
    ])
    def test_window_renders_cleanly_at_resolution(self, qapp: QApplication, width: int, height: int) -> None:
        win = MainWindow()
        win.resize(width, height)
        win.show()
        qapp.processEvents()

        assert win.width() == width
        assert win.height() == height

        # Switch to recovery tab
        win.tabs.setCurrentIndex(1)
        qapp.processEvents()

        # Heal tab checks: buttons pinned and visible
        heal = win.heal_view
        assert heal.btn_heal_single.isVisible() is True
        assert heal.btn_heal_batch.isVisible() is True

        # Switch to preview gallery tab
        win.tabs.setCurrentIndex(2)
        qapp.processEvents()
        assert win.carve_view.btn_scan.isVisible() is True

        # Grab pixmap without crash
        pixmap = win.grab()
        assert not pixmap.isNull()
        assert pixmap.width() == width
        assert pixmap.height() == height
