# -*- coding: utf-8 -*-
"""Generate high-resolution verification screenshots for Photo Healer responsiveness.

Saves screenshots of the GUI at:
  - 850x520 (Minimum supported resolution)
  - 1280x720 (Standard laptop resolution)
  - 1920x1080 (Full HD desktop resolution)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Use native font rendering
if "QT_QPA_PLATFORM" in os.environ:
    del os.environ["QT_QPA_PLATFORM"]

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from photo_healer.gui.i18n import set_language
from photo_healer.gui.views.main_window import MainWindow


def create_sample_thumbnail(w: int, h: int, color: QColor) -> bytes:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(color)
    p = QPainter(img)
    p.setPen(Qt.GlobalColor.white)
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"{w}x{h}")
    p.end()

    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(buf.data())


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtGui import QFont
    app.setFont(QFont("Segoe UI", 9))
    set_language("ru")

    out_dir = Path("docs/screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)

    resolutions = [
        (850, 520, "850x520_min"),
        (1280, 720, "1280x720_laptop"),
        (1920, 1080, "1920x1080_fhd"),
    ]

    for width, height, label in resolutions:
        win = MainWindow()
        win.resize(width, height)
        win.show()
        app.processEvents()

        # Populate realistic UI states
        win.txt_folder.setText("D:\\Photos\\Damaged_Archive_2024")
        win.lbl_metric_files.setText("Файлов: 42")
        win.lbl_metric_size.setText("Объем: 1.84 ГБ")
        win.lbl_status.setText("Сканирование: 18 / 42 файлов (IMG_0018.JPG)")
        win.progress_bar.setVisible(True)
        win.progress_bar.setValue(45)

        # 1. Recovery Tab (HealView)
        heal = win.heal_view
        for i in range(1, 12):
            heal.add_candidate(Path(f"D:\\Photos\\Damaged_Archive_2024\\CRW_{1000+i}.JPG"))
        heal.log_view.appendPlainText("Анализ повреждения: сектор TRIM с нулевыми байтами на смещении 0x00000000")
        heal.log_view.appendPlainText("Обнаружен донор: D:\\Photos\\Good\\CRW_0999.JPG (Canon EOS 5D Mark IV)")
        heal.log_view.appendPlainText("Совпадение матриц квантования DQT: 100% (хэш e4b8c9...)")
        heal.lbl_stats_badge.setText("4000x3000 • 3.8 МБ")
        heal.lbl_stats_badge.setVisible(True)

        win.tabs.setCurrentIndex(1)
        win.resize(width, height)
        app.processEvents()

        heal_path = out_dir / f"photo_healer_recovery_{label}.png"
        pix = win.grab()
        pix.save(str(heal_path))
        print(f"Saved: {heal_path} ({pix.width()}x{pix.height()})")

        # 2. Preview Gallery Tab (CarveView)
        carve = win.carve_view
        colors = [QColor("#1e3a8a"), QColor("#065f46"), QColor("#831843"), QColor("#701a75"), QColor("#312e81")]
        for i in range(8):
            color = colors[i % len(colors)]
            thumb_bytes = create_sample_thumbnail(160, 120, color)
            carve.add_preview({
                "preview_type": "mpf" if i % 2 == 0 else "exif_thumb",
                "type_label": "MPF PREVIEW" if i % 2 == 0 else "EXIF THUMB",
                "source_name": f"CRW_{1000+i}.JPG",
                "resolution": "1920x1080" if i % 2 == 0 else "160x120",
                "size_str": "240 КБ" if i % 2 == 0 else "14 КБ",
                "size_bytes": 245760 if i % 2 == 0 else 14336,
                "data": thumb_bytes,
            })

        win.tabs.setCurrentIndex(2)
        win.resize(width, height)
        app.processEvents()

        carve_path = out_dir / f"photo_healer_gallery_{label}.png"
        pix_carve = win.grab()
        pix_carve.save(str(carve_path))
        print(f"Saved: {carve_path} ({pix_carve.width()}x{pix_carve.height()})")

    print("\nAll responsiveness verification screenshots generated successfully!")


if __name__ == "__main__":
    main()
