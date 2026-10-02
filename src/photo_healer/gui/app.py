# -*- coding: utf-8 -*-
"""Desktop application entry point for Photo Healer.

Launches the PySide6 application with high-DPI scaling, dark theme palette,
runtime language detection, and single-window triage interface.
"""

from __future__ import annotations

import sys
from typing import Sequence

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtWidgets import QApplication
except ImportError:
    sys.stderr.write(
        "Error: GUI requires PySide6 and Pillow libraries.\n"
        "Install GUI dependencies with:\n"
        "    pip install photo-healer[gui]\n"
        "or:\n"
        "    pip install PySide6 pillow\n"
    )
    sys.exit(1)

from photo_healer import __version__
from photo_healer.gui.i18n import set_language
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

    # Parse command-line arguments if provided
    initial_folder: str | None = None
    argv_list = list(argv)
    args_slice = argv_list[1:] if len(argv_list) > 1 else []
    for idx, arg in enumerate(args_slice):
        if arg == "--folder" and idx + 1 < len(args_slice):
            initial_folder = args_slice[idx + 1]
        elif arg.startswith("--folder="):
            initial_folder = arg.split("=", 1)[1]
        elif arg == "--lang" and idx + 1 < len(args_slice):
            set_language(args_slice[idx + 1])
        elif arg.startswith("--lang="):
            set_language(arg.split("=", 1)[1])

    window = MainWindow(initial_folder=initial_folder)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
