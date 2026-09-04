# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


project_root = Path(SPECPATH).resolve()

datas = []
binaries = []

binaries += collect_dynamic_libs('PySide6')
datas += collect_data_files('PySide6')

config_file = project_root / "danmakustudio.yaml"
if config_file.exists():
    datas.append((str(config_file), "."))

assets_dir = project_root / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))

icon_file = project_root / "assets" / "DanmakuStudio.ico"
icon = str(icon_file) if icon_file.exists() else None

a = Analysis(
    [str(project_root / "src" / "danmakustudio" / "_pyinstaller_gui.py")],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "lxml.etree",
        "yaml",
        "loguru",
        "tqdm",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DanmakuStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DanmakuStudio",
)
