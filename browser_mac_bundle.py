# SPDX-License-Identifier: GPL-3.0-only
"""Preserve framework links and signatures throughout the macOS package lifecycle."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import subprocess
import zipfile

HOST_PREFIX = "dist/MediaRenamerBrowserNative"


def validate_tree(root: Path) -> None:
    root = root.resolve()
    for item in root.rglob("*"):
        if item.is_symlink():
            target = os.readlink(item)
            if not target or os.path.isabs(target) or "\\" in target or ":" in target:
                raise ValueError(f"Invalid runtime symlink: {item}")
            try:
                resolved = item.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"Broken or cyclic runtime symlink: {item}") from exc
            if not resolved.is_relative_to(root):
                raise ValueError(f"Runtime symlink escapes its bundle: {item}")


def validate_layout(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("The macOS runtime must be a directory, not a symlink.")
    validate_tree(root)
    for relative in ("MediaRenamerBrowserNative", "_internal/Python.framework/Python",
                     "_internal/Python.framework/Resources/Info.plist",
                     "BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge",
                     "BrowserDragBridge.app/Contents/Info.plist"):
        if not (root / relative).is_file():
            raise ValueError("The macOS package is incomplete: " + relative)


def _codesign(arguments: list[str], path: Path) -> None:
    result = subprocess.run(["/usr/bin/codesign", *arguments, str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise ValueError(f"Code signature check/signing failed for {path}: {detail}")


def _frameworks(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*.framework") if not path.is_symlink()),
                  key=lambda path: len(path.parts), reverse=True)


def verify_signatures(root: Path) -> None:
    validate_layout(root)
    for path in [*_frameworks(root), root / "BrowserDragBridge.app", root / "MediaRenamerBrowserNative"]:
        _codesign(["--verify", "--deep", "--strict", "--verbose=2"], path)


def sign_runtime(root: Path) -> None:
    """Re-seal the final collected frameworks, then verify the distributed bytes."""
    validate_layout(root)
    identity = os.environ.get("AIRENAMER_SIGNING_IDENTITY") or "-"
    arguments = ["--force", "--sign", identity]
    if identity != "-":
        arguments += ["--options", "runtime", "--timestamp"]
    # PyInstaller signs collected Mach-O files. Re-seal each collected framework
    # with its final resources; do not reuse Python's original resource seal.
    for path in [*_frameworks(root), root / "BrowserDragBridge.app", root / "MediaRenamerBrowserNative"]:
        _codesign(arguments, path)
    verify_signatures(root)


def add_tree(archive: zipfile.ZipFile, root: Path, prefix: str = HOST_PREFIX) -> None:
    validate_layout(root)
    for item in sorted(root.rglob("*")):
        name = prefix + "/" + item.relative_to(root).as_posix()
        if item.is_symlink():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, os.readlink(item).encode("utf-8"))
        elif item.is_file():
            archive.write(item, name)


def extract_tree(archive: zipfile.ZipFile, destination: Path) -> None:
    """Called after general ZIP validation; all links are confined to the host."""
    if destination.is_symlink() or destination.exists() and any(destination.iterdir()):
        raise ValueError("macOS extraction requires a new empty directory")
    links = {}
    for item in archive.infolist():
        if stat.S_ISLNK(item.external_attr >> 16):
            name = item.filename
            if not name.startswith(HOST_PREFIX + "/") or item.file_size > 4096:
                raise ValueError("macOS archive contains an unsafe symlink")
            try:
                target = archive.read(item).decode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Invalid symlink encoding") from exc
            normalized = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if (not target or "\x00" in target or target.startswith("/") or "\\" in target or ":" in target
                    or not normalized.startswith(HOST_PREFIX + "/")):
                raise ValueError("macOS archive symlink escapes its bundle")
            links[name] = target
    for item in archive.infolist():
        if any(str(parent) in links for parent in PurePosixPath(item.filename).parents):
            raise ValueError("macOS archive contains a child beneath a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    for item in archive.infolist():
        if item.filename in links:
            continue
        path = destination / item.filename
        if item.is_dir():
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as incoming, path.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            mode = (item.external_attr >> 16) & 0o777
            if mode:
                path.chmod(mode)
    for name, target in links.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
    validate_tree(destination / HOST_PREFIX)
