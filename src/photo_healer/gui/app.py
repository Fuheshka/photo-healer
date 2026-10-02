# -*- coding: utf-8 -*-
"""Desktop application entry point for Photo Healer.

Launches the PySide6 application with high-DPI scaling, dark theme palette,
runtime language detection, and single-window triage interface.
"""

from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

from photo_healer import __version__
from photo_healer.gui.views.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Launch Photo Healer desktop GUI application."""
    if argv is None:
        argv = sys.argv

    # High DPI scaling is default in Qt 6, but ensuring crisp rendering
    app = QApplication(list(argv))
    app.setApplicationName("Photo Healer")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PhotoHealer")

    # Set base dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#121214"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f4f4f5"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#18181b"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#202024"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#27272a"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f4f4f5"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f4f4f5"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#27272a"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f4f4f5"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3b82f6"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
