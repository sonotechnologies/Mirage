# PyInstaller spec — one-folder build (one-file unpacks ~1 GB on every launch; too slow).
#   pyinstaller build/mirage.spec --noconfirm --distpath dist --workpath build/out
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 — SPECPATH is injected by PyInstaller

datas = [
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "models"), "models"),
    (str(ROOT / "drivers"), "drivers"),
    (str(ROOT / "LICENSES.md"), "."),
]
datas += collect_data_files("webview")          # pywebview's JS shims
binaries = collect_dynamic_libs("onnxruntime")   # includes DirectML.dll for onnxruntime-directml
binaries += collect_dynamic_libs("pyvirtualcam")

hiddenimports = (
    collect_submodules("webview.platforms")
    + collect_submodules("pyvirtualcam")
    + ["keyboard", "clr_loader", "pythonnet"]
)

a = Analysis(  # noqa: F821
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "onnx"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mirage",
    icon=str(ROOT / "build" / "mirage.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Mirage",
)
