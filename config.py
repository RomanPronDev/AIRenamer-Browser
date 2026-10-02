# SPDX-License-Identifier: GPL-3.0-only

import os
import base64
import datetime
import hashlib
import json
import re
import shutil
import socket
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from runtime_support import (
    CONFIG_BUNDLE_SCHEMA,
    CONFIG_BUNDLE_SCHEMA_VERSION,
    SETTINGS_SCHEMA_VERSION,
    DEFAULT_EXT_IMAGE,
    DEFAULT_EXT_VIDEO,
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_IGNORED_FOLDERS,
    DEFAULT_ADDITIONAL_CATEGORIES,
    RuntimePathError,
    copy_tree_missing as runtime_copy_tree_missing,
    default_settings,
    empty_machine_state as runtime_empty_machine_state,
    exclusive_file_lock,
    get_local_base_dir,
    get_local_runtime_paths,
    get_machine_id as runtime_get_machine_id,
    write_emergency_diagnostic,
)

if getattr(sys, 'frozen', False):
    # Run by PyInstaller. The exe is in sys.executable. We want the folder containing the exe.
    APP_DIR = os.path.dirname(sys.executable)
else:
    # Run from script
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Kept as compatibility names for integrations written before the standalone
# runtime. The environment variable and server_config.json are no longer read.
SERVER_ROOT_ENV = "MEDIARENAMER_SERVER_ROOT"
SERVER_CONFIG_FILENAME = "server_config.json"
LOCAL_BASE_DIR = get_local_base_dir(required=False)
SERVER_ROOT_SOURCE = "local AppData standalone runtime"
SERVER_ROOT_ERROR = ""
RUNTIME_WARNINGS = []
LAST_LOG_WRITE_ERROR = ""
DEGRADED_MODE = False

def resolve_server_root(
    app_dir: str = APP_DIR,
    environ=None,
    local_base_dir: str | None = None,
    allow_dev_fallback: bool | None = None,
) -> str:
    """Return the local standalone root.

    This legacy function name remains callable so older plugins do not break,
    but server bootstrap files, UNC paths and ``MEDIARENAMER_SERVER_ROOT`` are
    deliberately ignored.
    """
    del app_dir, allow_dev_fallback
    root = local_base_dir or get_local_base_dir(environ=environ, required=True)
    global SERVER_ROOT_SOURCE
    SERVER_ROOT_SOURCE = "local AppData standalone runtime"
    return os.path.normpath(root)


try:
    SERVER_ROOT = resolve_server_root()
except RuntimePathError as exc:
    SERVER_ROOT_ERROR = str(exc)
    SERVER_ROOT = ""
RUNTIME_DIR = SERVER_ROOT
CONFIG_DIR = os.path.join(RUNTIME_DIR, "Config") if RUNTIME_DIR else ""
STATE_DIR = os.path.join(RUNTIME_DIR, "State") if RUNTIME_DIR else ""
LOGS_DIR = os.path.join(RUNTIME_DIR, "Logs") if RUNTIME_DIR else ""
TOOLS_DIR = os.path.join(RUNTIME_DIR, "Tools") if RUNTIME_DIR else ""
DIAGNOSTICS_DIR = os.path.join(RUNTIME_DIR, "Diagnostics") if RUNTIME_DIR else ""
BACKUPS_DIR = os.path.join(CONFIG_DIR, "Backups") if CONFIG_DIR else ""
LICENSES_DIR = os.path.join(RUNTIME_DIR, "Licenses") if RUNTIME_DIR else ""
FFMPEG_PATH = os.path.join(TOOLS_DIR, "ffmpeg.exe") if TOOLS_DIR else ""

LEGAL_ROOT_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "SOURCE_OFFER.md",
)
LEGAL_THIRD_PARTY_DIRECTORY = "third_party_licenses"
_LEGAL_COPY_CHUNK_SIZE = 1024 * 1024
LEGACY_DEFAULT_UPSCALE_CATEGORY = {
    "id": "upscale",
    "folder": "UPSCALE",
    "media_type": "image",
    "type_suffix": "UPS",
}

# Legacy data is imported only through import_legacy_config(); startup never
# probes a server or copies from a neighbouring _config automatically.
LEGACY_CONFIG_DIR = ""
LEGACY_LOGS_DIR = ""
LEGACY_TOOLS_DIR = ""
PROJECTS_FILE_LEGACY = os.path.join(CONFIG_DIR, "projects.txt") if CONFIG_DIR else ""
PROJECTS_FILE = os.path.join(CONFIG_DIR, "projects.json") if CONFIG_DIR else ""
PROJECTS_DIR = os.path.join(STATE_DIR, "Projects") if STATE_DIR else ""
EXTENSIONS_FILE = os.path.join(CONFIG_DIR, "extensions.json") if CONFIG_DIR else ""
FOLDERS_FILE = os.path.join(CONFIG_DIR, "folders.json") if CONFIG_DIR else ""
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json") if CONFIG_DIR else ""
CACHE_DIR = os.path.join(STATE_DIR, "Cache") if STATE_DIR else ""
HISTORY_DIR = os.path.join(STATE_DIR, "History") if STATE_DIR else ""
CACHE_FILE = os.path.join(CONFIG_DIR, "cache.json") if CONFIG_DIR else ""
HISTORY_FILE = os.path.join(CONFIG_DIR, "history.json") if CONFIG_DIR else ""
HELP_FILE = os.path.join(CONFIG_DIR, "help.html") if CONFIG_DIR else ""
SERVER_CONFIG_FILE = ""
BUNDLE_LOCK_FILE = os.path.join(STATE_DIR, "config-bundle.lock") if STATE_DIR else ""



# Standard Subfolders inside a Shot
DIR_KEYFRAME = "KEYFRAMES"
DIR_VIDEO = "VIDEO"

# Structure Configuration
SCENE_PREFIX = ""
SHOT_PREFIX = ""
TARGET_PREFIX = ""
SUBVERSION_ENABLED = True
SKIP_SEQUENCE = False
FILENAME_TEMPLATE = DEFAULT_FILENAME_TEMPLATE
APP_DISPLAY_NAME = "MediaRenamer"
DEFAULT_IMAGE_TYPE_SUFFIX = "IMG"
DEFAULT_VIDEO_TYPE_SUFFIX = "VID"
IMAGE_TYPE_SUFFIX = DEFAULT_IMAGE_TYPE_SUFFIX
VIDEO_TYPE_SUFFIX = DEFAULT_VIDEO_TYPE_SUFFIX

# Defaults if extensions.json is missing
ALLOWED_EXT_VIDEO = []
ALLOWED_EXT_IMAGE = []
ALLOWED_EXTENSIONS = []
IGNORED_FOLDERS = []
IGNORED_NAMES = set()
IGNORED_PATHS = set()

MEDIA_TYPE_IMAGE = "image"
MEDIA_TYPE_VIDEO = "video"


@dataclass(frozen=True)
class MediaCategory:
    """A configured media destination shown as one Existing Files section."""

    id: str
    folder: str
    media_type: str
    type_suffix: str

    @property
    def file_type(self) -> str:
        return TYPE_VID if self.media_type == MEDIA_TYPE_VIDEO else TYPE_IMG


MEDIA_CATEGORIES: list[MediaCategory] = []
ADDITIONAL_CATEGORIES: list[MediaCategory] = []


def record_runtime_warning(message: str):
    """Remember a runtime warning and mirror it into local diagnostics."""
    global DEGRADED_MODE
    DEGRADED_MODE = True
    if message not in RUNTIME_WARNINGS:
        RUNTIME_WARNINGS.append(message)
    write_emergency_diagnostic(message)


def get_runtime_warnings() -> list[str]:
    return list(RUNTIME_WARNINGS)


def clear_runtime_warnings():
    RUNTIME_WARNINGS.clear()
    global DEGRADED_MODE, LAST_LOG_WRITE_ERROR
    DEGRADED_MODE = False
    LAST_LOG_WRITE_ERROR = ""
def normalize_prefix(value) -> str:
    """Return a safe relative folder prefix from settings.json."""
    if not isinstance(value, str):
        return ""

    prefix = value.strip()
    if not prefix:
        return ""

    drive, tail = os.path.splitdrive(prefix)
    if drive and tail.strip("\\/"):
        prefix = tail

    prefix = prefix.strip("\\/")
    return os.path.normpath(prefix) if prefix else ""

def normalize_folder_name(value, default: str) -> str:
    if not isinstance(value, str):
        return default
    name = value.strip().strip("\\/")
    return name or default

def normalize_type_suffix(value, default: str) -> str:
    if not isinstance(value, str):
        return default
    suffix = value.strip()
    return suffix or default

def normalize_app_name(value, default: str = "MediaRenamer") -> str:
    """Return the fixed product name; it is no longer user-configurable."""
    return "MediaRenamer"


_WINDOWS_RESERVED_COMPONENTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _is_windows_safe_component(value: str) -> bool:
    """Return whether ``value`` is safe as one Windows path component."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    if value[-1] in {" ", "."}:
        return False
    if "/" in value or "\\" in value:
        return False
    if any(char in '<>:"|?*' or ord(char) < 32 for char in value):
        return False
    # Windows reserves these device names even when an extension is present.
    device_stem = value.split(".", 1)[0].upper()
    return device_stem not in _WINDOWS_RESERVED_COMPONENTS


def _valid_single_folder_name(value) -> str:
    if not isinstance(value, str):
        return ""
    folder = value.strip()
    if not folder or folder in {".", ".."}:
        return ""
    if os.path.isabs(folder) or os.path.splitdrive(folder)[0]:
        return ""
    if "/" in folder or "\\" in folder or os.path.basename(folder) != folder:
        return ""
    if not _is_windows_safe_component(folder):
        return ""
    return folder


def normalize_media_categories(data) -> list[MediaCategory]:
    """Validate configured categories and prepend the two built-in sections."""
    keyframe_folder = _valid_single_folder_name(DIR_KEYFRAME)
    video_folder = _valid_single_folder_name(DIR_VIDEO)
    if not keyframe_folder:
        record_runtime_warning("Invalid keyframe_folder; using 'KEYFRAMES'.")
        keyframe_folder = "KEYFRAMES"
    if not video_folder:
        record_runtime_warning("Invalid video_folder; using 'VIDEO'.")
        video_folder = "VIDEO"
    if keyframe_folder.casefold() == video_folder.casefold():
        record_runtime_warning(
            "keyframe_folder and video_folder must be unique; built-in folder defaults were restored."
        )
        keyframe_folder, video_folder = "KEYFRAMES", "VIDEO"
    image_suffix = IMAGE_TYPE_SUFFIX
    video_suffix = VIDEO_TYPE_SUFFIX
    if not _is_windows_safe_component(image_suffix):
        record_runtime_warning("Invalid image_type_suffix; using 'IMG'.")
        image_suffix = DEFAULT_IMAGE_TYPE_SUFFIX
    if not _is_windows_safe_component(video_suffix):
        record_runtime_warning("Invalid video_type_suffix; using 'VID'.")
        video_suffix = DEFAULT_VIDEO_TYPE_SUFFIX
    categories = [
        MediaCategory("keyframes", keyframe_folder, MEDIA_TYPE_IMAGE, image_suffix),
        MediaCategory("video", video_folder, MEDIA_TYPE_VIDEO, video_suffix),
    ]
    seen_ids = {item.id.casefold() for item in categories}
    seen_folders = {os.path.normcase(item.folder).casefold() for item in categories}
    if data is None:
        data = DEFAULT_ADDITIONAL_CATEGORIES
    if not isinstance(data, list):
        record_runtime_warning("settings.additional_categories must be a list; configured entries were skipped.")
        return categories

    for index, raw in enumerate(data):
        reason = ""
        if not isinstance(raw, dict):
            reason = "entry must be an object"
        else:
            raw_id = raw.get("id")
            category_id = raw_id.strip().casefold() if isinstance(raw_id, str) else ""
            folder = _valid_single_folder_name(raw.get("folder"))
            media_type = raw.get("media_type", "")
            media_type = media_type.strip().casefold() if isinstance(media_type, str) else ""
            suffix = raw.get("type_suffix", "")
            suffix = suffix.strip() if isinstance(suffix, str) else ""
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", category_id):
                reason = "id must match [a-z0-9][a-z0-9_-]*"
            elif not folder:
                reason = "folder must be one relative path segment"
            elif media_type not in {MEDIA_TYPE_IMAGE, MEDIA_TYPE_VIDEO}:
                reason = "media_type must be 'image' or 'video'"
            elif not _is_windows_safe_component(suffix):
                reason = "type_suffix must be a safe Windows filename component"
            elif category_id in seen_ids:
                reason = f"duplicate id '{category_id}'"
            elif os.path.normcase(folder).casefold() in seen_folders:
                reason = f"duplicate folder '{folder}'"
        if reason:
            record_runtime_warning(f"Skipped additional_categories[{index}]: {reason}.")
            continue
        category = MediaCategory(category_id, folder, media_type, suffix)
        categories.append(category)
        seen_ids.add(category.id.casefold())
        seen_folders.add(os.path.normcase(category.folder).casefold())
    return categories


def _set_media_categories(data=None):
    normalized = normalize_media_categories(data)
    MEDIA_CATEGORIES.clear()
    MEDIA_CATEGORIES.extend(normalized)
    ADDITIONAL_CATEGORIES.clear()
    ADDITIONAL_CATEGORIES.extend(normalized[2:])


def get_media_categories(media_type: str | None = None) -> list[MediaCategory]:
    if not MEDIA_CATEGORIES:
        _set_media_categories(DEFAULT_ADDITIONAL_CATEGORIES)
    if media_type is None:
        return list(MEDIA_CATEGORIES)
    normalized_type = str(media_type).strip().casefold()
    if normalized_type == TYPE_IMG.casefold():
        normalized_type = MEDIA_TYPE_IMAGE
    elif normalized_type == TYPE_VID.casefold():
        normalized_type = MEDIA_TYPE_VIDEO
    return [item for item in MEDIA_CATEGORIES if item.media_type == normalized_type]


def get_media_category(value) -> MediaCategory | None:
    """Resolve a category object, id, folder, suffix, or legacy IMG/VID token."""
    if isinstance(value, MediaCategory):
        return value
    if value is None:
        return None
    token = str(value).strip().casefold()
    categories = get_media_categories()
    for item in categories:
        if token in {item.id.casefold(), item.folder.casefold()}:
            return item
    # Legacy type identifiers intentionally resolve to their built-in category.
    if token in {TYPE_IMG.casefold(), MEDIA_TYPE_IMAGE}:
        return next((item for item in categories if item.id == "keyframes"), None)
    if token in {TYPE_VID.casefold(), MEDIA_TYPE_VIDEO}:
        return next((item for item in categories if item.id == "video"), None)
    matches = [item for item in categories if item.type_suffix.casefold() == token]
    return matches[0] if len(matches) == 1 else None


def get_category_for_file_type(file_type: str) -> MediaCategory | None:
    token = str(file_type or "").strip().casefold()
    if token in {TYPE_VID.casefold(), MEDIA_TYPE_VIDEO}:
        media_type = MEDIA_TYPE_VIDEO
    elif token in {TYPE_IMG.casefold(), MEDIA_TYPE_IMAGE}:
        media_type = MEDIA_TYPE_IMAGE
    else:
        return get_media_category(file_type)
    return next((item for item in get_media_categories() if item.media_type == media_type), None)


def get_category_for_folder(folder_or_path: str) -> MediaCategory | None:
    folder = os.path.basename(os.path.normpath(str(folder_or_path or "")))
    return next((item for item in get_media_categories() if item.folder.casefold() == folder.casefold()), None)


def resolve_media_category(category=None, *, file_type=None, target_dir=None) -> MediaCategory:
    resolved = get_media_category(category)
    if resolved is None and target_dir:
        resolved = get_category_for_folder(target_dir)
    if resolved is None and file_type is not None:
        resolved = get_media_category(file_type) or get_category_for_file_type(file_type)
    if resolved is None:
        raise ValueError(f"Unknown media category: {category if category is not None else file_type}")
    return resolved

def parse_yes_no(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"yes", "true", "1", "on", "enabled"}:
            return True
        if normalized in {"no", "false", "0", "off", "disabled"}:
            return False
    return default

def join_prefixed(base_path: str, prefix: str) -> str:
    normalized_prefix = normalize_prefix(prefix)
    if not normalized_prefix:
        return os.path.normpath(base_path)
    return os.path.normpath(os.path.join(base_path, normalized_prefix))

def get_scene_root(project_path: str) -> str:
    return join_prefixed(project_path, SCENE_PREFIX)

def get_scene_path(project_path: str, scene_name: str) -> str:
    return os.path.normpath(os.path.join(get_scene_root(project_path), scene_name))

def get_shot_root(scene_path: str) -> str:
    return join_prefixed(scene_path, SHOT_PREFIX)

def get_project_shot_root(project_path: str) -> str:
    """Return the shot parent when the Sequence level is bypassed."""
    return get_shot_root(get_scene_root(project_path))

def get_shot_path(scene_path: str, shot_name: str) -> str:
    return os.path.normpath(os.path.join(get_shot_root(scene_path), shot_name))

def get_target_base(shot_path: str) -> str:
    return join_prefixed(shot_path, TARGET_PREFIX)

PROJECT_LAYOUT_SEQUENCES = "sequences"
PROJECT_LAYOUT_SHOTS = "shots"
PROJECT_LAYOUTS = {PROJECT_LAYOUT_SEQUENCES, PROJECT_LAYOUT_SHOTS}
SHOT_WORK_FOLDERS = {"animation", "art_direction", "audio", "cache", "compo", "dmp", "fx", "genai", "layout", "previews", "render", "renderoutput", "roto", "source", "temp", "tracking"}


def _layout_child_directories(path: str, limit: int | None = None) -> list[str]:
    try:
        children = []
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir() and not is_ignored(entry.path, entry.name):
                    children.append(entry.path)
                    if limit is not None and len(children) >= limit:
                        break
        return children
    except (OSError, TypeError):
        return []


def detect_project_layout(
    project_path: str,
    fallback: str | None = None,
    *,
    settings: dict | None = None,
) -> str:
    """Detect whether a project exposes sequences or shots at its first level."""
    fallback = (
        fallback
        if fallback in PROJECT_LAYOUTS
        else PROJECT_LAYOUT_SHOTS if SKIP_SEQUENCE else PROJECT_LAYOUT_SEQUENCES
    )
    if not isinstance(project_path, str) or not os.path.isdir(project_path):
        return fallback

    if isinstance(settings, dict):
        scene_prefix = str(settings.get("scene_prefix", ""))
        shot_prefix = str(settings.get("shot_prefix", ""))
        target_prefix = str(settings.get("target_prefix", ""))
        category_folders = [
            str(settings.get("keyframe_folder", DIR_KEYFRAME)),
            str(settings.get("video_folder", DIR_VIDEO)),
            *[
                str(category.get("folder", ""))
                for category in settings.get("additional_categories", [])
                if isinstance(category, dict)
            ],
        ]
    else:
        scene_prefix, shot_prefix, target_prefix = (
            SCENE_PREFIX, SHOT_PREFIX, TARGET_PREFIX
        )
        category_folders = [
            category.folder for category in get_media_categories()
        ]

    scene_root = join_prefixed(project_path, scene_prefix)
    shot_root = lambda path: join_prefixed(path, shot_prefix)
    def has_media(path: str) -> bool:
        target = join_prefixed(path, target_prefix)
        if target != path and is_ignored(target, os.path.basename(target)):
            return False
        return any(
            os.path.isdir(os.path.join(target, folder))
            and not is_ignored(os.path.join(target, folder), folder)
            for folder in category_folders if folder
        )

    def work_folders(path: str) -> set[str]:
        return {
            os.path.basename(child).casefold()
            for child in _layout_child_directories(path, limit=16)
        } & SHOT_WORK_FOLDERS

    direct_root = shot_root(scene_root)
    # A project can contain thousands of shots on a network drive. Inspect a
    # representative, bounded set instead of traversing the whole tree for
    # every project registration and navigation request.
    direct_candidates = _layout_child_directories(direct_root, limit=24)
    sequence_candidates = (
        direct_candidates if direct_root == scene_root
        else _layout_child_directories(scene_root, limit=24)
    )

    def has_visible_genai(path: str) -> bool:
        marker = os.path.join(path, "genai")
        return not is_ignored(marker, "genai") and os.path.isdir(marker)

    # Most active projects contain genai. Decide from the first real shot we
    # encounter, before probing the rest of a large remote project tree.
    if direct_root != scene_root:
        for path in direct_candidates:
            if has_visible_genai(path):
                return PROJECT_LAYOUT_SHOTS
    nested_shots = []
    for sequence_path in sequence_candidates:
        if direct_root == scene_root and has_visible_genai(sequence_path):
            return PROJECT_LAYOUT_SHOTS
        sample = _layout_child_directories(shot_root(sequence_path), limit=4)
        for path in sample:
            if has_visible_genai(path):
                return PROJECT_LAYOUT_SEQUENCES
        nested_shots.extend(sample)
        if len(nested_shots) >= 32:
            break

    direct_work = [work_folders(path) for path in direct_candidates]
    nested_work = [work_folders(path) for path in nested_shots]

    # genai is a strong shot marker. Look at both possible depths before
    # deciding: numeric sequence and shot names do not reveal the layout.
    direct_genai = sum("genai" in folders for folders in direct_work)
    nested_genai = sum("genai" in folders for folders in nested_work)
    if nested_genai > direct_genai:
        return PROJECT_LAYOUT_SEQUENCES
    if direct_genai > nested_genai:
        return PROJECT_LAYOUT_SHOTS

    direct_departments = sum(len(folders) >= 2 for folders in direct_work)
    nested_departments = sum(len(folders) >= 2 for folders in nested_work)
    if nested_departments > direct_departments:
        return PROJECT_LAYOUT_SEQUENCES
    if direct_departments > nested_departments:
        return PROJECT_LAYOUT_SHOTS

    direct_media = sum(has_media(path) for path in direct_candidates)
    nested_media = 0
    nested_shot_names = 0
    for shot_path in nested_shots:
        nested_media += int(has_media(shot_path))
        nested_shot_names += int(
            bool(re.match(r"^(?:sh|shot)[-_ ]?\d+", os.path.basename(shot_path), re.I))
        )

    if nested_media > direct_media:
        return PROJECT_LAYOUT_SEQUENCES
    if direct_media > nested_media:
        return PROJECT_LAYOUT_SHOTS

    direct_shot_names = sum(
        bool(re.match(r"^(?:sh|shot)[-_ ]?\d+", os.path.basename(path), re.I))
        for path in direct_candidates
    )
    if nested_shot_names > direct_shot_names:
        return PROJECT_LAYOUT_SEQUENCES
    sequence_names = sum(
        bool(re.match(r"^(?:sq|seq|sc|scene)[-_ ]?\d+", os.path.basename(path), re.I))
        for path in sequence_candidates
    )
    if sequence_names and not direct_shot_names:
        return PROJECT_LAYOUT_SEQUENCES
    if direct_shot_names:
        return PROJECT_LAYOUT_SHOTS

    # A populated second directory level is a useful final signal for projects
    # that have not received media yet.
    if any(_layout_child_directories(shot_root(path), limit=1) for path in sequence_candidates):
        return PROJECT_LAYOUT_SEQUENCES
    if direct_candidates:
        return PROJECT_LAYOUT_SHOTS
    return fallback


def autodetect_project_modes(
    projects: dict,
    existing_modes: dict | None = None,
    *,
    settings: dict | None = None,
) -> dict:
    """Return a normalized layout mode for every project shortcut."""
    existing_modes = existing_modes if isinstance(existing_modes, dict) else {}
    return {
        name: detect_project_layout(
            path, existing_modes.get(name), settings=settings
        )
        for name, path in projects.items()
        if isinstance(name, str) and isinstance(path, str)
    }

_CONFIG_STORE_PROCESS_LOCK = threading.RLock()
_CONFIG_STORE_CONTEXT = threading.local()


@contextmanager
def config_store_lock(timeout: float = 15.0):
    """Serialize every settings/projects/ignored mutation.

    The process RLock plus thread-local depth makes the coordinator re-entrant,
    while the lease-backed file lock coordinates separate application
    processes without nested self-deadlocks.
    """
    if not _CONFIG_STORE_PROCESS_LOCK.acquire(timeout=max(0.0, timeout)):
        raise TimeoutError(f"Timed out waiting for config process lock: {_bundle_lock_path()}")
    try:
        depth = int(getattr(_CONFIG_STORE_CONTEXT, "depth", 0))
        if depth:
            _CONFIG_STORE_CONTEXT.depth = depth + 1
            try:
                yield
            finally:
                _CONFIG_STORE_CONTEXT.depth = depth
            return

        _CONFIG_STORE_CONTEXT.depth = 1
        try:
            with exclusive_file_lock(
                _bundle_lock_path(),
                timeout=timeout,
                stale_seconds=300.0,
            ):
                yield
        finally:
            _CONFIG_STORE_CONTEXT.depth = 0
    finally:
        _CONFIG_STORE_PROCESS_LOCK.release()


@contextmanager
def json_file_lock(
    target_path: str,
    timeout: float = 10.0,
    poll_interval: float = 0.1,
    stale_seconds: float = 300.0,
):
    """Create a lightweight lock beside a shared JSON file."""
    lock_path = f"{target_path}.lock"
    with exclusive_file_lock(
        lock_path,
        timeout=timeout,
        poll_interval=poll_interval,
        stale_seconds=stale_seconds,
    ):
        yield

def read_json_file(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default

def atomic_write_json(path: str, data):
    """Write shared JSON atomically under a per-file lock."""
    with json_file_lock(path):
        _write_json_unlocked(path, data)

def atomic_write_json_if_missing(path: str, data):
    """Create a shared JSON file only when it does not already exist."""
    if not path or os.path.exists(path):
        return
    atomic_write_json(path, data)

def _migrate_settings_defaults_unlocked() -> bool:
    """Add defaults and remove the retired automatic UPSCALE category once."""
    if not SETTINGS_FILE or not os.path.exists(SETTINGS_FILE):
        return False

    data = read_json_file(SETTINGS_FILE, None)
    if not isinstance(data, dict):
        return False

    defaults = default_settings()
    missing_keys = [key for key in defaults if key not in data]
    try:
        source_schema_version = int(data.get("settings_schema_version", 1))
    except (TypeError, ValueError):
        source_schema_version = 1
    retire_legacy_upscale = source_schema_version < 2
    old_categories = data.get("additional_categories", [])
    migrated_categories = old_categories
    if retire_legacy_upscale and isinstance(old_categories, list):
        migrated_categories = [
            category
            for category in old_categories
            if category != LEGACY_DEFAULT_UPSCALE_CATEGORY
        ]
    categories_changed = migrated_categories != old_categories
    app_name_changed = data.get("app_name") != "MediaRenamer"
    schema_changed = source_schema_version != SETTINGS_SCHEMA_VERSION
    if not missing_keys and not categories_changed and not app_name_changed and not schema_changed:
        return False

    backup_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(
        os.path.dirname(SETTINGS_FILE),
        f"settings.backup.{backup_stamp}.{os.getpid()}.json",
    )
    shutil.copy2(SETTINGS_FILE, backup_path)

    updated = dict(data)
    for key in missing_keys:
        updated[key] = defaults[key]
    if categories_changed:
        updated["additional_categories"] = migrated_categories
    updated["app_name"] = "MediaRenamer"
    updated["settings_schema_version"] = SETTINGS_SCHEMA_VERSION
    atomic_write_json(SETTINGS_FILE, updated)
    return True


def migrate_settings_defaults() -> bool:
    with config_store_lock():
        return _migrate_settings_defaults_unlocked()


def _write_json_unlocked(path: str, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    os.replace(tmp_path, path)

def update_json_file(path: str, default, updater):
    """Read, update, and atomically replace JSON while holding one lock."""
    with json_file_lock(path):
        data = read_json_file(path, default)
        updated = updater(data)
        _write_json_unlocked(path, updated)
        return updated


def get_runtime_paths() -> dict[str, str]:
    """Expose the writable standalone layout to the UI and diagnostics."""
    return {
        "root": RUNTIME_DIR,
        "config": CONFIG_DIR,
        "state": STATE_DIR,
        "tools": TOOLS_DIR,
        "logs": LOGS_DIR,
        "diagnostics": DIAGNOSTICS_DIR,
        "licenses": get_licenses_dir(),
    }


def get_licenses_dir() -> str:
    """Return the legal-material directory for the active local runtime.

    Deriving this from ``RUNTIME_DIR`` keeps test and embedded runtimes
    isolated even when their roots are selected after this module is imported.
    ``LICENSES_DIR`` remains available as the default-layout compatibility
    constant.
    """
    return os.path.join(RUNTIME_DIR, "Licenses") if RUNTIME_DIR else ""


def _path_is_link_or_junction(path: str) -> bool:
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _path_is_within(root: str, candidate: str) -> bool:
    try:
        root_key = os.path.normcase(os.path.abspath(root))
        candidate_key = os.path.normcase(os.path.abspath(candidate))
        return os.path.commonpath((root_key, candidate_key)) == root_key
    except (OSError, ValueError):
        return False


def _normalize_legal_relative_path(relative_path: str) -> str:
    """Validate a repository-controlled legal-material relative path."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("Legal-material path must be a non-empty string.")
    drive, _tail = os.path.splitdrive(relative_path)
    if drive or os.path.isabs(relative_path):
        raise ValueError(f"Legal-material path must be relative: {relative_path!r}.")
    parts = relative_path.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe legal-material path: {relative_path!r}.")
    normalized = os.path.normpath(os.path.join(*parts))
    if normalized == ".." or normalized.startswith(f"..{os.sep}"):
        raise ValueError(f"Unsafe legal-material path: {relative_path!r}.")
    return normalized


def _legal_material_source_root() -> str:
    if getattr(sys, "frozen", False):
        root = getattr(sys, "_MEIPASS", "")
    else:
        root = APP_DIR
    if not isinstance(root, str) or not root:
        raise RuntimeError("Bundled legal-material source root is unavailable.")
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Legal-material source root does not exist: {root}")
    return root


def _safe_legal_source_path(
    source_root: str,
    relative_path: str,
    *,
    expect_directory: bool = False,
) -> str:
    relative = _normalize_legal_relative_path(relative_path)
    root_absolute = os.path.abspath(source_root)
    root_real = os.path.realpath(root_absolute)
    candidate = os.path.abspath(os.path.join(root_absolute, relative))
    if not _path_is_within(root_absolute, candidate):
        raise ValueError(f"Legal-material source escapes its root: {relative_path!r}.")

    current = root_absolute
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.lexists(current) and _path_is_link_or_junction(current):
            raise ValueError(f"Legal-material source cannot be a link: {current}")

    candidate_real = os.path.realpath(candidate)
    if not _path_is_within(root_real, candidate_real):
        raise ValueError(f"Legal-material source resolves outside its root: {candidate}")
    if expect_directory:
        if not os.path.isdir(candidate):
            raise FileNotFoundError(f"Legal-material directory is missing: {candidate}")
    elif not os.path.isfile(candidate):
        raise FileNotFoundError(f"Legal-material file is missing: {candidate}")
    return candidate


def _ensure_safe_legal_destination_directory(
    path: str,
    *,
    trusted_root: str,
) -> str:
    root_absolute = os.path.abspath(trusted_root)
    destination = os.path.abspath(path)
    if not _path_is_within(root_absolute, destination):
        raise ValueError(f"Legal-material destination escapes local runtime: {destination}")

    if os.path.lexists(destination):
        if _path_is_link_or_junction(destination):
            raise ValueError(f"Legal-material destination cannot be a link: {destination}")
        if not os.path.isdir(destination):
            raise ValueError(f"Legal-material destination is not a directory: {destination}")
    else:
        os.mkdir(destination)

    root_real = os.path.realpath(root_absolute)
    destination_real = os.path.realpath(destination)
    if not _path_is_within(root_real, destination_real):
        raise ValueError(
            f"Legal-material destination resolves outside local runtime: {destination}"
        )
    return destination


def _sha256_regular_file(path: str) -> tuple[int, str]:
    if _path_is_link_or_junction(path):
        raise ValueError(f"Legal material cannot be read through a link: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"Legal material is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                block = handle.read(_LEGAL_COPY_CHUNK_SIZE)
                if not block:
                    break
                digest.update(block)
        return file_stat.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _atomic_copy_legal_file(source: str, destination: str) -> bool:
    """Atomically update one managed legal file, returning whether it changed."""
    parent = os.path.dirname(destination)
    if not os.path.isdir(parent) or _path_is_link_or_junction(parent):
        raise ValueError(f"Unsafe legal-material destination directory: {parent}")
    if os.path.lexists(destination):
        if _path_is_link_or_junction(destination) or not os.path.isfile(destination):
            raise ValueError(f"Unsafe legal-material destination file: {destination}")

    source_size, source_hash = _sha256_regular_file(source)
    if os.path.isfile(destination):
        destination_size = os.path.getsize(destination)
        if destination_size == source_size:
            _size, destination_hash = _sha256_regular_file(destination)
            if destination_hash == source_hash:
                return False

    temporary = os.path.join(
        parent,
        f".{os.path.basename(destination)}.{os.getpid()}.{time.time_ns()}.tmp",
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        copied_hash = hashlib.sha256()
        copied_size = 0
        source_descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            source_stat = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError(f"Legal material is not a regular file: {source}")
            with os.fdopen(source_descriptor, "rb", closefd=False) as input_file:
                with os.fdopen(descriptor, "wb", closefd=False) as output_file:
                    while True:
                        block = input_file.read(_LEGAL_COPY_CHUNK_SIZE)
                        if not block:
                            break
                        output_file.write(block)
                        copied_hash.update(block)
                        copied_size += len(block)
                    output_file.flush()
                    os.fsync(output_file.fileno())
        finally:
            os.close(source_descriptor)

        if copied_size != source_size or copied_hash.hexdigest() != source_hash:
            raise RuntimeError(f"Legal material changed while being copied: {source}")
        os.close(descriptor)
        descriptor = None
        if _path_is_link_or_junction(parent):
            raise ValueError(f"Unsafe legal-material destination directory: {parent}")
        os.replace(temporary, destination)
        return True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def materialize_legal_materials() -> int:
    """Copy bundled legal materials into the user's readable data directory.

    Files not managed by this function are retained. Each managed file is
    compared by size and SHA-256 and is replaced atomically only when needed.
    """
    source_root = _legal_material_source_root()
    destination_root = get_licenses_dir()
    if not destination_root:
        raise RuntimeError("Local legal-material destination is unavailable.")
    destination_root = _ensure_safe_legal_destination_directory(
        destination_root,
        trusted_root=RUNTIME_DIR,
    )

    errors = []
    updated = 0
    for filename in LEGAL_ROOT_FILES:
        try:
            source = _safe_legal_source_path(source_root, filename)
            destination = os.path.join(destination_root, filename)
            updated += int(_atomic_copy_legal_file(source, destination))
        except Exception as exc:
            errors.append(f"{filename}: {exc}")

    try:
        source_licenses = _safe_legal_source_path(
            source_root,
            LEGAL_THIRD_PARTY_DIRECTORY,
            expect_directory=True,
        )
        destination_licenses = _ensure_safe_legal_destination_directory(
            os.path.join(destination_root, LEGAL_THIRD_PARTY_DIRECTORY),
            trusted_root=destination_root,
        )
        with os.scandir(source_licenses) as entries:
            source_entries = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in source_entries:
            relative = os.path.join(LEGAL_THIRD_PARTY_DIRECTORY, entry.name)
            try:
                _normalize_legal_relative_path(relative)
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError(
                        f"Only regular, non-linked license files are supported: "
                        f"{entry.path}"
                    )
                source = _safe_legal_source_path(source_root, relative)
                destination = os.path.join(destination_licenses, entry.name)
                updated += int(_atomic_copy_legal_file(source, destination))
            except Exception as exc:
                errors.append(f"{relative}: {exc}")
    except Exception as exc:
        errors.append(f"{LEGAL_THIRD_PARTY_DIRECTORY}: {exc}")

    if errors:
        raise RuntimeError(
            "One or more legal materials could not be made user-accessible: "
            + " | ".join(errors)
        )
    return updated


def _normalize_extension_list(value, field_name: str, fallback: list[str]) -> list[str]:
    if value is None:
        value = fallback
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    normalized = []
    seen = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError(f"{field_name} entries must be strings.")
        extension = raw.strip().lower()
        if not extension:
            continue
        if "/" in extension or "\\" in extension or any(char.isspace() for char in extension):
            raise ValueError(f"Invalid extension in {field_name}: {raw!r}.")
        if not extension.startswith("."):
            extension = f".{extension}"
        if not re.fullmatch(r"\.[a-z0-9][a-z0-9._+-]*", extension):
            raise ValueError(f"Invalid extension in {field_name}: {raw!r}.")
        if extension not in seen:
            normalized.append(extension)
            seen.add(extension)
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one extension.")
    return normalized


def _validate_relative_prefix(value, field_name: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a relative path string.")
    raw = value.strip()
    if not raw:
        return ""
    drive, _ = os.path.splitdrive(raw)
    if drive or os.path.isabs(raw) or raw.startswith(("\\", "/")):
        raise ValueError(f"{field_name} must be relative.")
    parts = re.split(r"[\\/]+", raw)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} cannot contain traversal segments.")
    if any(not _is_windows_safe_component(part) for part in parts):
        raise ValueError(f"{field_name} contains an invalid Windows folder name.")
    return os.path.normpath(os.path.join(*parts))


def _validate_filename_component(value, field_name: str, fallback: str = "") -> str:
    if not isinstance(value, str):
        value = fallback
    component = value.strip()
    if not component:
        component = fallback
    if not _is_windows_safe_component(component):
        raise ValueError(f"{field_name} must be a safe filename component.")
    return component


def _normalize_settings_snapshot(settings) -> dict:
    """Validate a settings payload before it becomes persistent user data."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be a JSON object.")

    try:
        source_schema_version = int(settings.get("settings_schema_version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("settings_schema_version must be an integer.") from exc
    if source_schema_version < 1 or source_schema_version > SETTINGS_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported settings_schema_version: {source_schema_version!r}."
        )

    defaults = default_settings()
    normalized = dict(defaults)
    normalized.update(settings)
    normalized["settings_schema_version"] = SETTINGS_SCHEMA_VERSION
    if source_schema_version < 2:
        raw_categories = normalized.get("additional_categories", [])
        if isinstance(raw_categories, list):
            normalized["additional_categories"] = [
                category
                for category in raw_categories
                if category != LEGACY_DEFAULT_UPSCALE_CATEGORY
            ]

    normalized["app_name"] = normalize_app_name(normalized.get("app_name"))
    normalized["video_extensions"] = _normalize_extension_list(
        normalized.get("video_extensions"), "video_extensions", DEFAULT_EXT_VIDEO
    )
    normalized["image_extensions"] = _normalize_extension_list(
        normalized.get("image_extensions"), "image_extensions", DEFAULT_EXT_IMAGE
    )

    keyframe_folder = _valid_single_folder_name(normalized.get("keyframe_folder"))
    video_folder = _valid_single_folder_name(normalized.get("video_folder"))
    if not keyframe_folder:
        raise ValueError("keyframe_folder must be one relative folder name.")
    if not video_folder:
        raise ValueError("video_folder must be one relative folder name.")
    if keyframe_folder.casefold() == video_folder.casefold():
        raise ValueError("keyframe_folder and video_folder must be unique.")
    normalized["keyframe_folder"] = keyframe_folder
    normalized["video_folder"] = video_folder

    normalized["image_type_suffix"] = _validate_filename_component(
        normalized.get("image_type_suffix"),
        "image_type_suffix",
        DEFAULT_IMAGE_TYPE_SUFFIX,
    )
    normalized["video_type_suffix"] = _validate_filename_component(
        normalized.get("video_type_suffix"),
        "video_type_suffix",
        DEFAULT_VIDEO_TYPE_SUFFIX,
    )
    normalized["scene_prefix"] = _validate_relative_prefix(
        normalized.get("scene_prefix", ""), "scene_prefix"
    )
    normalized["shot_prefix"] = _validate_relative_prefix(
        normalized.get("shot_prefix", ""), "shot_prefix"
    )
    normalized["target_prefix"] = _validate_relative_prefix(
        normalized.get("target_prefix", ""), "target_prefix"
    )
    normalized["subversion_enabled"] = (
        "Yes" if parse_yes_no(normalized.get("subversion_enabled"), True) else "No"
    )
    normalized["skip_sequence"] = (
        "Yes" if parse_yes_no(normalized.get("skip_sequence"), False) else "No"
    )

    template = normalized.get("filename_template")
    if not isinstance(template, str) or not template.strip():
        raise ValueError("filename_template must be a non-empty string.")
    template = template.strip()
    if "/" in template or "\\" in template or any(ord(char) < 32 for char in template):
        raise ValueError("filename_template cannot contain path separators.")
    if template.count("{") != template.count("}"):
        raise ValueError("filename_template contains unmatched braces.")
    placeholders = set(re.findall(r"\{([^{}]+)\}", template))
    allowed_placeholders = {
        "sequence",
        "scene",
        "shot",
        "type",
        "version",
        "subversion",
        "format",
    }
    unknown = placeholders - allowed_placeholders
    if unknown:
        raise ValueError(
            "filename_template contains unknown placeholders: "
            + ", ".join(sorted(unknown))
        )
    if "version" not in placeholders:
        raise ValueError("filename_template must contain {version}.")
    # Any remaining brace means a malformed/nested placeholder which the
    # current substitution engine would otherwise leak into a filename.
    stripped_placeholders = re.sub(r"\{[^{}]+\}", "", template)
    if "{" in stripped_placeholders or "}" in stripped_placeholders:
        raise ValueError("filename_template contains malformed placeholders.")
    probe_values = {
        "sequence": "SEQ",
        "scene": "SCENE",
        "shot": "SHOT",
        "type": "TYPE",
        "version": "001",
        "subversion": "00",
        "format": ".png",
    }
    filename_probe = template
    for field, probe in probe_values.items():
        filename_probe = filename_probe.replace(f"{{{field}}}", probe)
    if not _is_windows_safe_component(filename_probe):
        raise ValueError("filename_template can produce an invalid Windows filename.")
    normalized["filename_template"] = template

    ignored = normalized.get("ignored_folders", DEFAULT_IGNORED_FOLDERS)
    if not isinstance(ignored, list) or any(not isinstance(item, str) for item in ignored):
        raise ValueError("ignored_folders must be a list of strings.")
    normalized["ignored_folders"] = list(dict.fromkeys(item.strip() for item in ignored if item.strip()))

    additional = normalized.get("additional_categories", DEFAULT_ADDITIONAL_CATEGORIES)
    if not isinstance(additional, list):
        raise ValueError("additional_categories must be a list.")
    seen_ids = {"keyframes", "video"}
    seen_folders = {keyframe_folder.casefold(), video_folder.casefold()}
    normalized_categories = []
    for index, raw in enumerate(additional):
        if not isinstance(raw, dict):
            raise ValueError(f"additional_categories[{index}] must be an object.")
        raw_id = raw.get("id")
        category_id = raw_id.strip().casefold() if isinstance(raw_id, str) else ""
        folder = _valid_single_folder_name(raw.get("folder"))
        media_type = raw.get("media_type")
        media_type = media_type.strip().casefold() if isinstance(media_type, str) else ""
        suffix = raw.get("type_suffix")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", category_id):
            raise ValueError(f"Invalid additional_categories[{index}].id.")
        if not folder:
            raise ValueError(f"Invalid additional_categories[{index}].folder.")
        if media_type not in {MEDIA_TYPE_IMAGE, MEDIA_TYPE_VIDEO}:
            raise ValueError(f"Invalid additional_categories[{index}].media_type.")
        try:
            suffix = _validate_filename_component(
                suffix, f"additional_categories[{index}].type_suffix"
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid additional_categories[{index}].type_suffix."
            ) from exc
        if category_id in seen_ids:
            raise ValueError(f"Duplicate category id: {category_id}.")
        if folder.casefold() in seen_folders:
            raise ValueError(f"Duplicate category folder: {folder}.")
        normalized_categories.append(
            {
                "id": category_id,
                "folder": folder,
                "media_type": media_type,
                "type_suffix": suffix,
            }
        )
        seen_ids.add(category_id)
        seen_folders.add(folder.casefold())
    normalized["additional_categories"] = normalized_categories
    return normalized


def _get_settings_snapshot_unlocked() -> dict:
    raw = read_json_file(SETTINGS_FILE, {}) if SETTINGS_FILE else {}
    if not isinstance(raw, dict):
        raw = {}
    merged = default_settings()
    merged.update(raw)
    # JSON round-tripping gives callers a deep copy without leaking mutable
    # default category/extension lists into process globals.
    return json.loads(json.dumps(merged))


def get_settings_snapshot() -> dict:
    """Return a complete, detached copy of settings under the coordinator."""
    with config_store_lock():
        return _get_settings_snapshot_unlocked()


def _normalize_portable_machine_state(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("machine_state must be a JSON object.")
    projects = data.get("projects", {})
    project_modes = data.get("project_modes", {})
    ignored = data.get("ignored_folders", [])
    if not isinstance(projects, dict):
        raise ValueError("projects must be a JSON object.")
    if not isinstance(project_modes, dict):
        raise ValueError("project_modes must be a JSON object.")
    clean_projects = {}
    for name, path in projects.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(path, str) or not path.strip():
            raise ValueError("projects must map non-empty names to non-empty paths.")
        clean_projects[name.strip()] = os.path.normpath(path.strip())
    clean_modes = {}
    for name, mode in project_modes.items():
        clean_name = name.strip() if isinstance(name, str) else ""
        clean_mode = mode.strip().casefold() if isinstance(mode, str) else ""
        if clean_name not in clean_projects:
            continue
        if clean_mode not in PROJECT_LAYOUTS:
            raise ValueError(
                "project_modes values must be 'sequences' or 'shots'."
            )
        clean_modes[clean_name] = clean_mode
    if not isinstance(ignored, list) or any(not isinstance(item, str) for item in ignored):
        raise ValueError("ignored_folders must be a list of strings.")
    clean_ignored = []
    seen = set()
    for item in ignored:
        if not item.strip():
            continue
        path = os.path.normpath(item.strip())
        key = os.path.normcase(path)
        if key not in seen:
            clean_ignored.append(path)
            seen.add(key)
    return {
        "projects": clean_projects,
        "project_modes": clean_modes,
        "ignored_folders": clean_ignored,
    }


def _utc_timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def get_app_version() -> str:
    """Read the embedded/source version without importing packaging modules."""
    roots = []
    embedded_root = getattr(sys, "_MEIPASS", "")
    if embedded_root:
        roots.append(embedded_root)
    roots.extend((APP_DIR, os.path.dirname(os.path.abspath(__file__))))
    seen = set()
    for root in roots:
        path = os.path.normpath(os.path.join(root, "version.json"))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        payload = read_json_file(path, {})
        version = payload.get("version") if isinstance(payload, dict) else ""
        if isinstance(version, str) and version.strip():
            return version.strip()
    return ""


def _canonical_bundle(settings: dict, machine_state: dict) -> dict:
    return {
        "schema": CONFIG_BUNDLE_SCHEMA,
        "schema_version": CONFIG_BUNDLE_SCHEMA_VERSION,
        "app_version": get_app_version(),
        "exported_at": _utc_timestamp(),
        "settings": _normalize_settings_snapshot(settings),
        "machine_state": _normalize_portable_machine_state(machine_state),
    }


def _parse_config_bundle(
    bundle: dict,
    *,
    current_settings: dict,
    current_machine_state: dict,
) -> tuple[dict, dict]:
    if not isinstance(bundle, dict):
        raise ValueError("Configuration must be a JSON object.")
    transient_keys = {"cache", "history"}.intersection(bundle)
    if transient_keys:
        raise ValueError(
            "Portable configuration cannot contain transient state: "
            + ", ".join(sorted(transient_keys))
            + "."
        )
    schema = bundle.get("schema")
    if schema is not None and schema != CONFIG_BUNDLE_SCHEMA:
        raise ValueError(f"Unsupported configuration schema: {schema!r}.")
    if schema is not None and {
        "projects", "project_modes", "ignored_folders"
    }.intersection(bundle):
        raise ValueError(
            "Portable bundle machine state must be nested under "
            "'machine_state'."
        )
    schema_version = bundle.get("schema_version")
    if schema is not None and schema_version != CONFIG_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported configuration schema_version: {schema_version!r}.")

    has_settings_object = "settings" in bundle
    is_raw_settings, is_machine_state_fragment = _top_level_bundle_kinds(
        bundle
    )

    if has_settings_object:
        settings = bundle.get("settings")
    elif is_raw_settings:
        # A plain settings.json is also a supported import source.
        settings = bundle
    else:
        settings = current_settings

    if "machine_state" in bundle:
        machine_state = bundle.get("machine_state")
    elif is_machine_state_fragment or (
        not is_raw_settings
        and {"projects", "project_modes", "ignored_folders"}.intersection(bundle)
    ):
        # UI/legacy short form. A raw settings.json also has ignored_folders,
        # so it must never enter this branch.
        machine_state = {
            "projects": bundle.get("projects", {}),
            "project_modes": bundle.get("project_modes", {}),
            "ignored_folders": bundle.get("ignored_folders", []),
        }
    else:
        machine_state = current_machine_state
    return _normalize_settings_snapshot(settings), _normalize_portable_machine_state(machine_state)


def _top_level_bundle_kinds(bundle: dict) -> tuple[bool, bool]:
    """Classify raw settings versus the legacy machine-state short form.

    ``ignored_folders`` exists in both schemas. A top-level ``projects`` key is
    therefore the discriminator for a machine-state fragment. Mixing that
    fragment with other raw settings keys is rejected instead of silently
    applying data to the wrong store.
    """
    if not isinstance(bundle, dict) or "settings" in bundle:
        return False, False
    keys = set(bundle)
    settings_keys = set(default_settings())
    clear_settings_keys = settings_keys - {"ignored_folders"}
    has_projects = "projects" in bundle
    if has_projects and keys.intersection(clear_settings_keys):
        raise ValueError(
            "Ambiguous top-level configuration: put settings under 'settings' "
            "and projects/ignored_folders under 'machine_state'."
        )
    is_machine_state_fragment = has_projects
    is_raw_settings = (
        not is_machine_state_fragment and bool(keys.intersection(settings_keys))
    )
    return is_raw_settings, is_machine_state_fragment


def _bundle_write_intent(bundle: dict) -> tuple[bool, bool]:
    """Return which managed files are explicitly represented by a payload."""
    if not isinstance(bundle, dict):
        return False, False
    is_raw_settings, is_machine_state_fragment = _top_level_bundle_kinds(
        bundle
    )
    write_settings = "settings" in bundle or is_raw_settings
    write_machine_state = "machine_state" in bundle or (
        (is_machine_state_fragment or not is_raw_settings)
        and {"projects", "project_modes", "ignored_folders"}.intersection(bundle)
    )
    return write_settings, write_machine_state


def _bundle_lock_path() -> str:
    expected_settings = (
        os.path.join(CONFIG_DIR, "settings.json") if CONFIG_DIR else ""
    )
    expected_projects = (
        os.path.join(STATE_DIR, "Projects") if STATE_DIR else ""
    )
    # Tests, portable integrations and future storage relocation may override
    # one managed path without rewriting every module constant. Keep the lock
    # beside that overridden store instead of accidentally touching the user's
    # real AppData.
    if SETTINGS_FILE and os.path.normcase(os.path.normpath(SETTINGS_FILE)) != os.path.normcase(
        os.path.normpath(expected_settings)
    ):
        return os.path.join(os.path.dirname(SETTINGS_FILE), "config-bundle.lock")
    if PROJECTS_DIR and os.path.normcase(os.path.normpath(PROJECTS_DIR)) != os.path.normcase(
        os.path.normpath(expected_projects)
    ):
        return os.path.join(PROJECTS_DIR, "config-bundle.lock")
    if BUNDLE_LOCK_FILE:
        return BUNDLE_LOCK_FILE
    base = STATE_DIR or os.path.dirname(SETTINGS_FILE) or RUNTIME_DIR
    return os.path.join(base, "config-bundle.lock")


def _file_snapshot(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {"exists": False, "content": b""}
    with open(path, "rb") as handle:
        return {"exists": True, "content": handle.read()}


def _snapshot_json(snapshot: dict, default):
    if not snapshot.get("exists"):
        return default
    try:
        return json.loads(snapshot.get("content", b"").decode("utf-8"))
    except Exception:
        return default


def _raw_backup_payload(settings_snapshot: dict, state_snapshot: dict) -> dict:
    raw_settings = _snapshot_json(settings_snapshot, None)
    raw_state = _snapshot_json(state_snapshot, None)
    return {
        "schema": CONFIG_BUNDLE_SCHEMA,
        "schema_version": CONFIG_BUNDLE_SCHEMA_VERSION,
        "app_version": get_app_version(),
        "backup_kind": "raw-pre-change",
        "exported_at": _utc_timestamp(),
        # Parsed mirrors keep healthy backups human-readable. raw_files always
        # preserve exact bytes so malformed JSON remains repairable/recoverable.
        "settings": raw_settings if isinstance(raw_settings, dict) else None,
        "machine_state": raw_state if isinstance(raw_state, dict) else None,
        "raw_files": {
            "settings": {
                "exists": bool(settings_snapshot.get("exists")),
                "content_base64": base64.b64encode(
                    settings_snapshot.get("content", b"")
                ).decode("ascii"),
            },
            "machine_state": {
                "exists": bool(state_snapshot.get("exists")),
                "content_base64": base64.b64encode(
                    state_snapshot.get("content", b"")
                ).decode("ascii"),
            },
        },
    }


def _backup_current_config(
    settings_snapshot: dict | None = None,
    state_snapshot: dict | None = None,
) -> str:
    settings_snapshot = settings_snapshot or _file_snapshot(SETTINGS_FILE)
    state_snapshot = state_snapshot or _file_snapshot(get_projects_file())
    if not settings_snapshot["exists"] and not state_snapshot["exists"]:
        return ""
    backup_dir = BACKUPS_DIR or os.path.join(CONFIG_DIR, "Backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(backup_dir, f"config-backup-{stamp}.json")
    atomic_write_json(path, _raw_backup_payload(settings_snapshot, state_snapshot))
    return path


def _restore_file_snapshot(path: str, snapshot: dict) -> None:
    with json_file_lock(path):
        if not snapshot.get("exists"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temporary = f"{path}.{os.getpid()}.{time.time_ns()}.restore"
        try:
            with open(temporary, "xb") as handle:
                handle.write(snapshot.get("content", b""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass


def _current_machine_state_unlocked(machine_id: str | None = None) -> dict:
    path = get_projects_file(machine_id)
    state, _changed, error, _snapshot = _read_machine_state_snapshot(path)
    if error:
        record_runtime_warning(
            f"Invalid per-machine state at '{path}' was preserved and not "
            f"applied: {error}"
        )
        return empty_machine_state()
    return state


def save_config_bundle(bundle: dict, *, create_backup: bool = True) -> dict:
    """Apply settings and per-machine state as one rollback-safe operation."""
    state_path = get_projects_file()
    backup_path = ""

    with config_store_lock():
        settings_snapshot = _file_snapshot(SETTINGS_FILE)
        state_snapshot = _file_snapshot(state_path)
        write_settings, write_machine_state = _bundle_write_intent(bundle)
        if not write_settings and not write_machine_state:
            raise ValueError(
                "Configuration payload contains neither settings nor machine_state."
            )
        settings, machine_state = _parse_config_bundle(
            bundle,
            current_settings=_get_settings_snapshot_unlocked(),
            current_machine_state=_current_machine_state_unlocked(),
        )
        if create_backup:
            backup_path = _backup_current_config(
                settings_snapshot, state_snapshot
            )
        try:
            if write_settings:
                atomic_write_json(SETTINGS_FILE, settings)
            if write_machine_state:
                atomic_write_json(state_path, machine_state)
        except Exception as exc:
            rollback_errors = []
            rollback_targets = []
            if write_settings:
                rollback_targets.append((SETTINGS_FILE, settings_snapshot))
            if write_machine_state:
                rollback_targets.append((state_path, state_snapshot))
            for path, snapshot in rollback_targets:
                try:
                    _restore_file_snapshot(path, snapshot)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    "Configuration write failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise

    load_settings()
    return {
        "settings": get_settings_snapshot(),
        "projects": dict(machine_state["projects"]),
        "project_modes": dict(machine_state["project_modes"]),
        "ignored_folders": list(machine_state["ignored_folders"]),
        "backup_path": backup_path,
        "backup_dir": os.path.dirname(backup_path) if backup_path else "",
    }


def save_settings_snapshot(settings: dict) -> dict:
    """Persist settings while preserving this machine's projects and ignores."""
    # A raw settings payload is deliberately parsed inside the coordinator,
    # where current machine state is read and preserved.
    summary = save_config_bundle(settings)
    return summary["settings"]


def save_machine_state(
    projects: dict,
    ignored_folders: list[str],
    project_modes: dict | None = None,
) -> dict:
    """Persist all portable machine-state fields in one operation."""
    summary = save_config_bundle(
        {
            "machine_state": {
                "projects": projects,
                "project_modes": project_modes or {},
                "ignored_folders": ignored_folders,
            },
        }
    )
    return {
        "projects": summary["projects"],
        "project_modes": summary["project_modes"],
        "ignored_folders": summary["ignored_folders"],
    }


def validate_config_bundle(bundle: dict) -> dict:
    """Strictly validate a UI/import payload without writing any files."""
    write_settings, write_machine_state = _bundle_write_intent(bundle)
    if write_settings and write_machine_state:
        # Complete Settings-dialog bundles need no persisted fallback values.
        # Keep this path pure so Apply/Export validation can never wait on the
        # cross-process store lock in the Qt GUI thread.
        settings, machine_state = _parse_config_bundle(
            bundle,
            current_settings=default_settings(),
            current_machine_state=empty_machine_state(),
        )
        return {
            "settings": settings,
            "machine_state": machine_state,
        }
    with config_store_lock():
        settings, machine_state = _parse_config_bundle(
            bundle,
            current_settings=_get_settings_snapshot_unlocked(),
            current_machine_state=_current_machine_state_unlocked(),
        )
        return {
            "settings": settings,
            "machine_state": machine_state,
        }


def get_config_bundle(
    *,
    include_cache: bool = False,
    include_history: bool = False,
) -> dict:
    """Return one consistent portable settings + machine-state snapshot."""
    if include_cache or include_history:
        raise ValueError(
            "Cache and history are transient and cannot be included in a "
            "portable configuration."
        )
    with config_store_lock():
        bundle = _canonical_bundle(
            _get_settings_snapshot_unlocked(),
            _current_machine_state_unlocked(),
        )
        return bundle


def export_config_bundle(
    destination_path: str,
    bundle: dict | None = None,
    *,
    include_cache: bool = False,
    include_history: bool = False,
) -> str:
    """Export one portable JSON file; transient state is excluded by default."""
    if include_cache or include_history:
        raise ValueError(
            "Cache and history are transient and cannot be exported."
        )
    if not destination_path:
        raise ValueError("An export destination is required.")
    destination = os.path.abspath(os.path.expanduser(str(destination_path)))
    managed_paths = {os.path.normcase(path) for path in (SETTINGS_FILE, get_projects_file()) if path}
    if os.path.normcase(destination) in managed_paths:
        raise ValueError("Export destination cannot replace a managed runtime file.")
    if bundle is None:
        export_payload = get_config_bundle()
    else:
        with config_store_lock():
            settings, machine_state = _parse_config_bundle(
                bundle,
                current_settings=_get_settings_snapshot_unlocked(),
                current_machine_state=_current_machine_state_unlocked(),
            )
            export_payload = _canonical_bundle(settings, machine_state)
    atomic_write_json(destination, export_payload)
    return destination


def inspect_config_import(source_path: str) -> dict:
    """Classify and validate an import source without touching local state."""
    source = os.path.abspath(os.path.expanduser(str(source_path)))
    if os.path.isdir(source):
        legacy_dir = _find_legacy_config_dir(source)
        settings, settings_present = _read_legacy_settings(legacy_dir)
        state, state_present = _read_legacy_machine_state(legacy_dir)
        if not settings_present and not state_present:
            raise ValueError(
                f"No supported settings or machine state was found in '{legacy_dir}'."
            )
        return {
            "kind": "legacy_directory",
            "source_path": source,
            "settings_present": settings_present,
            "settings_count": len(settings) if settings_present else 0,
            "machine_state_present": state_present,
            "projects_count": len(state["projects"]) if state_present else 0,
            "ignored_count": (
                len(state["ignored_folders"]) if state_present else 0
            ),
            "raw_settings_preserves_machine_state": False,
            "legacy_path": legacy_dir,
        }
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    with open(source, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be a JSON object.")
    if {"cache", "history"}.intersection(payload):
        raise ValueError(
            "Portable configuration cannot contain cache or history."
        )

    schema = payload.get("schema")
    schema_version = payload.get("schema_version")
    if schema is not None:
        if schema != CONFIG_BUNDLE_SCHEMA:
            raise ValueError(f"Unsupported configuration schema: {schema!r}.")
        if schema_version != CONFIG_BUNDLE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported configuration schema_version: {schema_version!r}."
            )
        if {"projects", "project_modes", "ignored_folders"}.intersection(payload):
            raise ValueError(
                "Portable bundle machine state must be nested under "
                "'machine_state'."
            )
        kind = "portable_bundle"
        settings_payload = payload.get("settings")
        if not isinstance(settings_payload, dict):
            raise ValueError("Portable bundle is missing settings.")
        settings = _normalize_settings_snapshot(settings_payload)
        state = _normalize_portable_machine_state(
            payload.get("machine_state", empty_machine_state())
        )
        preserves_state = "machine_state" not in payload
    else:
        is_raw_settings, is_machine_state_fragment = _top_level_bundle_kinds(
            payload
        )
    if schema is None and is_raw_settings:
        kind = "raw_settings"
        settings = _normalize_settings_snapshot(payload)
        state = empty_machine_state()
        preserves_state = True
    elif schema is None:
        kind = "config_fragment"
        settings_payload = payload.get("settings")
        settings = (
            _normalize_settings_snapshot(settings_payload)
            if isinstance(settings_payload, dict)
            else {}
        )
        if is_machine_state_fragment:
            state_payload = {
                "projects": payload.get("projects", {}),
                "project_modes": payload.get("project_modes", {}),
                "ignored_folders": payload.get("ignored_folders", []),
            }
        else:
            state_payload = payload.get(
                "machine_state",
                {
                    "projects": payload.get("projects", {}),
                    "project_modes": payload.get("project_modes", {}),
                    "ignored_folders": payload.get("ignored_folders", []),
                },
            )
        state = _normalize_portable_machine_state(state_payload)
        preserves_state = not (
            "machine_state" in payload
            or "projects" in payload
            or "project_modes" in payload
            or "ignored_folders" in payload
        )

    ignored_count = (
        len(settings.get("ignored_folders", []))
        if kind == "raw_settings"
        else len(state["ignored_folders"])
    )
    settings_present = kind == "raw_settings" or "settings" in payload
    machine_state_present = (
        "machine_state" in payload
        or "projects" in payload
        or "project_modes" in payload
        or (kind != "raw_settings" and "ignored_folders" in payload)
    )
    return {
        "kind": kind,
        "source_path": source,
        "settings_present": settings_present,
        "settings_count": len(settings),
        "machine_state_present": machine_state_present,
        "projects_count": len(state["projects"]),
        "ignored_count": ignored_count,
        "raw_settings_preserves_machine_state": preserves_state,
        "legacy_path": "",
    }


def import_config_bundle(
    source_path: str,
    *,
    include_runtime_state: bool = False,
    include_ffmpeg: bool = False,
) -> dict:
    """Import a portable bundle, raw settings.json, or legacy config folder."""
    if include_runtime_state:
        raise ValueError(
            "Cache and history are transient and cannot be imported."
        )
    source = os.path.abspath(os.path.expanduser(str(source_path)))
    if os.path.isdir(source):
        return import_legacy_config(
            source, overwrite=True, include_ffmpeg=include_ffmpeg
        )
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    with open(source, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    summary = save_config_bundle(payload)
    summary["source_path"] = source
    summary["imported_cache"] = False
    summary["imported_history"] = False
    return summary

def copy_tree_missing(source_dir: str, target_dir: str):
    """Copy old runtime files into a new layout without replacing destinations."""
    runtime_copy_tree_missing(source_dir, target_dir)


def safe_makedirs(path: str, description: str) -> bool:
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        record_runtime_warning(f"Failed to create {description} at '{path}': {e}")
        return False

def migrate_legacy_runtime_layout(source_path: str | None = None, *, include_ffmpeg: bool = False):
    """Compatibility wrapper for the explicit legacy importer.

    Automatic server migration was intentionally removed for standalone mode.
    Callers must provide the old root or ``_config`` directory explicitly.
    """
    if not source_path:
        return {"imported": False, "reason": "source path is required"}
    return import_legacy_config(source_path, overwrite=False, include_ffmpeg=include_ffmpeg)

def is_ignored(full_path: str, name: str) -> bool:
    """Helper function to check if a directory should be hidden.
    Matches names directly or checks absolute paths.
    """
    if name.startswith('.'):
        return True
    if name.casefold() == "_shotcode" or name.casefold() in {item.casefold() for item in IGNORED_NAMES}:
        return True
    
    # Normalize path for comparison
    normalized = os.path.normpath(full_path)
    return normalized in IGNORED_PATHS

# Media Type Identifiers
TYPE_VID = "VID"
TYPE_IMG = "IMG"

def get_type_suffix(file_type: str, category=None) -> str:
    resolved = get_media_category(category)
    if resolved is None and isinstance(file_type, MediaCategory):
        resolved = file_type
    if resolved is None and file_type not in {TYPE_IMG, TYPE_VID}:
        resolved = get_media_category(file_type)
    if resolved is not None:
        return resolved.type_suffix
    if file_type == TYPE_VID:
        return VIDEO_TYPE_SUFFIX
    if file_type == TYPE_IMG:
        return IMAGE_TYPE_SUFFIX
    return str(file_type)

def setup_directories():
    """Create the per-user standalone runtime without replacing user data."""
    if SERVER_ROOT_ERROR:
        raise RuntimeError(SERVER_ROOT_ERROR)
    if not RUNTIME_DIR:
        raise RuntimeError(
            "MediaRenamer local runtime is unavailable because LOCALAPPDATA "
            "could not be resolved."
        )

    safe_makedirs(RUNTIME_DIR, "standalone runtime directory")
    safe_makedirs(CONFIG_DIR, "local config directory")
    safe_makedirs(STATE_DIR, "local state directory")
    safe_makedirs(PROJECTS_DIR, "local projects directory")
    safe_makedirs(CACHE_DIR, "local cache directory")
    safe_makedirs(HISTORY_DIR, "local history directory")
    safe_makedirs(LOGS_DIR, "local logs directory")
    safe_makedirs(TOOLS_DIR, "local tools directory")
    safe_makedirs(DIAGNOSTICS_DIR, "local diagnostics directory")
    safe_makedirs(BACKUPS_DIR, "local config backup directory")

    try:
        materialize_legal_materials()
    except Exception as e:
        record_runtime_warning(
            f"Failed to make bundled legal materials user-accessible: {e}"
        )

    try:
        seed_machine_runtime_files()
    except Exception as e:
        record_runtime_warning(f"Failed to seed per-machine cache/history files: {e}")
    try:
        with config_store_lock():
            atomic_write_json_if_missing(get_projects_file(), empty_machine_state())
    except Exception as e:
        record_runtime_warning(f"Failed to create per-machine project file '{get_projects_file()}': {e}")

    # Create a reference projects.json from legacy projects.txt only when missing.
    # Existing config/support files are never overwritten or deleted here.
    if os.path.exists(PROJECTS_FILE_LEGACY) and not os.path.exists(PROJECTS_FILE):
        try:
            legacy_projects = get_projects_from_txt()
            with config_store_lock():
                atomic_write_json(PROJECTS_FILE, legacy_projects)
        except Exception as e:
            record_runtime_warning(f"Failed to create legacy reference projects.json: {e}")

    settings_data = default_settings()

    # Read legacy config files only to seed a brand-new settings.json.
    # Existing legacy files are retained for safety.
    if os.path.exists(EXTENSIONS_FILE):
        try:
            with open(EXTENSIONS_FILE, 'r', encoding='utf-8') as f:
                old_ext = json.load(f)
                settings_data["video_extensions"] = old_ext.get("video_extensions", DEFAULT_EXT_VIDEO)
                settings_data["image_extensions"] = old_ext.get("image_extensions", DEFAULT_EXT_IMAGE)
        except Exception as e:
            print(f"Error migrating extensions: {e}")
            
    if os.path.exists(FOLDERS_FILE):
        try:
            with open(FOLDERS_FILE, 'r', encoding='utf-8') as f:
                old_fold = json.load(f)
                settings_data["keyframe_folder"] = old_fold.get("keyframe_folder", "KEYFRAMES")
                settings_data["video_folder"] = old_fold.get("video_folder", "VIDEO")
        except Exception as e:
            print(f"Error migrating folders: {e}")
            
    try:
        with config_store_lock():
            if not os.path.exists(SETTINGS_FILE):
                atomic_write_json(
                    SETTINGS_FILE, _normalize_settings_snapshot(settings_data)
                )
            else:
                _migrate_settings_defaults_unlocked()
    except Exception as e:
        record_runtime_warning(
            f"Failed to create/migrate settings.json at '{SETTINGS_FILE}': {e}"
        )
            
    load_settings()

def load_settings():
    global DIR_KEYFRAME, DIR_VIDEO, SCENE_PREFIX, SHOT_PREFIX, TARGET_PREFIX
    global SUBVERSION_ENABLED, SKIP_SEQUENCE, FILENAME_TEMPLATE
    global IMAGE_TYPE_SUFFIX, VIDEO_TYPE_SUFFIX, APP_DISPLAY_NAME
    ALLOWED_EXT_VIDEO.clear()
    ALLOWED_EXT_IMAGE.clear()
    ALLOWED_EXTENSIONS.clear()
    IGNORED_FOLDERS.clear()
    IGNORED_NAMES.clear()
    IGNORED_PATHS.clear()
    def read_current_settings():
        snapshot = _file_snapshot(SETTINGS_FILE)
        error = ""
        if snapshot["exists"]:
            try:
                raw_settings = json.loads(snapshot["content"].decode("utf-8"))
                if not isinstance(raw_settings, dict):
                    raise ValueError("settings root must be a JSON object")
                data = _normalize_settings_snapshot(raw_settings)
            except Exception as exc:
                error = str(exc)
                data = _normalize_settings_snapshot(default_settings())
        else:
            data = _normalize_settings_snapshot(default_settings())
        return data, list(get_machine_ignored_folders()), error

    validation_error = ""
    try:
        # Writes use atomic replacement. A busy writer should not make an
        # already saved project list disappear when the Browser starts.
        with config_store_lock(timeout=1.0):
            data, machine_ignored, validation_error = read_current_settings()
    except TimeoutError:
        try:
            data, machine_ignored, validation_error = read_current_settings()
        except Exception as exc:
            validation_error = str(exc)
            data = _normalize_settings_snapshot(default_settings())
            machine_ignored = []
    except Exception as exc:
        validation_error = str(exc)
        data = _normalize_settings_snapshot(default_settings())
        machine_ignored = []

    if validation_error:
        record_runtime_warning(
            f"Unsafe or invalid settings.json at '{SETTINGS_FILE}' was not applied: "
            f"{validation_error}"
        )

    APP_DISPLAY_NAME = data["app_name"]
    DIR_KEYFRAME = data["keyframe_folder"]
    DIR_VIDEO = data["video_folder"]
    IMAGE_TYPE_SUFFIX = data["image_type_suffix"]
    VIDEO_TYPE_SUFFIX = data["video_type_suffix"]
    SCENE_PREFIX = data["scene_prefix"]
    SHOT_PREFIX = data["shot_prefix"]
    TARGET_PREFIX = data["target_prefix"]
    SUBVERSION_ENABLED = parse_yes_no(data["subversion_enabled"], True)
    SKIP_SEQUENCE = parse_yes_no(data["skip_sequence"], False)
    FILENAME_TEMPLATE = data["filename_template"]
    ALLOWED_EXT_VIDEO.extend(data["video_extensions"])
    ALLOWED_EXT_IMAGE.extend(data["image_extensions"])
    IGNORED_FOLDERS.extend(data["ignored_folders"])
    IGNORED_FOLDERS.extend(machine_ignored)
    for item in IGNORED_FOLDERS:
        if "\\" in item or "/" in item:
            IGNORED_PATHS.add(os.path.normpath(item))
        else:
            IGNORED_NAMES.add(item)
    ALLOWED_EXTENSIONS.extend(ALLOWED_EXT_VIDEO + ALLOWED_EXT_IMAGE)
    _set_media_categories(data["additional_categories"])

def add_ignored_folder(folder_path: str):
    """Add a folder to this machine's local hidden-folder state."""
    if not isinstance(folder_path, str) or not folder_path.strip():
        raise ValueError("Ignored folder path cannot be empty.")
    normalized_path = os.path.normpath(folder_path.strip())
    try:
        with config_store_lock():
            def updater(state):
                folders = state["ignored_folders"]
                comparisons = {
                    os.path.normcase(os.path.normpath(str(item)))
                    for item in folders
                }
                if os.path.normcase(normalized_path) not in comparisons:
                    folders.append(normalized_path)
                return state

            _mutate_machine_state_unlocked(get_projects_file(), updater)
            if normalized_path not in IGNORED_FOLDERS:
                IGNORED_FOLDERS.append(normalized_path)
                if "\\" in normalized_path or "/" in normalized_path:
                    IGNORED_PATHS.add(normalized_path)
                else:
                    IGNORED_NAMES.add(normalized_path)
    except Exception as e:
        record_runtime_warning(f"Error saving ignored folder '{folder_path}' to per-machine state: {e}")
        raise


def restore_ignored_folder(folder_path: str, machine_id: str | None = None) -> bool:
    """Remove one path from this machine's ignore list, even if it no longer exists."""
    normalized_path = os.path.normpath(str(folder_path))
    comparison = os.path.normcase(normalized_path)
    removed = False

    def updater(state):
        nonlocal removed
        kept = []
        for entry in state["ignored_folders"]:
            if os.path.normcase(os.path.normpath(str(entry))) == comparison:
                removed = True
            else:
                kept.append(entry)
        state["ignored_folders"] = kept
        return state

    with config_store_lock():
        _mutate_machine_state_unlocked(get_projects_file(machine_id), updater)
        if removed and (machine_id is None or get_machine_id(machine_id) == get_machine_id()):
            IGNORED_FOLDERS[:] = [
                entry for entry in IGNORED_FOLDERS
                if os.path.normcase(os.path.normpath(str(entry))) != comparison
            ]
            IGNORED_PATHS.discard(normalized_path)
            IGNORED_NAMES.discard(normalized_path)
            for entry in list(IGNORED_PATHS):
                if os.path.normcase(os.path.normpath(entry)) == comparison:
                    IGNORED_PATHS.discard(entry)
    return removed

def load_cache() -> dict:
    cache_file = get_cache_file()
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            record_runtime_warning(f"Failed to load cache file '{cache_file}': {e}")
            return {}
    return {}

def save_cache(cache_data: dict):
    try:
        atomic_write_json(get_cache_file(), cache_data)
    except Exception as e:
        record_runtime_warning(f"Failed to save cache to '{get_cache_file()}': {e}")

def get_projects_from_txt():
    """Legacy parser for projects.txt"""
    projects = {}
    if not os.path.exists(PROJECTS_FILE_LEGACY):
        return projects
    try:
        with open(PROJECTS_FILE_LEGACY, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                norm_path = os.path.normpath(line)
                if os.path.isdir(norm_path):
                    search_path = norm_path.replace('/', '\\')
                    ai_marker = "\\04_ONLINE\\AI"
                    if ai_marker in search_path.upper():
                        idx = int(search_path.upper().find(ai_marker))
                        pre_ai = search_path[:idx]
                        post_ai = search_path[idx + len(ai_marker):]
                        proj_base = os.path.basename(pre_ai)
                        sub_proj = post_ai.strip('\\')
                        if sub_proj:
                            project_name = f"{proj_base}/{sub_proj}"
                        else:
                            project_name = proj_base
                    else:
                        project_name = os.path.basename(norm_path)
                    projects[project_name] = norm_path
    except Exception:
        pass
    return projects

def get_machine_id(hostname: str | None = None) -> str:
    """Return a filesystem-safe machine id for per-workstation project lists."""
    return runtime_get_machine_id(hostname)

def empty_machine_state() -> dict:
    return runtime_empty_machine_state()

def normalize_machine_state(data) -> tuple[dict, bool]:
    """Normalize current and legacy per-machine project file shapes."""
    if not isinstance(data, dict):
        return empty_machine_state(), True

    if {"projects", "project_modes", "ignored_folders"}.intersection(data):
        projects = data.get("projects", {})
        project_modes = data.get("project_modes", {})
        ignored_folders = data.get("ignored_folders", [])
        state = dict(data)
        state["projects"] = projects if isinstance(projects, dict) else {}
        state["project_modes"] = (
            project_modes if isinstance(project_modes, dict) else {}
        )
        state["ignored_folders"] = ignored_folders if isinstance(ignored_folders, list) else []
        return state, state != data

    # 0.19.x stored the project mapping directly at the file root.
    return {
        "projects": data,
        "project_modes": {},
        "ignored_folders": [],
    }, True


def _read_machine_state_snapshot(
    path: str,
) -> tuple[dict, bool, str, dict]:
    """Strictly decode machine state without destroying a corrupt source.

    The returned snapshot always contains the exact original bytes. Callers may
    use the empty state for a safe read-only fallback, but a mutation must first
    preserve those bytes and explicitly repair or replace the file.
    """
    snapshot = _file_snapshot(path)
    if not snapshot["exists"]:
        return empty_machine_state(), False, "", snapshot

    try:
        raw = json.loads(snapshot["content"].decode("utf-8"))
    except Exception as exc:
        return (
            empty_machine_state(),
            False,
            f"malformed JSON ({exc})",
            snapshot,
        )
    if not isinstance(raw, dict):
        return (
            empty_machine_state(),
            False,
            "the JSON root must be an object",
            snapshot,
        )

    try:
        if {"projects", "project_modes", "ignored_folders"}.intersection(raw):
            normalized = _normalize_portable_machine_state(raw)
            return normalized, normalized != raw, "", snapshot

        # 0.19.x stored the project mapping directly at the file root.
        normalized = _normalize_portable_machine_state(
            {"projects": raw, "ignored_folders": []}
        )
        return normalized, True, "", snapshot
    except ValueError as exc:
        return empty_machine_state(), False, str(exc), snapshot


def _mutate_machine_state_unlocked(path: str, updater):
    """Read-modify-write a healthy machine-state file under its own lock."""
    with json_file_lock(path):
        state, _changed, error, snapshot = _read_machine_state_snapshot(path)
        if error:
            backup_path = _backup_current_config(
                _file_snapshot(SETTINGS_FILE),
                snapshot,
            )
            message = (
                f"Refusing to overwrite invalid per-machine state at '{path}': "
                f"{error}. The original bytes were preserved"
            )
            if backup_path:
                message += f" in '{backup_path}'"
            message += "."
            record_runtime_warning(message)
            raise ValueError(message)

        detached = json.loads(json.dumps(state))
        updated = _normalize_portable_machine_state(updater(detached))
        _write_json_unlocked(path, updated)
        return updated


def get_projects_file(machine_id: str | None = None) -> str:
    return os.path.join(PROJECTS_DIR, f"{get_machine_id(machine_id)}.json")

def get_machine_state(machine_id: str | None = None) -> dict:
    machine_file = get_projects_file(machine_id)
    state, changed, error, snapshot = _read_machine_state_snapshot(machine_file)
    if error:
        record_runtime_warning(
            f"Invalid per-machine state at '{machine_file}' was preserved "
            f"and not applied: {error}"
        )
        return empty_machine_state()
    if snapshot["exists"] and not changed:
        return state

    # Only creation/migration needs the coordinator. The normal read path
    # observes one complete file because every writer replaces it atomically.
    try:
        with config_store_lock(timeout=1.0):
            state, changed, error, snapshot = _read_machine_state_snapshot(machine_file)
            if error:
                record_runtime_warning(
                    f"Invalid per-machine state at '{machine_file}' was preserved "
                    f"and not applied: {error}"
                )
                return empty_machine_state()
            if not snapshot["exists"] or changed:
                atomic_write_json(machine_file, state)
    except TimeoutError:
        # An existing legacy file can still be read in normalized form; its
        # on-disk migration waits until the next successful write.
        return state
    return state

def get_projects(machine_id: str | None = None):
    """Read this machine's project shortcuts from structured local state."""
    try:
        return get_machine_state(machine_id).get("projects", {})
    except Exception as e:
        record_runtime_warning(f"Failed to read per-machine project list '{get_projects_file(machine_id)}': {e}")
        return {}


def get_project_modes(machine_id: str | None = None) -> dict:
    """Read persisted per-project navigation layouts."""
    try:
        return dict(get_machine_state(machine_id).get("project_modes", {}))
    except Exception as exc:
        record_runtime_warning(
            f"Failed to read project layouts '{get_projects_file(machine_id)}': {exc}"
        )
        return {}


def get_machine_ignored_folders(machine_id: str | None = None) -> list:
    return list(get_machine_state(machine_id).get("ignored_folders", []))


def save_projects(
    projects: dict,
    machine_id: str | None = None,
    *,
    project_modes: dict | None = None,
):
    """Safely write this machine's project list and detected layouts."""
    with config_store_lock():
        def updater(state):
            state["projects"] = projects
            modes = (
                project_modes
                if isinstance(project_modes, dict)
                else state.get("project_modes", {})
            )
            state["project_modes"] = {
                name: mode
                for name, mode in modes.items()
                if name in projects and mode in PROJECT_LAYOUTS
            }
            return state

        _mutate_machine_state_unlocked(get_projects_file(machine_id), updater)

def get_cache_file(machine_id: str | None = None) -> str:
    return os.path.join(CACHE_DIR, f"{get_machine_id(machine_id)}.json")

def get_history_file(machine_id: str | None = None) -> str:
    return os.path.join(HISTORY_DIR, f"{get_machine_id(machine_id)}.json")

def seed_machine_runtime_files(machine_id: str | None = None):
    cache_file = get_cache_file(machine_id)
    history_file = get_history_file(machine_id)
    cache_seed = read_json_file(CACHE_FILE, {}) if os.path.exists(CACHE_FILE) else {}
    legacy_history = read_json_file(HISTORY_FILE, {}) if os.path.exists(HISTORY_FILE) else {}
    history_seed = []
    if isinstance(legacy_history, dict):
        history_seed = legacy_history.get(socket.gethostname(), [])
    elif isinstance(legacy_history, list):
        history_seed = legacy_history
    atomic_write_json_if_missing(cache_file, cache_seed if isinstance(cache_seed, dict) else {})
    atomic_write_json_if_missing(history_file, history_seed if isinstance(history_seed, list) else [])


def _find_legacy_config_dir(source_path: str) -> str:
    source = os.path.abspath(os.path.expanduser(str(source_path)))
    candidates = [
        source,
        os.path.join(source, "_config"),
        os.path.join(source, "Config"),
        os.path.join(source, "MediaRenamer", "_config"),
        os.path.join(source, "MediaRenamer", "Config"),
    ]
    markers = ("settings.json", "extensions.json", "folders.json", "projects.json", "projects")
    for candidate in candidates:
        if os.path.isdir(candidate) and any(
            os.path.exists(os.path.join(candidate, marker)) for marker in markers
        ):
            return os.path.normpath(candidate)
    raise ValueError(
        f"No MediaRenamer configuration was found in '{source_path}'. "
        "Select a bundle JSON, settings.json, _config, or Config directory."
    )


def _read_legacy_json_object(path: str, description: str) -> dict | None:
    """Read a present legacy JSON object without hiding corruption."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Legacy {description} is unreadable or malformed: '{path}': {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"Legacy {description} must contain a JSON object: '{path}'."
        )
    return payload


def _read_legacy_settings(config_dir: str) -> tuple[dict | None, bool]:
    settings_path = os.path.join(config_dir, "settings.json")
    settings = _read_legacy_json_object(settings_path, "settings.json")
    extensions = _read_legacy_json_object(
        os.path.join(config_dir, "extensions.json"),
        "extensions.json",
    )
    folders = _read_legacy_json_object(
        os.path.join(config_dir, "folders.json"),
        "folders.json",
    )
    if settings is None and extensions is None and folders is None:
        return None, False

    settings = settings or {}
    merged = default_settings()
    merged.update(settings)

    if isinstance(extensions, dict):
        if "video_extensions" in extensions and "video_extensions" not in settings:
            merged["video_extensions"] = extensions["video_extensions"]
        if "image_extensions" in extensions and "image_extensions" not in settings:
            merged["image_extensions"] = extensions["image_extensions"]

    if isinstance(folders, dict):
        if "keyframe_folder" in folders and "keyframe_folder" not in settings:
            merged["keyframe_folder"] = folders["keyframe_folder"]
        if "video_folder" in folders and "video_folder" not in settings:
            merged["video_folder"] = folders["video_folder"]
    return _normalize_settings_snapshot(merged), True


def _read_legacy_machine_state(config_dir: str) -> tuple[dict | None, bool]:
    projects_dir = os.path.join(config_dir, "projects")
    candidates = [os.path.join(projects_dir, f"{get_machine_id()}.json")]
    if os.path.isdir(projects_dir):
        json_files = sorted(
            os.path.join(projects_dir, name)
            for name in os.listdir(projects_dir)
            if name.lower().endswith(".json")
        )
        if len(json_files) == 1 and json_files[0] not in candidates:
            candidates.append(json_files[0])
    candidates.append(os.path.join(config_dir, "projects.json"))

    for path in candidates:
        if not os.path.isfile(path):
            continue
        payload = _read_legacy_json_object(path, "project state")
        state, _ = normalize_machine_state(payload)
        return _normalize_portable_machine_state(state), True

    projects_txt = os.path.join(config_dir, "projects.txt")
    projects = {}
    if os.path.isfile(projects_txt):
        with open(projects_txt, "r", encoding="utf-8") as handle:
            for line in handle:
                path = line.strip()
                if path and not path.startswith("#"):
                    normalized = os.path.normpath(path)
                    projects[os.path.basename(normalized)] = normalized
        return (
            _normalize_portable_machine_state(
                {"projects": projects, "ignored_folders": []}
            ),
            True,
        )
    return None, False


def import_legacy_config(
    source_path: str,
    *,
    overwrite: bool = False,
    include_ffmpeg: bool = False,
) -> dict:
    """Explicitly import an older ``_config``/server layout into local storage.

    Only settings, projects and ignored folders are migrated. Cache, history,
    logs and FFmpeg are always excluded.
    """
    del include_ffmpeg  # Deprecated compatibility keyword; intentionally ignored.
    config_dir = _find_legacy_config_dir(source_path)
    imported_settings, settings_present = _read_legacy_settings(config_dir)
    imported_state, state_present = _read_legacy_machine_state(config_dir)
    if not settings_present and not state_present:
        raise ValueError(
            f"No supported settings or machine state was found in '{config_dir}'."
        )

    with config_store_lock():
        if not overwrite:
            if settings_present:
                current_settings = _get_settings_snapshot_unlocked()
                try:
                    current_is_default = (
                        _normalize_settings_snapshot(current_settings)
                        == _normalize_settings_snapshot(default_settings())
                    )
                except ValueError:
                    current_is_default = False
                if os.path.exists(SETTINGS_FILE) and not current_is_default:
                    merged_settings = dict(imported_settings)
                    merged_settings.update(current_settings)
                    imported_settings = _normalize_settings_snapshot(
                        merged_settings
                    )

            if state_present:
                current_state = _current_machine_state_unlocked()
                projects = dict(imported_state["projects"])
                projects.update(current_state["projects"])
                ignored = list(current_state["ignored_folders"])
                existing_ignored = {
                    os.path.normcase(item) for item in ignored
                }
                for item in imported_state["ignored_folders"]:
                    if os.path.normcase(item) not in existing_ignored:
                        ignored.append(item)
                        existing_ignored.add(os.path.normcase(item))
                imported_state = {
                    "projects": projects,
                    "ignored_folders": ignored,
                }

        payload = {}
        if settings_present:
            payload["settings"] = imported_settings
        if state_present:
            payload["machine_state"] = imported_state
        summary = save_config_bundle(payload)
    summary["source_path"] = os.path.abspath(source_path)
    summary["legacy_config_dir"] = config_dir
    summary["settings_present"] = settings_present
    summary["machine_state_present"] = state_present
    summary["imported_cache"] = False
    summary["imported_history"] = False
    summary["imported_ffmpeg"] = False
    summary["ffmpeg_path"] = ""
    return summary

def log_action(message, is_error=False):
    """Write a per-user log entry to Logs/YYYY-MM-DD.txt."""
    global LAST_LOG_WRITE_ERROR
    today = datetime.datetime.now()
    log_filename = today.strftime("%Y-%m-%d.txt")
    log_path = os.path.join(LOGS_DIR, log_filename)
    
    timestamp = today.strftime("%H:%M:%S")
    prefix = "[ERROR]" if is_error else "[INFO]"
    hostname = socket.gethostname()
    
    log_line = f"{timestamp} {prefix} [{hostname}] {message}\n"
    print(log_line.strip())
    
    try:
        if not LOGS_DIR:
            raise RuntimeError("LOGS_DIR is not configured.")
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(log_line)
        LAST_LOG_WRITE_ERROR = ""
        return True
    except Exception as e:
        LAST_LOG_WRITE_ERROR = f"Failed to write local log '{log_path}': {e}"
        print(LAST_LOG_WRITE_ERROR)
        diagnostic_path = write_emergency_diagnostic(f"{prefix} {LAST_LOG_WRITE_ERROR} | original: {message}")
        warning = LAST_LOG_WRITE_ERROR
        if diagnostic_path:
            warning = f"{warning} Emergency diagnostic: {diagnostic_path}"
        record_runtime_warning(warning)
        return False

def get_history() -> list:
    history_file = get_history_file()
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            record_runtime_warning(f"Failed to load history file '{history_file}': {e}")
            pass
    return []

def save_history(new_history_item: dict):
    try:
        def updater(data):
            history = data if isinstance(data, list) else []
            history = [
                item for item in history
                if isinstance(item, dict) and item.get('path') != new_history_item.get('path')
            ]
            history.insert(0, new_history_item)
            return history[:5]

        update_json_file(get_history_file(), [], updater)
    except Exception as e:
        record_runtime_warning(f"Failed to save history to '{get_history_file()}': {e}")

def clear_history():
    history_file = get_history_file()
    if os.path.exists(history_file):
        try:
            def updater(data):
                return []

            update_json_file(history_file, [], updater)
        except Exception:
            pass
