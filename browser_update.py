# SPDX-License-Identifier: GPL-3.0-only
"""Verified, unattended updates for the unpacked AIRenamer Browser extension."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import utils

REPOSITORY = "RomanPronDev/AIRenamer-Browser"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ARCHIVE_BYTES = 160 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class UpdateError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise UpdateError("Invalid release version")
    return tuple(map(int, value.split(".")))


def installation_file() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "MediaRenamer" / "Browser" / "installation.json"


def read_installation(path: Path | None = None) -> dict:
    settings = json.loads((path or installation_file()).read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict) or not settings.get("extensionFolder"):
        raise UpdateError("Browser installation is incomplete")
    extension = Path(settings["extensionFolder"])
    if not extension.is_absolute() or not (extension / "manifest.json").is_file():
        raise UpdateError("Installed extension folder is unavailable")
    return settings


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "User-Agent": "AIRenamer-Browser-Updater",
        "Accept": "application/vnd.github+json",
    })


def _metadata(url: str = API_URL) -> dict:
    with urllib.request.urlopen(_request(url), timeout=25) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise UpdateError("Release metadata exceeds safety limit")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise UpdateError("Invalid release metadata")
    return data


def select_release(metadata: dict, installed_version: str) -> tuple[str, dict] | None:
    tag = metadata.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("Invalid release tag")
    version = tag[1:]
    if version_tuple(version) <= version_tuple(installed_version):
        return None
    if metadata.get("draft") or metadata.get("prerelease"):
        return None
    name = f"AIRenamer-Browser-{version}.zip"
    assets = [asset for asset in metadata.get("assets", [])
              if isinstance(asset, dict) and asset.get("name") == name]
    if len(assets) != 1:
        raise UpdateError(f"Release is missing {name}")
    asset = assets[0]
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", str(asset.get("digest", ""))):
        raise UpdateError("Release asset has no SHA-256 digest")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    if asset.get("browser_download_url") != expected_url:
        raise UpdateError("Unexpected release asset URL")
    if not isinstance(asset.get("size"), int) or not 0 < asset["size"] <= MAX_ARCHIVE_BYTES:
        raise UpdateError("Release asset exceeds safety limit")
    return version, asset


def download_asset(asset: dict, target: Path) -> None:
    expected = asset["digest"].split(":", 1)[1].lower()
    digest = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(_request(asset["browser_download_url"]), timeout=120) as source, target.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_ARCHIVE_BYTES or count > asset["size"]:
                raise UpdateError("Download exceeds declared size")
            output.write(chunk)
            digest.update(chunk)
    if count != asset["size"] or digest.hexdigest() != expected:
        raise UpdateError("Release archive failed size or SHA-256 verification")


def extract_release(archive_path: Path, destination: Path, version: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = set()
        total_size = 0
        for item in archive.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name or ":" in name
                    or any(part in (".", "..") for part in parts)
                    or (not item.is_dir() and item.file_size > MAX_ARCHIVE_BYTES)
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise UpdateError("Release archive contains an unsafe path")
            total_size += item.file_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise UpdateError("Release archive expands beyond safety limit")
            key = name.casefold()
            if key in names:
                raise UpdateError("Release archive contains duplicate paths")
            names.add(key)
        archive.extractall(destination)
    try:
        package_version = json.loads((destination / "version.json").read_text(encoding="utf-8-sig"))["version"]
        extension_version = json.loads((destination / "browser" / "manifest.json").read_text(encoding="utf-8-sig"))["version"]
    except (OSError, ValueError, KeyError) as exc:
        raise UpdateError("Release archive is incomplete") from exc
    if package_version != version or extension_version != version:
        raise UpdateError("Release version does not match its archive")
    if not (destination / "Install-BrowserNative.ps1").is_file() or not (
        destination / "dist" / "MediaRenamerBrowserNative.exe"
    ).is_file():
        raise UpdateError("Release archive lacks installer or native host")


def check_and_install(installed_version: str, *, settings_path: Path | None = None) -> str:
    settings = read_installation(settings_path)
    selected = select_release(_metadata(), installed_version)
    if selected is None:
        return "current"
    version, asset = selected
    base = (settings_path or installation_file()).parent
    base.mkdir(parents=True, exist_ok=True)
    with utils.exclusive_file_lock(str(base / "update.lock"), timeout=1, stale_seconds=3600):
        with tempfile.TemporaryDirectory(prefix="update-", dir=base) as temp:
            archive = Path(temp) / "release.zip"
            package = Path(temp) / "package"
            package.mkdir()
            download_asset(asset, archive)
            extract_release(archive, package, version)
            args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(package / "Install-BrowserNative.ps1"), "-ExtensionFolder",
                    settings["extensionFolder"]]
            result = subprocess.run(args, capture_output=True, text=True, timeout=300,
                                    creationflags=0x08000000 if os.name == "nt" else 0,
                                    env=utils.sanitized_subprocess_environment())
            if result.returncode:
                raise UpdateError(f"Installer failed: {result.stderr[-1000:] or result.stdout[-1000:]}")
    return version


def run_auto_update(installed_version: str) -> int:
    log = installation_file().parent / "update.log"
    try:
        result = check_and_install(installed_version)
        message = f"Update check completed: {result}"
        code = 0
    except Exception as exc:
        message = f"Update check failed: {exc}"
        code = 1
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(message + "\n", encoding="utf-8")
    return code
