# SPDX-License-Identifier: GPL-3.0-only
"""Transactional per-user macOS install; retains Config, State and project data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import browser_platform
import browser_mac_bundle
from runtime_support import get_local_base_dir

HOST_NAME = "com.airenamer.browser"


def _write(path: Path, data: dict):
    import config
    config.atomic_write_json(str(path), data)


def _snapshot(path: Path):
    return path.read_bytes() if path.exists() else None


def _restore(path: Path, data):
    if data is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(data)


def validate_package(package: Path) -> str:
    version = json.loads((package / "version.json").read_text(encoding="utf-8"))["version"]
    manifest = json.loads((package / "browser/manifest.json").read_text(encoding="utf-8"))
    target = json.loads((package / "package-target.json").read_text(encoding="utf-8"))
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version) or not isinstance(manifest, dict) or manifest.get("version") != version:
        raise ValueError("Package and extension versions must match.")
    if target != {"platform": "macos", "architecture": "arm64", "minimumMacOS": "14.0"}:
        raise ValueError("This package is not a macOS 14+ arm64 Browser package.")
    if manifest.get("name") != "AIRenamer · Browser":
        raise ValueError("The package does not contain the AIRenamer Browser extension.")
    host = package / "dist/MediaRenamerBrowserNative"
    browser_mac_bundle.validate_layout(host)
    if sys.platform == "darwin":
        browser_mac_bundle.verify_signatures(host)
    return version


def install(package: Path, *, extension_id: str | None = None, open_chrome: bool = True,
            prepare_ffmpeg: bool = True) -> dict:
    import utils
    package = package.resolve()
    version = validate_package(package)
    base = Path(get_local_base_dir()) / "Browser"
    extension = base / "Extension"
    installation = base / "installation.json"
    native_manifest = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts" / (HOST_NAME + ".json")
    previous = json.loads(installation.read_text(encoding="utf-8")) if installation.exists() else {}
    extension_id = extension_id or previous.get("extensionId")
    if extension_id and not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("The Chrome extension ID must contain 32 letters from a to p.")
    if extension.exists():
        if extension.is_symlink():
            raise ValueError("The installed extension folder must not be a symlink.")
        old = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
        if old.get("name") != "AIRenamer · Browser":
            raise ValueError("The extension destination belongs to another application.")
    base.mkdir(parents=True, exist_ok=True)
    native_manifest.parent.mkdir(parents=True, exist_ok=True)
    with utils.exclusive_file_lock(str(base / "install.lock"), timeout=30, stale_seconds=600):
        saved_installation, saved_native = _snapshot(installation), _snapshot(native_manifest)
        host_directory = base / "Host" / (version + "-" + uuid.uuid4().hex[:12])
        extension_changed = False
        try:
            with tempfile.TemporaryDirectory(prefix=".install-", dir=base) as temporary:
                stage = Path(temporary)
                staged_extension, backup = stage / "Extension", stage / "PreviousExtension"
                shutil.copytree(package / "browser", staged_extension)
                shutil.copytree(package / "dist/MediaRenamerBrowserNative", host_directory, symlinks=True)
                executable = host_directory / "MediaRenamerBrowserNative"
                executable.chmod(0o755)
                helper = host_directory / "BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge"
                helper.chmod(0o755)
                browser_mac_bundle.validate_layout(host_directory)
                if sys.platform == "darwin":
                    browser_mac_bundle.verify_signatures(host_directory)
                if extension.exists():
                    os.replace(extension, backup)
                try:
                    os.replace(staged_extension, extension)
                    extension_changed = True
                    if not extension_id:
                        print("Open chrome://extensions, enable Developer mode, choose Load unpacked:")
                        print(extension)
                        if open_chrome:
                            try:
                                subprocess.Popen(["/usr/bin/open", "-a", "Google Chrome", "chrome://extensions"],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except OSError:
                                print("Open chrome://extensions manually to finish setup.")
                        extension_id = input("Paste the ID shown on the AIRenamer extension card: ").strip()
                        if not re.fullmatch(r"[a-p]{32}", extension_id):
                            raise ValueError("Invalid Chrome extension ID; installation was rolled back.")
                    manifest = {"name": HOST_NAME, "description": "AIRenamer local project and media access",
                                "path": str(executable), "type": "stdio",
                                "allowed_origins": ["chrome-extension://" + extension_id + "/"]}
                    _write(native_manifest, manifest)
                    settings = {**previous, "version": version, "extensionFolder": str(extension),
                                "extensionId": extension_id, "hostPath": str(executable),
                                "platform": "macos", "architecture": "arm64", "legacySharedSettings": False}
                    _write(installation, settings)
                except BaseException:
                    if extension_changed:
                        shutil.rmtree(extension)
                    if backup.exists():
                        os.replace(backup, extension)
                    raise
        except BaseException:
            _restore(installation, saved_installation)
            _restore(native_manifest, saved_native)
            if host_directory.exists():
                shutil.rmtree(host_directory)
            raise
    if prepare_ffmpeg:
        try:
            subprocess.Popen([str(executable), "--install-ffmpeg"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=browser_platform.detached_host_environment(), close_fds=True)
        except OSError as exc:
            (base / "ffmpeg-setup.log").write_text(str(exc) + "\n", encoding="utf-8")
    print("AIRenamer Browser " + version + " installed. Reopen its Chrome panel to connect.")
    return settings


def uninstall() -> None:
    base = Path(get_local_base_dir()) / "Browser"
    settings = json.loads((base / "installation.json").read_text(encoding="utf-8"))
    native = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts" / (HOST_NAME + ".json")
    if native.exists():
        manifest = json.loads(native.read_text(encoding="utf-8"))
        if manifest.get("path") != settings.get("hostPath"):
            raise ValueError("Native host registration belongs to another installation.")
        native.unlink()
    # Keep user data and old binaries, which may still be in use by Chrome.
    (base / "installation.json").unlink()
    print("Native host disconnected. Remove AIRenamer in chrome://extensions. Project settings are preserved.")


def main(arguments) -> int:
    if not browser_platform.is_macos() or platform.machine().lower() != "arm64":
        raise RuntimeError("Install this package on an Apple Silicon Mac.")
    if int(platform.mac_ver()[0].split(".")[0]) < 14:
        raise RuntimeError("AIRenamer Browser requires macOS 14 or newer.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-folder", type=Path)
    parser.add_argument("--extension-id")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--no-ffmpeg", action="store_true", help="Defer video tool setup until first conversion")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(arguments)
    if args.uninstall:
        uninstall()
    elif args.package_folder:
        install(args.package_folder, extension_id=args.extension_id, open_chrome=not args.no_open,
                prepare_ffmpeg=not args.no_ffmpeg)
    else:
        parser.error("--package-folder is required")
    return 0
