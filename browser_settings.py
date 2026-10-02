# SPDX-License-Identifier: GPL-3.0-only
"""Bind the separate Browser process to its own mutable configuration store.

Shared code and an existing FFmpeg binary are reused; desktop configuration is
never written. Only previous Browser installations receive a one-time snapshot
of their formerly shared project shortcuts and exclusions.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from runtime_support import default_settings, empty_machine_state

_active_root = None


def browser_defaults():
    settings = default_settings()
    settings.update(app_name="AIRenamer Browser", scene_prefix="vfx/shots", target_prefix="genai",
                    keyframe_folder="KEYFRAMES", video_folder="VIDEO")
    return settings


def initialize():
    global _active_root
    desktop = Path(config.get_local_base_dir())
    root = desktop / "Browser"
    if _active_root == root:
        return
    if _active_root is not None:
        raise RuntimeError("Browser settings are already initialized for another user root.")
    paths = {
        "LOCAL_BASE_DIR": root, "SERVER_ROOT": root, "RUNTIME_DIR": root,
        "CONFIG_DIR": root / "Config", "STATE_DIR": root / "State",
        "LOGS_DIR": root / "Logs", "DIAGNOSTICS_DIR": root / "Diagnostics",
        "BACKUPS_DIR": root / "Config/Backups", "LICENSES_DIR": root / "Licenses",
        "PROJECTS_FILE_LEGACY": root / "Config/projects.txt",
        "PROJECTS_FILE": root / "Config/projects.json",
        "PROJECTS_DIR": root / "State/Projects",
        "EXTENSIONS_FILE": root / "Config/extensions.json",
        "FOLDERS_FILE": root / "Config/folders.json",
        "SETTINGS_FILE": root / "Config/settings.json",
        "CACHE_DIR": root / "State/Cache", "HISTORY_DIR": root / "State/History",
        "CACHE_FILE": root / "Config/cache.json", "HISTORY_FILE": root / "Config/history.json",
        "HELP_FILE": root / "Config/help.html",
        "BUNDLE_LOCK_FILE": root / "State/config-bundle.lock",
    }
    for name, path in paths.items():
        setattr(config, name, str(path))
    # FFmpeg is a tool, not a preference. Reuse the cached binary across products.
    config.TOOLS_DIR = str(desktop / "Tools")
    config.FFMPEG_PATH = str(desktop / "Tools/ffmpeg.exe")
    config.SERVER_ROOT_SOURCE = "isolated Browser AppData runtime"
    config.SERVER_ROOT_ERROR = ""
    machine = root / "State/Projects" / (config.get_machine_id() + ".json")
    marker = root / "settings-isolation.json"
    installation = config.read_json_file(str(root / "installation.json"), {})
    version = installation.get("version", "") if isinstance(installation, dict) else ""
    try:
        legacy_version = bool(version) and tuple(int(part) for part in version.split(".")) < (0, 27, 7)
    except (TypeError, ValueError):
        legacy_version = False
    legacy = (root / "structure.json").is_file() or legacy_version or (
        isinstance(installation, dict) and installation.get("legacySharedSettings") is True)
    # Coordinator is Browser-local, so a busy desktop config lock cannot block us.
    with config.config_store_lock():
        if not marker.exists():
            state = empty_machine_state()
            source = desktop / "State/Projects" / machine.name
            imported = False
            migration_error = None
            if legacy and source.is_file() and not machine.exists():
                try:
                    data = json.loads(source.read_text(encoding="utf-8-sig"))
                    if not isinstance(data, dict) or not isinstance(data.get("projects", {}), dict):
                        raise ValueError("Previous shared project list is invalid.")
                    state = {key: data.get(key, default) for key, default in state.items()}
                    imported = True
                except (OSError, ValueError, UnicodeError) as error:
                    # A damaged desktop store must not prevent adding Browser projects.
                    migration_error = str(error)
                    config.record_runtime_warning("Browser shortcut migration skipped; original preserved at "
                                                  + str(source) + ": " + migration_error)
            config.atomic_write_json_if_missing(str(machine), state)
            config.atomic_write_json_if_missing(config.SETTINGS_FILE, browser_defaults())
            config.atomic_write_json(str(marker), {"schema": 1, "legacyProjectsCopied": imported,
                                                  "source": str(source) if legacy else None,
                                                  "migrationError": migration_error})
        else:
            config.atomic_write_json_if_missing(str(machine), empty_machine_state())
            config.atomic_write_json_if_missing(config.SETTINGS_FILE, browser_defaults())
    config.load_settings()
    _active_root = root
