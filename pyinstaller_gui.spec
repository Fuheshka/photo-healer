# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Photo Healer GUI (photo-healer-gui.exe).

Engineered according to Ponytail canons:
- Bundles PySide6 Qt6 core desktop widgets (QtCore, QtGui, QtWidgets)
- Includes essential Windows platform & styling plugins (platforms, styles, imageformats)
- Strictly excludes massive unused Qt subsystems (WebEngine, QML/Quick, 3D, Multimedia)
- Native dark theme palette and Windows desktop subsystem (console=False)
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve()
SRC_PATH = str(PROJECT_ROOT / "src")

# Only collect necessary pure submodules for photo_healer
hidden_imports = [
    "photo_healer",
    "photo_healer.core",
    "photo_healer.core.carver",
    "photo_healer.core.entropy",
    "photo_healer.core.parser",
    "photo_healer.core.resync",
    "photo_healer.core.splicer",
    "photo_healer.core.validator",
    "photo_healer.gui",
    "photo_healer.gui.app",
    "photo_healer.gui.i18n",
    "photo_healer.gui.icon",
    "photo_healer.gui.model",
    "photo_healer.gui.worker",
    "photo_healer.gui.models",
    "photo_healer.gui.models.file_table_model",
    "photo_healer.gui.views",
    "photo_healer.gui.views.carve_view",
    "photo_healer.gui.views.heal_view",
    "photo_healer.gui.views.main_window",
    "photo_healer.gui.views.triage_view",
    "photo_healer.gui.widgets",
    "photo_healer.gui.widgets.split_preview",
    "photo_healer.gui.workers",
    "photo_healer.gui.workers.carve_worker",
    "photo_healer.gui.workers.heal_worker",
    "photo_healer.gui.workers.triage_worker",
    "PIL",
    "PIL.Image",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

icon_png = PROJECT_ROOT / "assets" / "icon.png"
datas = [(str(icon_png), "assets")] if icon_png.is_file() else []

a = Analysis(
    [str(PROJECT_ROOT / "src" / "photo_healer" / "gui" / "app.py")],
    pathex=[SRC_PATH],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Unused Qt6 heavy modules
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQuick3D",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtSensors",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtSql",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtCharts",
        "PySide6.QtSpatialAudio",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtTest",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtStateMachine",
        "PySide6.QtUiTools",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        # Unused heavy packages
        "tkinter",
        "_tkinter",
        "turtle",
        "turtledemo",
        "curses",
        "numpy",
        "scipy",
        "matplotlib",
        "pandas",
        "pytest",
        "unittest",
        "test",
        "doctest",
        "xmlrpc",
        "pydoc",
        "pydoc_data",
        "idlelib",
        "lib2to3",
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="photo-healer-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
