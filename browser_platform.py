# SPDX-License-Identifier: GPL-3.0-only
"""Operating-system integration shared by the Browser host and installer."""
from __future__ import annotations

import platform
from pathlib import Path
import subprocess
import sys


def is_macos() -> bool:
    return sys.platform == "darwin"


def package_suffix() -> str:
    if is_macos():
        if platform.machine().lower() not in ("arm64", "aarch64"):
            raise RuntimeError("This Browser package supports Apple Silicon only.")
        return "-macos-arm64"
    if sys.platform != "win32":
        raise RuntimeError("AIRenamer Browser supports Windows and macOS only.")
    return ""


def capabilities() -> dict:
    return {"platform": "macos" if is_macos() else "windows",
            "architecture": platform.machine(),
            "capabilities": {"folderPicker": True, "revealFile": True,
                             "nativeDrag": True, "ffmpegDownload": True},
            "fileManager": "Finder" if is_macos() else "Explorer",
            "projectPathExample": "/Volumes/Projects/MyProject" if is_macos() else r"D:\Projects\MyProject"}


def bridge_executable() -> Path:
    frozen = getattr(sys, "frozen", False)
    if is_macos():
        root = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent / "build_assets"
        return root / "BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge"
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / ("BrowserDragBridge.exe" if frozen else "build_assets/BrowserDragBridge.exe")


def open_folder(path: str, *, reveal: bool = False) -> None:
    if is_macos():
        subprocess.Popen(["/usr/bin/open", *(["-R"] if reveal else []), path],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif reveal:
        subprocess.Popen(["explorer.exe", "/select,", path])
    else:
        import os
        os.startfile(path)


def detached_host_environment() -> dict:
    import utils
    environment = utils.sanitized_subprocess_environment()
    # A one-file child must own its extraction directory after the parent exits.
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment
