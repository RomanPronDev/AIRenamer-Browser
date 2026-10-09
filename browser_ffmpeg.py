# SPDX-License-Identifier: GPL-3.0-only
"""Browser-only, verified FFmpeg provisioning for Windows and Apple Silicon."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile

URL = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip"
SHA256 = "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"
VERSION = "9.0.2"
MAX_BYTES = 256 * 1024 * 1024
_validated = {}
_windows_validated = {}
WINDOWS_RELEASE = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/'
WINDOWS_ASSET = 'ffmpeg-master-latest-win64-gpl.zip'
ACTIVE_STATES = ('queued', 'checking', 'downloading', 'verifying')


def _hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def status_path(target: Path) -> Path:
    return target.parent / "ffmpeg-status.json"


def status(target: Path) -> dict:
    try:
        value = json.loads(status_path(target).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {"state": "missing"}
        updated = value.get("updatedAt", 0)
        if not isinstance(updated, (int, float)):
            updated = 0
        if value.get("state") in ACTIVE_STATES and (
                time.time() - updated > 900):
            return {"state": "error", "error": "Video tool preparation stopped. Please retry."}
        return value
    except (OSError, ValueError):
        return {"state": "missing"}


def _report(target: Path, state: str, **details) -> None:
    import config
    config.atomic_write_json(str(status_path(target)), {"state": state, "version": WINDOWS_ASSET if details.get('platform')=='windows' else VERSION,
                                                      "updatedAt": time.time(), **details})


def _windows_valid(path: Path) -> bool:
    try:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if _windows_validated.get(key):
            return True
        import utils
        if not utils.validate_ffmpeg_executable(str(path)):
            return False
        if len(_windows_validated) >= 32:
            _windows_validated.clear()
        _windows_validated[key] = True
        return True
    except OSError:
        return False


def _local_windows(target: Path) -> str | None:
    if _windows_valid(target):
        return str(target)
    installed = shutil.which('ffmpeg.exe')
    if installed and _windows_valid(Path(installed)):
        return installed
    return None


def tool_status(target: Path) -> dict:
    """Settings status on either platform, without starting a download."""
    import browser_platform
    if browser_platform.is_macos():
        value=status(target)
        if value.get('state')=='ready' and not valid(target):
            return {'state':'missing'}
        return value
    installed = _local_windows(target)
    if installed:
        return {'state': 'ready', 'path': installed}
    return status(target)


def _windows_source(timeout: float) -> tuple[str, str]:
    import utils
    try:
        metadata = utils._load_release_metadata(timeout=min(timeout, 20))
        asset = utils._select_ffmpeg_asset(metadata)
        return str(asset['browser_download_url']), utils._checksum_from_release(metadata, asset, timeout=min(timeout, 20))
    except Exception:
        # Official floating release URLs also work when unauthenticated API calls
        # are throttled or a proxy blocks api.github.com. Never skip verification.
        text = utils._read_url_bytes(WINDOWS_RELEASE+'checksums.sha256', timeout=min(timeout, 30), limit=1024*1024).decode('utf-8')
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip('*') == WINDOWS_ASSET:
                import re
                if re.fullmatch(r'[a-fA-F0-9]{64}', parts[0]):
                    return WINDOWS_RELEASE+WINDOWS_ASSET, parts[0].lower()
        raise utils.FFmpegResolutionError('The official FFmpeg checksum list lacks the Windows build.')


def _download_windows(url: str, destination: Path, digest: str, target: Path, timeout: float) -> None:
    import utils
    started = time.monotonic()
    last_report = started
    sha = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(utils._request(url), timeout=min(timeout, 60)) as response, destination.open('xb') as output:
        length = response.headers.get('Content-Length')
        total = int(length) if length and length.isdigit() else None
        if total and total > MAX_BYTES:
            raise utils.FFmpegResolutionError('FFmpeg archive exceeds the download limit.')
        _report(target, 'downloading', bytes=0, total=total, platform='windows')
        while chunk := response.read(1024*1024):
            count += len(chunk)
            if count > MAX_BYTES:
                raise utils.FFmpegResolutionError('FFmpeg archive exceeds the download limit.')
            if time.monotonic()-started > timeout:
                raise TimeoutError('FFmpeg download timed out.')
            sha.update(chunk)
            output.write(chunk)
            if time.monotonic()-last_report >= .5:
                _report(target, 'downloading', bytes=count, total=total, platform='windows')
                last_report = time.monotonic()
        output.flush()
        os.fsync(output.fileno())
    if total is not None and count != total:
        raise urllib.error.URLError('FFmpeg download was interrupted.')
    if sha.hexdigest() != digest:
        raise utils.FFmpegResolutionError('FFmpeg archive SHA-256 mismatch; the release may have changed during download.')


def _retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 429, 500, 502, 503, 504)
    return isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)) or 'SHA-256 mismatch' in str(error)


def resolve_windows(target_path: str, *, timeout: float = 600) -> str:
    """Reuse local tools first; failed attempts are never memoized for a session."""
    import utils
    target = Path(target_path).absolute()
    installed = _local_windows(target)
    if installed:
        _report(target, 'ready', path=installed, platform='windows')
        return installed
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with utils.exclusive_file_lock(str(target)+utils.FFMPEG_LOCK_SUFFIX, timeout=timeout, stale_seconds=max(timeout*2, 1200)):
            installed = _local_windows(target)
            if installed:
                _report(target, 'ready', path=installed, platform='windows')
                return installed
            deadline = time.monotonic()+timeout
            for attempt in range(3):
                try:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('FFmpeg preparation timed out. Please retry.')
                    _report(target, 'checking', attempt=attempt+1, platform='windows')
                    url, digest = _windows_source(remaining)
                    with tempfile.TemporaryDirectory(prefix='.ffmpeg-install-', dir=target.parent) as temporary:
                        archive_path = Path(temporary)/'ffmpeg.zip'
                        candidate = Path(temporary)/'ffmpeg.exe'
                        _download_windows(url, archive_path, digest, target, max(1, deadline-time.monotonic()))
                        _report(target, 'verifying', platform='windows')
                        with zipfile.ZipFile(archive_path) as archive:
                            members = [item for item in archive.infolist() if not item.is_dir() and item.filename.rsplit('/', 1)[-1].lower()=='ffmpeg.exe']
                            if len(members)!=1 or members[0].file_size>512*1024*1024 or (members[0].external_attr>>16)&0o170000==0o120000:
                                raise utils.FFmpegResolutionError('FFmpeg archive lacks one regular Windows executable.')
                        utils._extract_ffmpeg(str(archive_path), str(candidate))
                        if not _windows_valid(candidate):
                            raise utils.FFmpegResolutionError('Downloaded FFmpeg cannot run on this computer. Check Windows/CPU compatibility or company launch policy.')
                        os.replace(candidate, target)
                        if not _windows_valid(target):
                            raise utils.FFmpegResolutionError('Installed FFmpeg cannot run. Check company launch policy and retry.')
                    _report(target, 'ready', path=str(target), platform='windows')
                    return str(target)
                except Exception as exc:
                    if attempt==2 or not _retryable(exc) or deadline-time.monotonic()<2:
                        raise
                    time.sleep(min(attempt+1, max(0, deadline-time.monotonic())))
    except Exception as exc:
        detail=str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            detail=f'HTTP {exc.code}: check access to GitHub release downloads, then retry.'
        elif isinstance(exc, urllib.error.URLError):
            detail='Network, proxy or certificate error: '+str(exc.reason)+'. Check access to GitHub, then retry.'
        _report(target, 'error', error=detail, platform='windows')
        raise utils.FFmpegResolutionError('Could not prepare Windows FFmpeg: '+detail) from exc


def provision(target_path: str, *, timeout: float = 600) -> str:
    import browser_platform
    if browser_platform.is_macos():
        return resolve(target_path, timeout=timeout)
    return resolve_windows(target_path, timeout=timeout)


def check_macho(path: Path) -> None:
    """Reject a wrong architecture or a deployment target newer than macOS 14."""
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise RuntimeError("FFmpeg is not a valid Mach-O executable.")
        magic, cpu, _, _, commands, command_bytes, _, _ = struct.unpack("<8I", header)
        if magic != 0xFEEDFACF or cpu != 0x0100000C or command_bytes > 1024 * 1024:
            raise RuntimeError("FFmpeg must be a native Apple Silicon executable.")
        data = stream.read(command_bytes)
    offset = 0
    minimum = None
    for _ in range(commands):
        if offset + 8 > len(data):
            raise RuntimeError("Invalid FFmpeg Mach-O load commands.")
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > len(data):
            raise RuntimeError("Invalid FFmpeg Mach-O load command size.")
        if command == 0x32 and size >= 24:  # LC_BUILD_VERSION
            platform_id, minimum = struct.unpack_from("<II", data, offset + 8)
            if platform_id != 1:
                raise RuntimeError("FFmpeg is not built for macOS.")
        elif command == 0x24 and size >= 16:  # LC_VERSION_MIN_MACOSX
            minimum = struct.unpack_from("<I", data, offset + 8)[0]
        offset += size
    if minimum is None or minimum > 0x000E0000:
        raise RuntimeError("The FFmpeg build requires a system newer than macOS 14.")


def valid(target: Path) -> bool:
    try:
        stat = target.stat()
        key = (str(target), stat.st_size, stat.st_mtime_ns)
        if _validated.get(key):
            return True
        receipt = json.loads(target.with_name("ffmpeg-receipt.json").read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("archiveSha256") != SHA256 or receipt.get("binarySha256") != _hash(target):
            return False
        check_macho(target)
        import utils
        result = subprocess.run([str(target), "-version"], capture_output=True, timeout=15,
                                env=utils.sanitized_subprocess_environment())
        if result.returncode or not result.stdout.startswith(b"ffmpeg version " + VERSION.encode()):
            return False
        _validated[key] = True
        return True
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return False


def resolve(target_path: str, *, timeout: float = 120) -> str:
    import utils
    target = Path(target_path)
    if valid(target):
        _report(target, "ready")
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with utils.exclusive_file_lock(str(target) + ".lock", timeout=timeout, stale_seconds=max(timeout * 2, 600)):
        if valid(target):
            _report(target, "ready")
            return str(target)
        try:
            with tempfile.TemporaryDirectory(prefix=".ffmpeg-install-", dir=target.parent) as temporary:
                archive_path = Path(temporary) / "ffmpeg.zip"
                candidate = Path(temporary) / "ffmpeg"
                request = urllib.request.Request(URL, headers={"User-Agent": "AIRenamer-Browser"})
                started = time.monotonic()
                _report(target, "downloading", bytes=0, total=None)
                with urllib.request.urlopen(request, timeout=min(timeout, 60)) as source, archive_path.open("xb") as output:
                    length = source.headers.get("Content-Length")
                    total = int(length) if length and length.isdigit() else None
                    count = 0
                    digest = hashlib.sha256()
                    while chunk := source.read(1024 * 1024):
                        if time.monotonic() - started > timeout:
                            raise TimeoutError("FFmpeg download timed out. Please retry.")
                        count += len(chunk)
                        if count > MAX_BYTES:
                            raise RuntimeError("FFmpeg archive exceeds the download limit.")
                        digest.update(chunk)
                        output.write(chunk)
                        _report(target, "downloading", bytes=count, total=total)
                if digest.hexdigest() != SHA256:
                    raise RuntimeError("FFmpeg archive failed SHA-256 verification.")
                _report(target, "verifying")
                with zipfile.ZipFile(archive_path) as archive:
                    members = [item for item in archive.infolist() if not item.is_dir() and
                               item.filename.rsplit("/", 1)[-1] == "ffmpeg"]
                    if len(members) != 1 or members[0].file_size > MAX_BYTES or (
                            (members[0].external_attr >> 16) & 0o170000) == 0o120000:
                        raise RuntimeError("FFmpeg archive lacks one regular executable.")
                    # Copy only the selected executable; never extract paths supplied by the ZIP.
                    with archive.open(members[0]) as source, candidate.open("xb") as output:
                        shutil.copyfileobj(source, output)
                candidate.chmod(0o755)
                check_macho(candidate)
                receipt = {"version": VERSION, "url": URL, "archiveSha256": SHA256,
                           "binarySha256": _hash(candidate)}
                candidate.with_name("ffmpeg-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
                if not valid(candidate):
                    raise RuntimeError("Downloaded FFmpeg could not run. Check the macOS launch message and retry.")
                import config
                os.replace(candidate, target)
                config.atomic_write_json(str(target.with_name("ffmpeg-receipt.json")), receipt)
                if not valid(target):
                    raise RuntimeError("Installed FFmpeg failed validation.")
            _report(target, "ready")
            return str(target)
        except Exception as exc:
            _report(target, "error", error=str(exc))
            raise utils.FFmpegResolutionError("Could not prepare macOS FFmpeg: " + str(exc)) from exc
