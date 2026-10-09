# SPDX-License-Identifier: GPL-3.0-only
"""Build a versioned Browser package without bundling FFmpeg."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
import browser_platform
import browser_mac_bundle

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    "browser_native.py", "browser_update.py", "browser_release.py", "browser_drag.py", "browser_settings.py",
    "native_drag/DragBridge.cs", "native_drag/Build-DragBridge.ps1",
    "native_drag/README.md",
    "config.py", "utils.py", "runtime_support.py", "version.json", "conftest.py",
    "MediaRenamerBrowserNative.spec", "browser-requirements.txt",
    "Install-BrowserNative.ps1", "Setup-Browser.cmd", "Update-Browser.ps1",
    "Update-Browser.cmd", "Uninstall-BrowserNative.ps1", "LICENSE",
    "BROWSER_SOURCE.md", "BROWSER_THIRD_PARTY_NOTICES.md",
    "third_party_licenses/Python-3.14.5-PSF.txt",
    "third_party_licenses/Pillow-12.2.0-MIT-CMU-and-third-party.txt",
    "third_party_licenses/PyInstaller-6.20.0-GPL-2.0-or-later-with-Bootloader-Exception.txt",
    "tests/test_browser_native.py", "tests/test_browser_update.py", "tests/test_browser_drag.py",
    "tests/test_browser_settings.py",
    "tests/test_browser_ffmpeg.py",
    "tests/test_config.py", "tests/test_utils.py",
    "browser_platform.py", "browser_ffmpeg.py", "browser_mac_install.py", "browser_mac_bundle.py",
    "native_drag/DragBridge.swift", "native_drag/build_macos.py",
    "Setup-Browser.command", "Update-Browser.command", "Uninstall-Browser.command", "Build-Browser.command",
    "browser/MACOS.md", "tests/test_browser_macos.py", "tests/macos_protocol_smoke.py",
    ".github/workflows/browser-macos.yml",
    ".gitattributes",
)


def source_files() -> list[str]:
    return sorted(set(SOURCE_FILES) | {
        item.relative_to(ROOT).as_posix() for item in (ROOT / "browser").rglob("*")
        if item.is_file()
    })


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
    (destination / "README.md").write_text(
        (ROOT / "browser/README.md").read_text(encoding="utf-8").replace(
            "(MACOS.md)", "(browser/MACOS.md)"), encoding="utf-8")


def source_package() -> Path:
    """A complete buildable source handoff, without claiming a macOS binary."""
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    if json.loads((ROOT / "browser/manifest.json").read_text(encoding="utf-8"))["version"] != version:
        raise ValueError("Source and extension versions must match")
    output = ROOT / "dist" / f"AIRenamer-Browser-{version}-source.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files():
            path = ROOT / name
            if name.endswith(".command"):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o100755 << 16
                archive.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"), compress_type=zipfile.ZIP_DEFLATED)
            else:
                archive.write(path, name)
        archive.writestr("README.md", (ROOT / "browser/README.md").read_text(encoding="utf-8").replace(
            "(MACOS.md)", "(browser/MACOS.md)"))
    return output


def package(version: str | None = None, *, build: bool = True) -> Path:
    version = version or json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    extension_version = json.loads((ROOT / "browser" / "manifest.json").read_text(encoding="utf-8"))["version"]
    if version != extension_version:
        raise ValueError("Version mismatch between package and Chrome manifest")
    suffix = browser_platform.package_suffix()
    macos = suffix == "-macos-arm64"
    if build:
        if macos:
            subprocess.run([sys.executable, str(ROOT / "native_drag/build_macos.py")], cwd=ROOT, check=True)
        else:
            subprocess.run(["powershell.exe", "-NoProfile", "-File",
                            str(ROOT / "native_drag" / "Build-DragBridge.ps1")], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                        str(ROOT / "MediaRenamerBrowserNative.spec")],
                       cwd=ROOT, check=True)
    host_directory = ROOT / "dist/MediaRenamerBrowserNative"
    executable = host_directory / "MediaRenamerBrowserNative" if macos else ROOT / "dist/MediaRenamerBrowserNative.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    files = source_files()
    output = ROOT / "dist" / f"AIRenamer-Browser-{version}{suffix}.zip"
    if macos:
        helper = ROOT / "build_assets/BrowserDragBridge.app"
        if not helper.is_dir():
            raise FileNotFoundError(helper)
        shutil.copytree(helper, host_directory / helper.name, dirs_exist_ok=True, symlinks=True)
        browser_mac_bundle.validate_layout(host_directory)
        if sys.platform == "darwin":
            browser_mac_bundle.sign_runtime(host_directory)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for name in files:
            path = ROOT / name
            if not path.is_file():
                raise FileNotFoundError(path)
            if name.endswith(".command"):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o100755 << 16
                archive.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"), compress_type=zipfile.ZIP_DEFLATED)
            else:
                archive.write(path, name)
        archive.writestr("README.md", (ROOT / "browser/README.md").read_text(encoding="utf-8").replace(
            "(MACOS.md)", "(browser/MACOS.md)"))
        if macos:
            archive.writestr("package-target.json", json.dumps({"platform": "macos", "architecture": "arm64", "minimumMacOS": "14.0"}))
            browser_mac_bundle.add_tree(archive, host_directory)
        else:
            archive.write(executable, "dist/MediaRenamerBrowserNative.exe")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Built archive failed integrity check")
        if any(Path(name).name.casefold() in ("ffmpeg.exe", "ffmpeg") for name in archive.namelist()):
            raise ValueError("FFmpeg must not be bundled")
    if macos:
        import browser_update
        with tempfile.TemporaryDirectory(prefix="browser-mac-package-") as temporary:
            browser_update.extract_release(output, Path(temporary) / "package", version,
                                           target_suffix="-macos-arm64")
    return output


if __name__ == "__main__":
    print(source_package() if sys.argv[1:] == ["--source-only"] else package())
