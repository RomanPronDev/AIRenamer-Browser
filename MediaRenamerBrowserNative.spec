# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
"""Native Messaging host for AIRenamer Browser."""
from pathlib import Path
import os
import sys

root = Path(SPECPATH)
macos = sys.platform == "darwin"
a = Analysis(
    ["browser_native.py"],
    pathex=[SPECPATH],
    binaries=[] if sys.platform == "darwin" else [(str(root / "build_assets" / "BrowserDragBridge.exe"), ".")],
    datas=[(str(root / "version.json"), "."),
           (str(root / "LICENSE"), "."),
           (str(root / "BROWSER_SOURCE.md"), "."),
           (str(root / "BROWSER_THIRD_PARTY_NOTICES.md"), "."),
           (str(root / "third_party_licenses" / "Python-3.14.5-PSF.txt"), "third_party_licenses"),
           (str(root / "third_party_licenses" / "Pillow-12.2.0-MIT-CMU-and-third-party.txt"), "third_party_licenses"),
           (str(root / "third_party_licenses" / "PyInstaller-6.20.0-GPL-2.0-or-later-with-Bootloader-Exception.txt"), "third_party_licenses")],
    hiddenimports=["config", "utils", "browser_update", "browser_drag", "browser_settings",
                   "browser_platform", "browser_ffmpeg", "browser_mac_install", "browser_mac_bundle", "PIL.Image", "PIL.ImageOps"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt6", "numpy", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [] if macos else a.binaries, [] if macos else a.datas, [],
    name="MediaRenamerBrowserNative",
    exclude_binaries=macos,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=os.environ.get("AIRENAMER_SIGNING_IDENTITY") if sys.platform == "darwin" else None,
)
if macos:
    # Keep Python.framework/resources/links in a stable tree instead of unpacking
    # them into a onefile temporary directory on every Chrome invocation.
    collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                      name="MediaRenamerBrowserNative")
