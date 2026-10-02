# SPDX-License-Identifier: GPL-3.0-only

import os
import hashlib
import io
import json
import shutil
import re
import sys
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from functools import lru_cache
from itertools import product

# Ensure the project root is in the Python path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # type: ignore
from config import (  # type: ignore
    ALLOWED_EXT_VIDEO, ALLOWED_EXT_IMAGE,
    TYPE_VID, TYPE_IMG,
    log_action, IGNORED_FOLDERS
)
from runtime_support import exclusive_file_lock

def get_file_type(file_path):
    """Returns TYPE_VID or TYPE_IMG based on extension, or None if invalid."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    if ext in ALLOWED_EXT_VIDEO:
        return TYPE_VID
    elif ext in ALLOWED_EXT_IMAGE:
        return TYPE_IMG
    return None

def get_target_directory(shot_path, file_type=None, category=None):
    """Return/create the configured category directory inside a shot.

    ``file_type`` keeps the legacy IMG/VID API; callers can pass a category id,
    folder, or ``MediaCategory`` through either argument.
    """
    target_base = config.get_target_base(shot_path)
    resolved = config.resolve_media_category(category, file_type=file_type)
    target_path = os.path.join(target_base, resolved.folder)
    
    if not os.path.exists(target_path):
        os.makedirs(target_path, exist_ok=True)
        log_action(f"Auto-created target subfolder: {target_path}")
    return target_path

@lru_cache(maxsize=32)
def _version_patterns(template):
    # Empty tokens are cleaned exactly like generated names. Cache the small
    # set of patterns instead of rebuilding them for each file on a share.
    fields = list(dict.fromkeys(re.findall(r"\{(\w+)\}", template)))
    optional = [field for field in fields if field != "version"]
    patterns = []
    for present in product((True, False), repeat=len(optional)):
        omitted = {field for field, keep in zip(optional, present) if not keep}
        marked = re.sub(r"\{(\w+)\}", lambda m: "" if m[1] in omitted else "AIRENAMERTOKEN" + m[1] + "END", template)
        marked = clean_filename_base(marked)
        parts = re.split(r"(AIRENAMERTOKEN(?:sequence|scene|shot|type|version|subversion|format)END)", marked)
        seen = set(); regex = ""
        for part in parts:
            if part.startswith("AIRENAMERTOKEN") and part.endswith("END"):
                field = part[len("AIRENAMERTOKEN"):-3]
                if field in seen:
                    regex += "(?P=" + field + ")"
                else:
                    value = r"\d{3,}" if field == "version" else r"\d{2,}" if field == "subversion" else r"[_\-.]?\d+x\d+" if field == "format" else r".+?"
                    regex += "(?P<" + field + ">" + value + ")"
                    seen.add(field)
            else:
                regex += re.escape(part)
        patterns.append(re.compile(regex, re.IGNORECASE))
    return patterns


def parse_version_from_filename(filename: str):
    legacy = re.search(r'_v(\d{3,})(?:_(\d{2,}))?', filename, re.IGNORECASE)
    template = config.FILENAME_TEMPLATE or config.DEFAULT_FILENAME_TEMPLATE
    if "{subversion}" not in template and legacy and legacy.group(2):
        return int(legacy.group(1)), int(legacy.group(2))
    stem = os.path.splitext(os.path.basename(filename))[0]
    for pattern in _version_patterns(config.FILENAME_TEMPLATE or config.DEFAULT_FILENAME_TEMPLATE):
        match = pattern.fullmatch(stem)
        if match:
            return int(match["version"]), int(match.groupdict().get("subversion") or 0)
    # Continue recognizing files created with the former standard template.
    match = re.search(r'_v(\d{3,})(?:_(\d{2,}))?', filename, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)

def _filename_context_matches(filename: str, scene_name=None, shot_name=None, file_type=None, category=None) -> bool:
    base_name = os.path.splitext(os.path.basename(filename))[0].casefold()
    template = (config.FILENAME_TEMPLATE or config.DEFAULT_FILENAME_TEMPLATE).casefold()
    if ("{sequence}" in template or "{scene}" in template) and scene_name:
        if scene_name.casefold() not in base_name:
            return False
    if "{shot}" in template and shot_name:
        if shot_name.casefold() not in base_name:
            return False
    if "{type}" in template and file_type:
        if config.get_type_suffix(file_type, category=category).casefold() not in base_name:
            return False
    return True

def clean_filename_base(base_name: str) -> str:
    base_name = re.sub(r'[_\-.]{2,}', '_', base_name)
    base_name = re.sub(r'[_\-.]+$', '', base_name)
    base_name = re.sub(r'^[_\-.]+', '', base_name)
    return base_name

def format_versioned_base_name(
    scene_name,
    shot_name,
    file_type,
    version,
    subversion=0,
    format_suffix="",
    category=None,
):
    template = config.FILENAME_TEMPLATE or config.DEFAULT_FILENAME_TEMPLATE
    values = {
        "sequence": scene_name,
        "scene": scene_name,
        "shot": shot_name,
        "type": config.get_type_suffix(file_type, category=category),
        "version": f"{version:03d}",
        "subversion": f"{subversion:02d}" if config.SUBVERSION_ENABLED else "",
        "format": format_suffix or "",
    }
    base_name = template
    for key, value in values.items():
        base_name = base_name.replace(f"{{{key}}}", value)
    return clean_filename_base(base_name)

def parse_existing_versions(target_path, scene_name=None, shot_name=None, file_type=None, category=None):
    r"""
    Scans the target directory and returns the highest main_version (`vXXX`)
    and sub_version (`YY`).
    Returns list of dicts: [{'filename': name, 'v': int, 'sub': int}]
    Supports both _v001_00 and _v001 style versions.
    """
    versions = []
    if not os.path.exists(target_path):
        return versions
        
    for filename in os.listdir(target_path):
        if not _filename_context_matches(filename, scene_name, shot_name, file_type, category):
            continue

        parsed = parse_version_from_filename(filename)
        if parsed:
            version, subversion = parsed
            versions.append({
                'filename': filename,
                'v': version,
                'sub': subversion
            })
    return versions

def calculate_new_version(existing_versions, is_subversion=False, target_existing_filename=None):
    """
    Given parsed versions, returns the next (v, sub) tuple.
    If target_existing_filename is provided, strictly increment its subversion.
    If is_subversion is True (Shift pressed), increment 'sub' of highest 'v'.
    If is_subversion is False, increment 'v', set 'sub' to 0.
    """
    if not config.SUBVERSION_ENABLED:
        is_subversion = False
        target_existing_filename = None

    if target_existing_filename:
        # Find the 'v' logic specifically for this target
        parsed = parse_version_from_filename(target_existing_filename)
        if parsed:
            target_v, _ = parsed
            # Get the highest subversion for THIS specific target_v
            subs_for_target_v = [item['sub'] for item in existing_versions if item['v'] == target_v]
            highest_sub = max(subs_for_target_v) if subs_for_target_v else -1
            return target_v, highest_sub + 1

    if not existing_versions:
        return 1, 0
        
    highest_v = max([item['v'] for item in existing_versions])
    
    if is_subversion:
        # Find highest sub for this specific 'v'
        subs_for_highest_v = [item['sub'] for item in existing_versions if item['v'] == highest_v]
        highest_sub = max(subs_for_highest_v) if subs_for_highest_v else -1
        return highest_v, highest_sub + 1
    else:
        # Next generation version
        return highest_v + 1, 0


def version_is_taken(version: int, subversion: int, versions) -> bool:
    return any(item["v"] == version and item["sub"] == subversion for item in versions)


def next_available_version(version: int, subversion: int, versions, target_exists) -> tuple[int, int]:
    """Advance a version tuple until parsed versions and target paths are free."""
    while version_is_taken(version, subversion, versions) or target_exists(version, subversion):
        if config.SUBVERSION_ENABLED:
            subversion += 1
        else:
            version += 1
    return version, subversion


def get_standardized_aspect_ratio(file_path):
    """Calculates aspect ratio and maps it to standard format strings like 16x09."""
    width, height = 0, 0
    file_type = get_file_type(file_path)
    
    try:
        if file_type == TYPE_IMG:
            from PIL import Image # type: ignore
            with Image.open(file_path) as img:
                width, height = img.size
        elif file_type == TYPE_VID:
            width, height = read_video_dimensions(file_path)
    except Exception as e:
        print(f"Error reading media size: {e}")
        return ""
        
    if width == 0 or height == 0:
        return ""
        
    ratio = width / height
    
    standard_ratios = {
        "16x09": 16/9,
        "09x16": 9/16,
        "01x01": 1/1,
        "04x05": 4/5,
        "05x04": 5/4,
        "03x04": 3/4,
        "04x03": 4/3,
        "21x09": 21/9
    }
    
    closest_format = min(standard_ratios.keys(), key=lambda k: abs(standard_ratios[k] - ratio))
    return closest_format

def _preview_payload_from_pillow(image, max_size):
    image = image.convert("RGB")
    image.thumbnail(max_size)
    return image.width, image.height, image.tobytes()

def load_media_preview_rgb(file_path, max_size=(320, 220)):
    """Return a scaled RGB preview payload for a supported image or video."""
    file_type = get_file_type(file_path)
    if not file_type or not os.path.isfile(file_path):
        return None

    try:
        from PIL import Image  # type: ignore

        if file_type == TYPE_IMG:
            with Image.open(file_path) as image:
                return _preview_payload_from_pillow(image, max_size)

        if file_type == TYPE_VID:
            try:
                preview = _load_video_first_frame_rgb(file_path, max_size)
                if preview is not None:
                    return preview
            except Exception as exc:
                log_action(f"Video preview frame unavailable for '{file_path}': {exc}", is_error=True)

            return load_converted_sequence_preview_rgb(file_path, max_size)
    except Exception as exc:
        log_action(f"Preview unavailable for '{file_path}': {exc}", is_error=True)
    return None

def converted_sequence_preview_path(video_path: str) -> str:
    """Return the first generated sequence frame beside a converted video."""
    sequence_dir = find_converted_sequence_folder(video_path)
    if sequence_dir:
        for filename in sorted(os.listdir(sequence_dir)):
            _, ext = os.path.splitext(filename)
            if ext.lower() in ALLOWED_EXT_IMAGE:
                frame_path = os.path.join(sequence_dir, filename)
                if os.path.isfile(frame_path):
                    return frame_path
    return ""

def load_converted_sequence_preview_rgb(video_path: str, max_size=(320, 220)):
    """Fallback preview for converted videos whose original codec is unreadable."""
    preview_path = converted_sequence_preview_path(video_path)
    if not preview_path:
        return None
    try:
        from PIL import Image  # type: ignore
        with Image.open(preview_path) as image:
            return _preview_payload_from_pillow(image, max_size)
    except Exception as exc:
        log_action(f"Converted sequence preview unavailable for '{video_path}': {exc}", is_error=True)
    return None

def sequence_base_name_for_video(source_video: str) -> str:
    """Return the source filename stem used for folder and PNG sequence names."""
    return os.path.splitext(os.path.basename(source_video))[0]


def converted_sequence_folder_candidates(video_path: str) -> tuple[str, str]:
    """Return current and legacy sequence folder paths for a video."""
    base_name = sequence_base_name_for_video(video_path)
    parent_dir = os.path.dirname(video_path)
    return (
        os.path.join(parent_dir, base_name),
        os.path.join(parent_dir, f"{base_name}_sequence"),
    )


def find_converted_sequence_folder(video_path: str) -> str:
    """Return the preferred existing sequence directory, or an empty string."""
    return next(
        (candidate for candidate in converted_sequence_folder_candidates(video_path) if os.path.isdir(candidate)),
        "",
    )

def has_converted_sequence_folder(video_path: str) -> bool:
    """Detect sequence output beside a video using current and legacy names."""
    return bool(find_converted_sequence_folder(video_path))


def resolve_converted_drag_path(path: str) -> str:
    """Drag a converted video's sequence folder, with safe file fallback."""
    if get_file_type(path) == TYPE_VID:
        return find_converted_sequence_folder(path) or path
    return path


def resolve_converted_drag_paths(paths) -> list[str]:
    """Resolve mixed file/folder selections and deduplicate them in input order."""
    resolved_paths = []
    seen = set()
    for path in paths or ():
        resolved = os.path.normpath(resolve_converted_drag_path(str(path)))
        key = os.path.normcase(os.path.abspath(resolved))
        if key not in seen:
            seen.add(key)
            resolved_paths.append(resolved)
    return resolved_paths

FFMPEG_RELEASE_API_URL = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
FFMPEG_ASSET_NAME = "ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_MINIMUM_SIZE = 64 * 1024
FFMPEG_LOCK_SUFFIX = ".install.lock"
EMBEDDED_FFMPEG_SIZE = 204028928
EMBEDDED_FFMPEG_SHA256 = (
    "bae49822b210397dca03f5830205ca655d5ae68b04b1e4ee1bf79b27b9eff40a"
)
EMBEDDED_FFMPEG_VERSION_TOKEN = "N-124616-g3baab604db-20260524"
EMBEDDED_FFMPEG_DIR = os.path.join("embedded_tools", "ffmpeg")
EMBEDDED_FFMPEG_MANIFEST = "manifest.json"
EMBEDDED_FFMPEG_SUPPORT_FILES = (
    EMBEDDED_FFMPEG_MANIFEST,
    "LICENSE.GPLv3.txt",
    "NOTICE.txt",
    "provenance.txt",
)
LOCAL_FFMPEG_SUPPORT_NAMES = {
    EMBEDDED_FFMPEG_MANIFEST: "ffmpeg.manifest.json",
    "LICENSE.GPLv3.txt": "ffmpeg.LICENSE.GPLv3.txt",
    "NOTICE.txt": "ffmpeg.NOTICE.txt",
    "provenance.txt": "ffmpeg.provenance.txt",
}
_FFMPEG_FAILURES: dict[str, str] = {}
_FFMPEG_STATE_LOCK = threading.Lock()
_WINDOWS_DLL_DIRECTORY_LOCK = threading.Lock()


class FFmpegResolutionError(RuntimeError):
    """Raised when the local, verified FFmpeg executable cannot be resolved."""


def sanitized_subprocess_environment(environ=None) -> dict[str, str]:
    """Return an environment that cannot leak frozen Python/Qt paths to tools."""
    env = dict(os.environ if environ is None else environ)
    for key in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_PLATFORM",
        "PYTHONHOME",
        "PYTHONPATH",
        "_MEIPASS2",
    ):
        env.pop(key, None)
    frozen_roots = []
    for value in (getattr(sys, "_MEIPASS", ""), env.get("_MEIPASS", "")):
        if value:
            frozen_roots.append(os.path.normcase(os.path.abspath(value)))
    env.pop("_MEIPASS", None)
    if frozen_roots and env.get("PATH"):
        clean_entries = []
        for entry in env["PATH"].split(os.pathsep):
            normalized = os.path.normcase(os.path.abspath(entry or "."))
            if not any(normalized == root or normalized.startswith(root + os.sep) for root in frozen_roots):
                clean_entries.append(entry)
        env["PATH"] = os.pathsep.join(clean_entries)
    return env


@contextmanager
def _reset_windows_dll_directory():
    """Prevent frozen-app DLL search state from leaking into external tools."""
    if os.name != "nt":
        yield
        return
    import ctypes

    with _WINDOWS_DLL_DIRECTORY_LOCK:
        kernel32 = ctypes.windll.kernel32
        previous = ""
        try:
            length = kernel32.GetDllDirectoryW(0, None)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                kernel32.GetDllDirectoryW(len(buffer), buffer)
                previous = buffer.value
            kernel32.SetDllDirectoryW(None)
            yield
        finally:
            kernel32.SetDllDirectoryW(previous or None)


def _popen_compat(cmd, **kwargs):
    """Allow simple legacy test doubles while production always receives env."""
    with _reset_windows_dll_directory():
        try:
            return subprocess.Popen(cmd, **kwargs)
        except TypeError as exc:
            if "env" not in str(exc):
                raise
            kwargs.pop("env", None)
            return subprocess.Popen(cmd, **kwargs)


def _run_compat(cmd, **kwargs):
    with _reset_windows_dll_directory():
        try:
            return subprocess.run(cmd, **kwargs)
        except TypeError as exc:
            if "env" not in str(exc):
                raise
            kwargs.pop("env", None)
            return subprocess.run(cmd, **kwargs)


FFMPEG_METADATA_TIMEOUT_SECONDS = 30
_SHOWINFO_SIZE_RE = re.compile(
    r"\bn:\s*\d+.*?\bs:\s*(\d{1,6})x(\d{1,6})\b",
    re.IGNORECASE,
)
_SHOWINFO_RATE_RE = re.compile(
    r"\bframe_rate:\s*(\d+)\s*/\s*(\d+)\b",
    re.IGNORECASE,
)
_STREAM_SIZE_RE = re.compile(r"(?<![\w])(\d{1,6})x(\d{1,6})(?=[\s,\[])")
_STREAM_FPS_RE = re.compile(r",\s*(\d+(?:\.\d+)?)\s+fps\b", re.IGNORECASE)
_PROGRESS_FRAME_RE = re.compile(r"(?m)^frame\s*=\s*(\d+)\s*$")


def _run_media_ffmpeg(arguments, *, text=True, timeout=None):
    """Run the verified local FFmpeg with isolated frozen-app state.

    These synchronous helpers are called by the existing background media
    workers. ``resolve_ffmpeg()`` restores the embedded copy when needed and
    remains fully offline in frozen production.
    """
    ffmpeg_path = resolve_ffmpeg()
    creationflags = 0x08000000 if os.name == "nt" else 0
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
        "check": False,
        "creationflags": creationflags,
        "env": sanitized_subprocess_environment(),
    }
    if text:
        kwargs["errors"] = "replace"
    if timeout is not None:
        kwargs["timeout"] = timeout
    return _run_compat(
        [ffmpeg_path, "-hide_banner", "-nostdin", *arguments],
        **kwargs,
    )


def _completed_output_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _ffmpeg_failure_message(completed) -> str:
    message = _completed_output_text(getattr(completed, "stderr", None)).strip()
    if not message:
        message = _completed_output_text(getattr(completed, "stdout", None)).strip()
    return message[-2000:] if message else "FFmpeg returned no diagnostic output."


def _read_video_stream_info(video_path: str) -> tuple[int, int, float]:
    """Decode one frame and return its dimensions and reported frame rate."""
    completed = _run_media_ffmpeg(
        [
            "-loglevel",
            "info",
            "-i",
            video_path,
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            "showinfo",
            "-f",
            "null",
            "-",
        ],
        timeout=FFMPEG_METADATA_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not read video stream information: {_ffmpeg_failure_message(completed)}"
        )

    diagnostics = _completed_output_text(completed.stderr)
    size_match = _SHOWINFO_SIZE_RE.search(diagnostics)
    width = int(size_match.group(1)) if size_match else 0
    height = int(size_match.group(2)) if size_match else 0

    fps = 0.0
    for numerator_text, denominator_text in _SHOWINFO_RATE_RE.findall(diagnostics):
        numerator = int(numerator_text)
        denominator = int(denominator_text)
        if numerator > 0 and denominator > 0:
            fps = numerator / denominator
            break

    # Retain a conservative fallback for codecs that omit showinfo's config
    # line while still exposing valid stream metadata in FFmpeg diagnostics.
    if not width or not height or fps <= 0:
        for line in diagnostics.splitlines():
            if "Video:" not in line:
                continue
            if not width or not height:
                stream_size = _STREAM_SIZE_RE.search(line)
                if stream_size:
                    width, height = map(int, stream_size.groups())
            if fps <= 0:
                stream_fps = _STREAM_FPS_RE.search(line)
                if stream_fps:
                    fps = float(stream_fps.group(1))
            if width > 0 and height > 0 and fps > 0:
                break

    return width, height, fps


def read_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return decoded video dimensions using the bundled FFmpeg."""
    width, height, _ = _read_video_stream_info(video_path)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Video dimensions are unavailable: {video_path}")
    return width, height


def _load_video_first_frame_rgb(video_path: str, max_size=(320, 220)):
    """Decode one video frame as PNG and convert it to the preview contract."""
    completed = _run_media_ffmpeg(
        [
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        text=False,
        timeout=FFMPEG_METADATA_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not decode video preview: {_ffmpeg_failure_message(completed)}"
        )
    payload = completed.stdout or b""
    if not payload:
        raise RuntimeError("FFmpeg decoded no video preview frame.")

    from PIL import Image  # type: ignore

    with Image.open(io.BytesIO(payload)) as image:
        return _preview_payload_from_pillow(image, max_size)


def _run_ffmpeg_version(path: str):
    creationflags = 0x08000000 if os.name == "nt" else 0
    return _run_compat(
        [path, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=10,
        check=False,
        creationflags=creationflags,
        env=sanitized_subprocess_environment(),
    )


def validate_ffmpeg_executable(path: str, *, minimum_size: int = FFMPEG_MINIMUM_SIZE) -> bool:
    """Validate file presence/size and execute ``ffmpeg -version`` safely."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) < minimum_size:
            return False
        completed = _run_ffmpeg_version(path)
        output = completed.stdout or ""
        return completed.returncode == 0 and "ffmpeg version" in output.casefold()
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _sha256_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def validate_pinned_ffmpeg_executable(path: str) -> bool:
    """Require the exact embedded FFmpeg bytes and pinned reported version."""
    try:
        if (
            not path
            or not os.path.isfile(path)
            or os.path.getsize(path) != EMBEDDED_FFMPEG_SIZE
            or _sha256_path(path).casefold() != EMBEDDED_FFMPEG_SHA256
        ):
            return False
        completed = _run_ffmpeg_version(path)
        output = completed.stdout or ""
        return (
            completed.returncode == 0
            and "ffmpeg version" in output.casefold()
            and EMBEDDED_FFMPEG_VERSION_TOKEN.casefold() in output.casefold()
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _is_frozen_application() -> bool:
    return bool(getattr(sys, "frozen", False))


def _verified_embedded_ffmpeg(
    embedded_root: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return the exact embedded FFmpeg and support files after byte verification."""
    if embedded_root is None:
        if not _is_frozen_application():
            raise FFmpegResolutionError("Embedded FFmpeg is only available in a frozen build.")
        embedded_root = str(getattr(sys, "_MEIPASS", "") or "")
    if not embedded_root:
        raise FFmpegResolutionError("Frozen MediaRenamer has no extraction root for embedded FFmpeg.")
    asset_dir = os.path.join(os.path.abspath(embedded_root), EMBEDDED_FFMPEG_DIR)
    source = os.path.join(asset_dir, "ffmpeg.exe")
    support = {
        name: os.path.join(asset_dir, name)
        for name in EMBEDDED_FFMPEG_SUPPORT_FILES
    }
    missing = [
        path
        for path in (source, *support.values())
        if not os.path.isfile(path)
    ]
    if missing:
        raise FFmpegResolutionError(
            "Standalone package is missing embedded FFmpeg asset: " + missing[0]
        )
    try:
        with open(support[EMBEDDED_FFMPEG_MANIFEST], "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise FFmpegResolutionError(f"Embedded FFmpeg manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise FFmpegResolutionError("Embedded FFmpeg manifest must be a JSON object.")
    expected_manifest = {
        "version": EMBEDDED_FFMPEG_VERSION_TOKEN,
        "license": "GPL-3.0-or-later",
        "license_file": "LICENSE.GPLv3.txt",
        "notice_file": "NOTICE.txt",
        "size": EMBEDDED_FFMPEG_SIZE,
        "sha256": EMBEDDED_FFMPEG_SHA256,
        "embedded_relative_path": "embedded_tools/ffmpeg/ffmpeg.exe",
    }
    for key, expected in expected_manifest.items():
        actual = manifest.get(key)
        matches = (
            isinstance(actual, str)
            and isinstance(expected, str)
            and actual.casefold() == expected.casefold()
        ) if isinstance(expected, str) else actual == expected
        if not matches:
            raise FFmpegResolutionError(
                f"Embedded FFmpeg manifest field {key!r} is invalid."
            )
    actual_size = os.path.getsize(source)
    if actual_size != EMBEDDED_FFMPEG_SIZE:
        raise FFmpegResolutionError(
            f"Embedded FFmpeg size mismatch (expected {EMBEDDED_FFMPEG_SIZE}, "
            f"got {actual_size})."
        )
    actual_hash = _sha256_path(source)
    if actual_hash.casefold() != EMBEDDED_FFMPEG_SHA256:
        raise FFmpegResolutionError(
            "Embedded FFmpeg SHA-256 mismatch "
            f"(expected {EMBEDDED_FFMPEG_SHA256}, got {actual_hash})."
        )
    return source, support


def _copy_file_fsync(source: str, destination: str) -> None:
    with open(source, "rb") as input_file, open(destination, "xb") as output:
        shutil.copyfileobj(input_file, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _install_embedded_ffmpeg(
    target_path: str,
    *,
    embedded_root: str | None = None,
) -> str:
    """Atomically materialize the bundled tool and notices into local Tools."""
    source, support = _verified_embedded_ffmpeg(embedded_root)
    target_dir = os.path.dirname(target_path)
    temp_dir = tempfile.mkdtemp(prefix=".ffmpeg-install-", dir=target_dir)
    try:
        candidate = os.path.join(temp_dir, "ffmpeg.exe")
        _copy_file_fsync(source, candidate)
        if (
            os.path.getsize(candidate) != EMBEDDED_FFMPEG_SIZE
            or _sha256_path(candidate).casefold() != EMBEDDED_FFMPEG_SHA256
        ):
            raise FFmpegResolutionError("Copied embedded FFmpeg failed byte verification.")

        staged_support: list[tuple[str, str]] = []
        for embedded_name, local_name in LOCAL_FFMPEG_SUPPORT_NAMES.items():
            staged = os.path.join(temp_dir, local_name)
            _copy_file_fsync(support[embedded_name], staged)
            staged_support.append((staged, os.path.join(target_dir, local_name)))

        # Support/license data lands first; ffmpeg.exe is the commit point.
        for staged, destination in staged_support:
            os.replace(staged, destination)
        os.replace(candidate, target_path)
        if not validate_pinned_ffmpeg_executable(target_path):
            try:
                os.unlink(target_path)
            except OSError:
                pass
            raise FFmpegResolutionError(
                "Installed embedded ffmpeg.exe failed version validation."
            )
        return target_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _request(url: str):
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MediaRenamer FFmpeg resolver",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_url_bytes(url: str, *, timeout: float = 30.0, limit: int | None = None) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=timeout) as response:
        data = response.read() if limit is None else response.read(limit + 1)
    if limit is not None and len(data) > limit:
        raise FFmpegResolutionError(f"Remote response exceeds the {limit}-byte safety limit.")
    return data


def _load_release_metadata(url: str = FFMPEG_RELEASE_API_URL, *, timeout: float = 30.0) -> dict:
    try:
        payload = json.loads(_read_url_bytes(url, timeout=timeout, limit=5 * 1024 * 1024).decode("utf-8"))
    except Exception as exc:
        raise FFmpegResolutionError(f"Could not read BtbN release metadata: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        raise FFmpegResolutionError("BtbN release metadata did not contain an assets list.")
    return payload


def _select_ffmpeg_asset(metadata: dict, asset_name: str = FFMPEG_ASSET_NAME) -> dict:
    assets = [item for item in metadata.get("assets", []) if isinstance(item, dict)]
    for item in assets:
        if str(item.get("name", "")).casefold() == asset_name.casefold():
            if item.get("browser_download_url"):
                return item
    candidates = [
        item for item in assets
        if str(item.get("name", "")).casefold().endswith("win64-gpl.zip")
        and "shared" not in str(item.get("name", "")).casefold()
        and item.get("browser_download_url")
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise FFmpegResolutionError(f"BtbN release does not contain the expected asset '{asset_name}'.")


def _digest_from_asset(asset: dict) -> str:
    digest = str(asset.get("digest", "") or "").strip().casefold()
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
    return match.group(1) if match else ""


def _checksum_from_release(metadata: dict, asset: dict, *, timeout: float = 30.0) -> str:
    direct = _digest_from_asset(asset)
    if direct:
        return direct
    asset_name = str(asset.get("name", ""))
    checksum_assets = []
    for item in metadata.get("assets", []):
        if not isinstance(item, dict) or not item.get("browser_download_url"):
            continue
        name = str(item.get("name", "")).casefold()
        if "sha256" in name or "checksum" in name:
            checksum_assets.append(item)
    for checksum_asset in checksum_assets:
        try:
            text = _read_url_bytes(
                str(checksum_asset["browser_download_url"]),
                timeout=timeout,
                limit=10 * 1024 * 1024,
            ).decode("utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            if asset_name.casefold() not in line.casefold():
                continue
            match = re.search(r"(?i)\b([0-9a-f]{64})\b", line)
            if match:
                return match.group(1).casefold()
    raise FFmpegResolutionError(f"No published SHA-256 was found for '{asset_name}'.")


def _download_verified(url: str, destination: str, expected_sha256: str, *, timeout: float = 120.0):
    digest = hashlib.sha256()
    with urllib.request.urlopen(_request(url), timeout=timeout) as response, open(destination, "xb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    actual = digest.hexdigest().casefold()
    if actual != expected_sha256.casefold():
        raise FFmpegResolutionError(
            f"FFmpeg archive SHA-256 mismatch (expected {expected_sha256}, got {actual})."
        )


def _extract_ffmpeg(archive_path: str, destination: str):
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = [
            member for member in archive.infolist()
            if not member.is_dir() and os.path.basename(member.filename).casefold() == "ffmpeg.exe"
        ]
        if len(members) != 1:
            raise FFmpegResolutionError("Downloaded archive must contain exactly one ffmpeg.exe.")
        with archive.open(members[0], "r") as source, open(destination, "xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())


def reset_ffmpeg_resolution_state():
    """Clear failed-attempt memoization for an explicit Retry or a test."""
    with _FFMPEG_STATE_LOCK:
        _FFMPEG_FAILURES.clear()


def resolve_ffmpeg(
    *,
    tools_dir: str | None = None,
    target_path: str | None = None,
    metadata_url: str = FFMPEG_RELEASE_API_URL,
    timeout: float = 120.0,
    embedded_root: str | None = None,
    allow_frozen_download: bool = False,
) -> str:
    """Return a valid local FFmpeg, provisioning it only when needed.

    Frozen builds are strictly offline: if the verified embedded payload cannot
    be installed, the operation fails without attempting any network access.
    Browser's standalone host may opt into the verified BtbN download.
    """
    tools_dir = tools_dir or config.TOOLS_DIR
    target_path = target_path or (os.path.join(tools_dir, "ffmpeg.exe") if tools_dir else "")
    if not target_path:
        raise FFmpegResolutionError("The local MediaRenamer Tools directory is not configured.")
    target_path = os.path.abspath(target_path)
    failure_key = os.path.normcase(target_path)
    use_embedded = embedded_root is not None or (
        _is_frozen_application() and not allow_frozen_download
    )
    local_validator = (
        validate_pinned_ffmpeg_executable
        if use_embedded
        else validate_ffmpeg_executable
    )
    if local_validator(target_path):
        return target_path
    with _FFMPEG_STATE_LOCK:
        cached_failure = _FFMPEG_FAILURES.get(failure_key)
    if cached_failure:
        raise FFmpegResolutionError(f"Previous FFmpeg setup attempt failed this session: {cached_failure}")

    lock_path = f"{target_path}{FFMPEG_LOCK_SUFFIX}"
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with exclusive_file_lock(lock_path, timeout=timeout, stale_seconds=max(600.0, timeout * 2)):
            if local_validator(target_path):
                return target_path
            with _FFMPEG_STATE_LOCK:
                cached_failure = _FFMPEG_FAILURES.get(failure_key)
            if cached_failure:
                raise FFmpegResolutionError(f"Previous FFmpeg setup attempt failed this session: {cached_failure}")

            if use_embedded:
                log_action("Installing verified embedded FFmpeg to the local Tools folder...")
                installed = _install_embedded_ffmpeg(
                    target_path,
                    embedded_root=embedded_root,
                )
                log_action(f"Embedded FFmpeg installed successfully: {installed}")
                return installed

            metadata = _load_release_metadata(metadata_url, timeout=min(timeout, 30.0))
            asset = _select_ffmpeg_asset(metadata)
            expected_digest = _checksum_from_release(metadata, asset, timeout=min(timeout, 30.0))
            temp_dir = tempfile.mkdtemp(prefix=".ffmpeg-install-", dir=os.path.dirname(target_path))
            try:
                archive_path = os.path.join(temp_dir, "ffmpeg.zip")
                candidate_path = os.path.join(temp_dir, "ffmpeg.exe")
                log_action("Downloading verified portable FFmpeg to the local Tools folder...")
                _download_verified(
                    str(asset["browser_download_url"]),
                    archive_path,
                    expected_digest,
                    timeout=timeout,
                )
                _extract_ffmpeg(archive_path, candidate_path)
                if not validate_ffmpeg_executable(candidate_path):
                    raise FFmpegResolutionError("Extracted ffmpeg.exe failed size/version validation.")
                os.replace(candidate_path, target_path)
                if not validate_ffmpeg_executable(target_path):
                    raise FFmpegResolutionError("Installed ffmpeg.exe failed post-install validation.")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
            log_action(f"FFmpeg installed successfully: {target_path}")
            return target_path
    except Exception as exc:
        message = str(exc)
        with _FFMPEG_STATE_LOCK:
            _FFMPEG_FAILURES.setdefault(failure_key, message)
        if isinstance(exc, FFmpegResolutionError):
            raise
        raise FFmpegResolutionError(f"Could not install local FFmpeg: {message}") from exc


def ensure_ffmpeg() -> bool:
    """Backward-compatible boolean wrapper around :func:`resolve_ffmpeg`."""
    try:
        resolve_ffmpeg()
        return True
    except Exception as exc:
        log_action(f"Failed to resolve FFmpeg: {exc}", is_error=True)
        return False


def run_ffmpeg_process(cmd, cancel_check=None, process_started=None):
    """Run FFmpeg without pipe backpressure while keeping cancellation responsive."""
    creationflags = 0x08000000 if os.name == 'nt' else 0
    cancelled = False

    # FFmpeg logs status to stderr. A PIPE can fill while a worker only polls
    # the process, so keep stderr in a temp file and read it only on completion.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
        process = _popen_compat(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            text=True,
            creationflags=creationflags,
            env=sanitized_subprocess_environment(),
        )
        if process_started:
            process_started(process)

        try:
            while process.poll() is None:
                if cancel_check and cancel_check():
                    cancelled = True
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    break
                time.sleep(0.1)

            process.communicate()
            stderr_file.flush()
            stderr_file.seek(0)
            stderr = stderr_file.read()
            return process.returncode, stderr, cancelled
        finally:
            if process_started:
                process_started(None)


def convert_to_sequence(source_video, target_dir, base_name, cancel_check=None, process_started=None, ffmpeg_path=None):
    """Uses FFMPEG to convert a video into a lossless PNG sequence."""
    ffmpeg_path = ffmpeg_path or resolve_ffmpeg()
        
    sequence_dir = os.path.join(target_dir, base_name)
    if os.path.exists(sequence_dir):
        raise FileExistsError(f"Sequence target already exists: {sequence_dir}")

    os.makedirs(sequence_dir)
    log_action(f"Created sequence wrapper folder: {sequence_dir}")
    
    # %05d padding covers 99999 frames
    output_pattern = os.path.join(sequence_dir, f"{base_name}_%05d.png")
    
    log_action(f"Starting FFMPEG conversion: {source_video} -> {sequence_dir}")
    
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        source_video,
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "png",
        "-y",
        output_pattern
    ]

    returncode, stderr, cancelled = run_ffmpeg_process(
        cmd,
        cancel_check=cancel_check,
        process_started=process_started,
    )

    if cancelled or (returncode != 0 and cancel_check and cancel_check()):
        shutil.rmtree(sequence_dir, ignore_errors=True)
        log_action(f"FFMPEG conversion cancelled: {source_video}", is_error=True)
        raise RuntimeError("Sequence conversion cancelled.")
    
    if returncode != 0:
        log_action(f"FFMPEG Error: {stderr}", is_error=True)
        shutil.rmtree(sequence_dir, ignore_errors=True)
        raise Exception(f"FFMPEG failed with code {returncode}")
        
    log_action(f"Sequence created successfully in {sequence_dir}")
    undo_op = ('folder_create', sequence_dir)
    return sequence_dir, base_name, undo_op


def count_decoded_video_frames(video_path: str) -> int:
    """Count readable video frames by decoding them with bundled FFmpeg."""
    completed = _run_media_ffmpeg(
        [
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-fps_mode",
            "passthrough",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "null",
            "-",
        ],
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not read video frames: {_ffmpeg_failure_message(completed)}"
        )
    matches = _PROGRESS_FRAME_RE.findall(_completed_output_text(completed.stdout))
    frame_count = int(matches[-1]) if matches else 0
    if frame_count <= 0:
        raise RuntimeError(f"Video contains no readable frames: {video_path}")
    return frame_count


def read_video_fps(video_path: str) -> float:
    """Read the decoder-reported video frame rate using bundled FFmpeg."""
    _, _, fps = _read_video_stream_info(video_path)
    if fps <= 0:
        raise RuntimeError(f"Video FPS is unavailable: {video_path}")
    return fps


def build_retime_mp4_command(source_video: str, output_path: str, ffmpeg_path: str | None = None) -> list[str]:
    """Build an exact-CFR 24fps FFmpeg command that keeps decoded frame order."""
    return [
        ffmpeg_path or config.FFMPEG_PATH,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        source_video,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        "setpts=N/(24*TB)",
        "-r",
        "24",
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-video_track_timescale",
        "24000",
        "-movflags",
        "+faststart",
        "-y",
        output_path,
    ]


def retime_mp4_to_24fps(
    source_video: str,
    target_dir: str,
    scene_name: str,
    shot_name: str,
    cancel_check=None,
    process_started=None,
):
    """Create the next MP4 video version with the same frames timed at 24fps."""
    if os.path.splitext(source_video)[1].lower() != ".mp4":
        raise ValueError("24fps retime supports MP4 source files only.")
    if not os.path.isfile(source_video):
        raise FileNotFoundError(f"Source file not found: {source_video}")
    ffmpeg_path = resolve_ffmpeg()

    base_name, next_v, next_sub = build_versioned_base_name(
        scene_name,
        shot_name,
        TYPE_VID,
        target_dir,
        is_subversion=False,
    )

    def retime_target_exists(version, subversion):
        name = format_versioned_base_name(scene_name, shot_name, TYPE_VID, version, subversion)
        return os.path.exists(os.path.join(target_dir, f"{name}.mp4"))

    next_v, next_sub = next_available_version(next_v, next_sub, [], retime_target_exists)
    base_name = format_versioned_base_name(scene_name, shot_name, TYPE_VID, next_v, next_sub)
    new_filename = f"{base_name}.mp4"
    new_filepath = os.path.join(target_dir, new_filename)
    partial_path = os.path.join(target_dir, f".{base_name}.retime.{os.getpid()}.mp4")

    source_frames = count_decoded_video_frames(source_video)
    cmd = build_retime_mp4_command(source_video, partial_path, ffmpeg_path=ffmpeg_path)
    returncode, stderr, cancelled = run_ffmpeg_process(
        cmd,
        cancel_check=cancel_check,
        process_started=process_started,
    )

    if cancelled or (returncode != 0 and cancel_check and cancel_check()):
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        log_action(f"MP4 24fps conversion cancelled: {source_video}", is_error=True)
        raise RuntimeError("MP4 24fps conversion cancelled.")

    if returncode != 0:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        log_action(f"FFMPEG 24fps Error: {stderr}", is_error=True)
        raise Exception(f"FFMPEG 24fps conversion failed with code {returncode}")

    try:
        output_frames = count_decoded_video_frames(partial_path)
    except Exception:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise

    if output_frames != source_frames:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"24fps frame count mismatch: source {source_frames}, output {output_frames}."
        )

    output_fps = read_video_fps(partial_path)
    if abs(output_fps - 24.0) > 0.01:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(f"24fps output FPS mismatch: got {output_fps:.3f}.")

    os.replace(partial_path, new_filepath)
    log_action(
        f"RETIMED MP4 TO 24FPS: '{os.path.basename(source_video)}' TO '{new_filepath}' "
        f"WITH {output_frames} FRAMES"
    )
    undo_op = ('copy', new_filepath)
    return new_filepath, new_filename, undo_op


def build_versioned_base_name(scene_name, shot_name, file_type, target_path,
                              is_subversion=False, target_existing_filename=None, format_suffix="",
                              category=None):
    """Builds the next versioned base name without an extension."""
    existing_versions = parse_existing_versions(target_path, scene_name, shot_name, file_type, category)
    next_v, next_sub = calculate_new_version(existing_versions, is_subversion, target_existing_filename)
    base_name = format_versioned_base_name(
        scene_name, shot_name, file_type, next_v, next_sub, format_suffix, category
    )
    return base_name, next_v, next_sub


def build_sequence_base_name(
    scene_name, shot_name, target_path, is_subversion=False,
    target_existing_filename=None, category=None,
):
    """Returns a versioned base name for PNG sequence folders."""
    base_name, _, _ = build_versioned_base_name(
        scene_name,
        shot_name,
        TYPE_VID,
        target_path,
        is_subversion=is_subversion,
        target_existing_filename=target_existing_filename,
        category=category,
    )
    return base_name


def downscale_image(source_file, target_dir, scene_name, shot_name, file_type, category=None):
    """Downscales an image by 50% and saves it as a new subversion."""
    from PIL import Image # type: ignore
    
    if not os.path.exists(source_file):
        raise FileNotFoundError(f"Source file not found: {source_file}")
        
    _, ext = os.path.splitext(source_file)
    ext = ext.lower()
    
    existing_versions = parse_existing_versions(target_dir, scene_name, shot_name, file_type, category)
    next_v, next_sub = calculate_new_version(
        existing_versions, 
        is_subversion=config.SUBVERSION_ENABLED,
        target_existing_filename=os.path.basename(source_file)
    )
    
    base_name = format_versioned_base_name(
        scene_name, shot_name, file_type, next_v, next_sub, category=category
    )
    new_filename = f"{base_name}{ext}"
    new_filepath = os.path.join(target_dir, new_filename)
    
    def downscale_target_exists(version, subversion):
        name = format_versioned_base_name(
            scene_name, shot_name, file_type, version, subversion, category=category
        )
        return os.path.exists(os.path.join(target_dir, f"{name}{ext}"))

    next_v, next_sub = next_available_version(next_v, next_sub, [], downscale_target_exists)
    base_name = format_versioned_base_name(
        scene_name, shot_name, file_type, next_v, next_sub, category=category
    )
    new_filename = f"{base_name}{ext}"
    new_filepath = os.path.join(target_dir, new_filename)
        
    try:
        with Image.open(source_file) as img:
            new_width = max(1, int(img.width * 0.5))
            new_height = max(1, int(img.height * 0.5))
            downscaled = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            downscaled.save(new_filepath)
            
        log_action(f"DOWNSCALED 50%: '{os.path.basename(source_file)}' TO '{new_filepath}'")
        undo_op = ('copy', new_filepath)
        return new_filepath, new_filename, undo_op
    except Exception as e:
        log_action(f"Failed to downscale image: {e}", is_error=True)
        raise e

@contextmanager
def _copy_target_lock(target_path: str, timeout: float = 120.0):
    """Hold one OS file lock while selecting a version and copying its bytes."""
    normalized = os.path.normcase(os.path.realpath(target_path))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    lock_root = os.path.join(config.STATE_DIR, "CopyLocks")
    os.makedirs(lock_root, exist_ok=True)
    lock_path = os.path.join(lock_root, f"{digest}.lock")
    with open(lock_path, "a+b", buffering=0) as handle:
        started = time.monotonic()
        # Another process may lock byte zero while a new file is being
        # initialized. Retry the write under the same timeout as lock entry.
        while True:
            handle.seek(0, os.SEEK_END)
            if handle.tell() != 0:
                break
            try:
                handle.write(b"0")
                break
            except PermissionError:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError(f"Timed out initializing copy lock: {target_path}")
                time.sleep(0.05)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError(f"Timed out waiting for copy lock: {target_path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError(f"Timed out waiting for copy lock: {target_path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def synchronized_versioned_copy(func):
    from functools import wraps

    @wraps(func)
    def locked(source_file, target_path, *args, **kwargs):
        with _copy_target_lock(target_path):
            return func(source_file, target_path, *args, **kwargs)

    return locked


@synchronized_versioned_copy
def copy_and_rename_file(source_file, target_path, scene_name, shot_name, file_type,
                         is_subversion=False, target_existing_filename=None, add_format=False,
                         create_sequence=False, cancel_check=None, process_started=None,
                         category=None):
    """Core logic to figure out next name and copy the file."""
    if not os.path.exists(source_file):
        raise FileNotFoundError(f"Source file not found: {source_file}")
        
    _, ext = os.path.splitext(source_file)
    ext = ext.lower()
    
    format_suffix = ""
    if add_format:
        fmt = get_standardized_aspect_ratio(source_file)
        if fmt:
            format_suffix = f"_{fmt}"

    base_name, next_v, next_sub = build_versioned_base_name(
        scene_name,
        shot_name,
        file_type,
        target_path,
        is_subversion=is_subversion,
        target_existing_filename=target_existing_filename,
        format_suffix=format_suffix,
        category=category,
    )
    new_filename = f"{base_name}{ext}"
    new_filepath = os.path.join(target_path, new_filename)
    
    # To be extremely safe, check if file exists despite our highest check
    def copy_target_exists(version, subversion):
        name = format_versioned_base_name(
            scene_name, shot_name, file_type, version, subversion, format_suffix, category
        )
        return os.path.exists(os.path.join(target_path, f"{name}{ext}"))

    next_v, next_sub = next_available_version(next_v, next_sub, [], copy_target_exists)
    base_name = format_versioned_base_name(
        scene_name, shot_name, file_type, next_v, next_sub, format_suffix, category
    )
    new_filename = f"{base_name}{ext}"
    new_filepath = os.path.join(target_path, new_filename)
    
    try:
        resolved_category = config.resolve_media_category(category, file_type=file_type)
        if create_sequence and resolved_category.media_type == config.MEDIA_TYPE_VIDEO:
            shutil.copy2(source_file, new_filepath)
            log_action(f"COPIED VIDEO FOR SEQUENCE: '{os.path.basename(source_file)}' TO '{new_filepath}'")
            video_undo = ('copy', new_filepath)
            
            seq_dir, seq_name, seq_undo = convert_to_sequence(
                new_filepath,
                target_path,
                base_name,
                cancel_check=cancel_check,
                process_started=process_started,
            )
            return seq_dir, seq_name, [video_undo, seq_undo]
        else:
            shutil.copy2(source_file, new_filepath)
            log_action(f"COPIED: '{os.path.basename(source_file)}' TO '{new_filepath}'")
            undo_op = ('copy', new_filepath)
            return new_filepath, new_filename, undo_op
    except Exception as e:
        log_action(f"Failed to process file: {e}", is_error=True)
        raise e

def rename_target_files(
    target_dir, scene_name, shot_name, single_filename=None, add_format=False, category=None
):
    """
    Renames files in target_dir that don't match the convention.
    If single_filename is provided, it only renames that specific file (if it exists).
    Otherwise, it finds all non-conforming files, sorts by modification date, and renames them.
    Returns a tuple: (list of messages, list of undo_operations).
    """
    if not os.path.exists(target_dir):
        return ["Directory does not exist."], []
        
    all_files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
    
    resolved_category = config.resolve_media_category(
        category,
        target_dir=target_dir,
        file_type=TYPE_VID if os.path.basename(target_dir) == config.DIR_VIDEO else TYPE_IMG,
    )
    file_type = resolved_category.file_type
    non_conforming_files = []

    for f in all_files:
        if parse_version_from_filename(f) and _filename_context_matches(
            f, scene_name, shot_name, file_type, resolved_category
        ):
            continue
        non_conforming_files.append(f)
            
    files_to_rename = []
    if single_filename:
        if single_filename in all_files:
            files_to_rename.append(single_filename)
        else:
            return [f"File {single_filename} not found."], []
    else:
        files_to_rename = non_conforming_files
        
    if not files_to_rename:
        return ["No files need renaming."], []
        
    # Sort files_to_rename by modification date (FIFO)
    files_to_rename.sort(key=lambda x: os.path.getmtime(os.path.join(target_dir, x)))
    
    msgs = []
    undo_ops = []
    
    for f in files_to_rename:
        source_path = os.path.join(target_dir, f)
        
        existing_versions = parse_existing_versions(
            target_dir, scene_name, shot_name, file_type, resolved_category
        )
        
        # Check if the badly named file already contains a version string
        parsed_version = parse_version_from_filename(f)
        if parsed_version:
            base_v, base_sub = parsed_version
            
            next_v, next_sub = next_available_version(
                base_v,
                base_sub,
                existing_versions,
                lambda version, subversion: False,
            )
        else:
            # Fallback for completely unversioned files
            next_v, next_sub = calculate_new_version(existing_versions, is_subversion=False)
        
        _, ext = os.path.splitext(source_path)
        ext = ext.lower()
        
        # Attempt to preserve existing format if present
        format_suffix = ""
        standard_ratios = ["16x09", "09x16", "01x01", "04x05", "05x04", "03x04", "04x03", "21x09"]
        for ratio in standard_ratios:
            if f"_{ratio}" in f:
                format_suffix = f"_{ratio}"
                break
                
        # Override if explicitly requested
        if add_format:
            fmt = get_standardized_aspect_ratio(source_path)
            if fmt:
                format_suffix = f"_{fmt}"
                
        new_base_name = format_versioned_base_name(
            scene_name, shot_name, file_type, next_v, next_sub, format_suffix, resolved_category
        )
        new_filename = f"{new_base_name}{ext}"
        new_filepath = os.path.join(target_dir, new_filename)
        
        def rename_target_exists(version, subversion):
            base = format_versioned_base_name(
                scene_name, shot_name, file_type, version, subversion, format_suffix, resolved_category
            )
            return os.path.exists(os.path.join(target_dir, f"{base}{ext}"))

        next_v, next_sub = next_available_version(next_v, next_sub, [], rename_target_exists)
        new_base_name = format_versioned_base_name(
            scene_name, shot_name, file_type, next_v, next_sub, format_suffix, resolved_category
        )
        new_filename = f"{new_base_name}{ext}"
        new_filepath = os.path.join(target_dir, new_filename)
            
        try:
            os.rename(source_path, new_filepath)
            log_action(f"RENAMED: '{f}' TO '{new_filename}'")
            msgs.append(f"Renamed: {new_filename}")
            undo_ops.append(('rename', new_filepath, source_path))
        except Exception as e:
            log_action(f"Failed to rename {f}: {e}", is_error=True)
            msgs.append(f"Error ({f}): {e}")
            
    return msgs, undo_ops

def get_subfolders(parent_dir):
    """Returns a list of immediate subdirectories inside parent_dir."""
    try:
        if not os.path.exists(parent_dir):
            return []
        
        folders = []
        for name in os.listdir(parent_dir):
            full_path = os.path.join(parent_dir, name)
            # Skip hidden files/folders and globally/path ignored folders
            if config.is_ignored(full_path, name):
                continue
            if os.path.isdir(os.path.join(parent_dir, name)):
                folders.append(name)
        return folders
    except Exception as e:
        print(f"Error accessing {parent_dir}: {e}")
        return []

class UndoManager:
    """Tracks generic file operations and reverse-executes them."""
    def __init__(self):
        self._history = []  # List of dicts: {'description': str, 'operations': list of ops}
        
    def push_action(self, description, operations):
        """
        operations is a list of tuples:
        ('copy', filepath) -> reverse is to delete the filepath
        ('rename', current_filepath, original_filepath) -> reverse is to rename current back to original
        ('folder_create', folder_path) -> reverse is to shutil.rmtree the folder
        """
        if operations:
            self._history.append({
                'description': description,
                'operations': operations
            })
            log_action(f"UNDO STACK: Added '{description}' with {len(operations)} operations.")

    def has_undo(self):
        return len(self._history) > 0
        
    def get_last_action_desc(self):
        if not self._history:
            return None
        return self._history[-1]['description']
        
    def pop_and_undo(self):
        """Pops the last action and executes its reverse operations."""
        if not self._history:
            return False, "Nothing to undo."
            
        action = self._history.pop()
        return self._execute_undo(action['operations'], action['description'])
        
    def undo_at(self, index):
        """Pops a specific action by index and executes its reverse operations."""
        if index < 0 or index >= len(self._history):
            return False, "Invalid undo index."
            
        action = self._history.pop(index)
        return self._execute_undo(action['operations'], action['description'])
        
    def _execute_undo(self, ops, description):
        # We must execute them in reverse order of how they were applied
        ops.reverse()
        
        success_count = 0
        error_count = 0
        
        for op in ops:
            try:
                op_type = op[0]
                if op_type == 'copy':
                    filepath = op[1]
                    if os.path.exists(filepath) and os.path.isfile(filepath):
                        # SAFETY: Ensure we only delete files conforming to our versioning syntax
                        if "_v" in os.path.basename(filepath):
                            os.remove(filepath)
                            success_count += 1
                        else:
                            log_action(f"Undo Security Blocked: '{filepath}' did not contain versioning string.", is_error=True)
                elif op_type == 'rename':
                    current_path, original_path = op[1], op[2]
                    if os.path.exists(current_path):
                        os.rename(current_path, original_path)
                        success_count += 1
                elif op_type == 'folder_create':
                    folder_path = op[1]
                    if os.path.exists(folder_path) and os.path.isdir(folder_path):
                        # SAFETY: Ensure we only delete folders specifically created as sequences
                        if "_sequence" in os.path.basename(folder_path) or "_v" in os.path.basename(folder_path):
                            shutil.rmtree(folder_path)
                            success_count += 1
                        else:
                            log_action(f"Undo Security Blocked: '{folder_path}' did not look like sequence.", is_error=True)
            except Exception as e:
                log_action(f"Undo operation failed: {e}", is_error=True)
                error_count += 1
                
        log_action(f"UNDID: '{description}'. Success: {success_count}, Errors: {error_count}")
        return True, description

undo_manager = UndoManager()
