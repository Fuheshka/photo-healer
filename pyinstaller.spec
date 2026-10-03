# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Photo Healer CLI (photo-healer.exe).

Optimized for ultra-compact distribution (< 15 MB) according to Ponytail canons:
- Strict exclusion of GUI frameworks (PySide6, Shiboken6)
- Strict exclusion of heavy imaging packages (Pillow/PIL)
- Exclusion of unused test and legacy standard library modules
- Pure streaming forensic analysis using native Python standard library
"""

from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve()
SRC_PATH = str(PROJECT_ROOT / "src")

a = Analysis(
    [str(PROJECT_ROOT / "src" / "photo_healer" / "cli" / "main.py")],
    pathex=[SRC_PATH],
    binaries=[],
    datas=[],
    hiddenimports=[
        "photo_healer",
        "photo_healer.cli",
        "photo_healer.cli.banner",
        "photo_healer.cli.i18n",
        "photo_healer.cli.main",
        "photo_healer.cli.updater",
        "photo_healer.core",
        "photo_healer.core.carver",
        "photo_healer.core.donor_discovery",
        "photo_healer.core.donor_pool",
        "photo_healer.core.entropy",
        "photo_healer.core.parser",
        "photo_healer.core.resync",
        "photo_healer.core.splicer",
        "photo_healer.core.thumbnail",
        "photo_healer.core.validator",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI frameworks & heavy graphic suites
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "shiboken6",
        "PIL",
        "Pillow",
        "tkinter",
        "_tkinter",
        "turtle",
        "turtledemo",
        "curses",
        # Unused scientific / data science packages
        "numpy",
        "scipy",
        "matplotlib",
        "pandas",
        # Testing & development suites
        "pytest",
        "unittest",
        "test",
        "doctest",
        # Unused network & daemon servers
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
    name="photo-healer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
