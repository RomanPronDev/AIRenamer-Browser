# SPDX-License-Identifier: GPL-3.0-only
"""Build a versioned Browser package without bundling FFmpeg."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    "browser_native.py", "browser_update.py", "browser_release.py",
    "config.py", "utils.py", "runtime_support.py", "version.json",
    "MediaRenamerBrowserNative.spec", "browser-requirements.txt",
    "Install-BrowserNative.ps1", "Setup-Browser.cmd", "Update-Browser.ps1",
    "Update-Browser.cmd", "Uninstall-BrowserNative.ps1", "LICENSE",
    "BROWSER_SOURCE.md", "BROWSER_THIRD_PARTY_NOTICES.md",
    "third_party_licenses/Python-3.14.5-PSF.txt",
    "third_party_licenses/Pillow-12.2.0-MIT-CMU-and-third-party.txt",
    "third_party_licenses/PyInstaller-6.20.0-GPL-2.0-or-later-with-Bootloader-Exception.txt",
    "tests/test_browser_native.py", "tests/test_browser_update.py",
    "tests/test_config.py", "tests/test_utils.py",
)


def source_files() -> list[str]:
    return sorted(list(SOURCE_FILES) + [
        item.relative_to(ROOT).as_posix() for item in (ROOT / "browser").rglob("*")
        if item.is_file()
    ])


def stage_source(destination: Path) -> None:
    """Copy the exact Browser source selection into an isolated publication tree."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name in source_files():
        source = ROOT / name
        if not source.is_file():
            raise FileNotFoundError(source)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / "browser" / "README.md", destination / "README.md")


def package(version: str | None = None, *, build: bool = True) -> Path:
    version = version or json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    extension_version = json.loads((ROOT / "browser" / "manifest.json").read_text(encoding="utf-8"))["version"]
    if version != extension_version:
        raise ValueError("Version mismatch between package and Chrome manifest")
    if build:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                        str(ROOT / "MediaRenamerBrowserNative.spec")],
                       cwd=ROOT, check=True)
    executable = ROOT / "dist" / "MediaRenamerBrowserNative.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    files = source_files()
    output = ROOT / "dist" / f"AIRenamer-Browser-{version}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for name in files:
            path = ROOT / name
            if not path.is_file():
                raise FileNotFoundError(path)
            archive.write(path, name)
        archive.write(ROOT / "browser" / "README.md", "README.md")
        archive.write(executable, "dist/MediaRenamerBrowserNative.exe")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Built archive failed integrity check")
        if any(name.casefold().endswith("ffmpeg.exe") for name in archive.namelist()):
            raise ValueError("FFmpeg must not be bundled")
    return output


if __name__ == "__main__":
    print(package())
