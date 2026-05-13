# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Voice2Claude
# Build with:  pyinstaller --clean Voice2Claude.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os

block_cipher = None

# Bundle these files alongside the frozen Python interpreter.
# Format: (source path, destination dir inside the bundle).
# NOTE: we do NOT bundle .env. Users put it next to the .exe themselves.
datas = [
    ("index.html", "."),
    ("static", "static"),
]

# anthropic and uvicorn ship some files that PyInstaller's auto-detection misses
datas += collect_data_files("anthropic")
datas += collect_data_files("uvicorn")

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("pydantic")
    + ["dotenv"]
)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep the bundle slim by excluding things we definitely don't use
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Voice2Claude",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # no console window (production)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="static/app.ico" if os.path.exists("static/app.ico") else None,
)
