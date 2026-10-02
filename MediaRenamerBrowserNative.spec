# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
"""Native Messaging host for AIRenamer Browser."""
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    ["browser_native.py"],
    pathex=[SPECPATH],
    binaries=[(str(root / "build_assets" / "BrowserDragBridge.exe"), ".")],
    datas=[(str(root / "version.json"), "."),
           (str(root / "LICENSE"), "."),
           (str(root / "BROWSER_SOURCE.md"), "."),
           (str(root / "BROWSER_THIRD_PARTY_NOTICES.md"), "."),
           (str(root / "third_party_licenses" / "Python-3.14.5-PSF.txt"), "third_party_licenses"),
           (str(root / "third_party_licenses" / "Pillow-12.2.0-MIT-CMU-and-third-party.txt"), "third_party_licenses"),
           (str(root / "third_party_licenses" / "PyInstaller-6.20.0-GPL-2.0-or-later-with-Bootloader-Exception.txt"), "third_party_licenses")],
    hiddenimports=["config", "utils", "browser_update", "browser_drag", "browser_settings", "PIL.Image", "PIL.ImageOps"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt6", "numpy", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="MediaRenamerBrowserNative",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
