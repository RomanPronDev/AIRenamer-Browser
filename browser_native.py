# SPDX-License-Identifier: GPL-3.0-only
"""Chrome Native Messaging host for AIRenamer.

The browser sends project/shot/category identifiers; only this process resolves
filesystem paths. stdout is reserved for length-prefixed protocol messages.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
import base64
import io
import mimetypes
import secrets
import shutil
import subprocess
import threading
import time
import json
import os
import re
import struct
import sys
import tempfile
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

import config
import utils
import browser_update
import browser_drag
import browser_settings
import browser_platform

MAX_INBOUND = 64 * 1024 * 1024
MAX_OUTBOUND = 1024 * 1024
MAX_CHUNK = 256 * 1024
MAX_IMAGE_BYTES = 600 * 1024


PREFERENCE_KEYS = ("filename_template", "image_type_suffix", "video_type_suffix",
                   "subversion_enabled", "image_extensions", "video_extensions", "additional_categories")


def _check_category_folders(settings, structure):
    folders = [structure["imageFolder"], structure["videoFolder"]] + [
        item["folder"] for item in settings.get("additional_categories", [])]
    if len({name.casefold() for name in folders}) != len(folders):
        raise HostError("Category folders must be unique, including project folder overrides.")


def _naming_preview(settings, sequence="SEQ010", shot="SH010"):
    values = {"sequence": sequence, "scene": sequence, "shot": shot, "version": "001", "format": ""}
    enabled = config.parse_yes_no(settings["subversion_enabled"], True)
    result = {}
    for kind, suffix, extension in (("image", settings["image_type_suffix"], ".png"),
                                     ("video", settings["video_type_suffix"], ".mp4")):
        for label, sub in (("main", 0), ("subversion", 1)):
            name = settings["filename_template"]
            for key, value in {**values, "type": suffix, "subversion": f"{sub:02d}" if enabled else ""}.items():
                name = name.replace("{" + key + "}", value)
            result[kind + label.title()] = utils.clean_filename_base(name) + extension
    return result


class HostError(ValueError):
    pass


def _component(value: object, name: str) -> str:
    if not isinstance(value, str) or not config._is_windows_safe_component(value):
        raise HostError(f"Invalid {name}")
    return value


def _choose_project_folder(initial: str = "") -> str:
    """Show the native Windows folder picker without bundling Tk or Qt."""
    if browser_platform.is_macos():
        bridge = browser_drag.NativeDrag()
        try:
            return bridge.request("choose_folder", initial).get("path", "")
        finally:
            bridge.close()
    if os.name != "nt":
        raise HostError("Folder selection is available only on Windows")
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$owner = New-Object System.Windows.Forms.Form
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$owner.Size = [System.Drawing.Size]::new(1, 1)
$owner.Location = [System.Windows.Forms.Cursor]::Position
$owner.Opacity = 0.01
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select project folder'
$dialog.ShowNewFolderButton = $true
try {
    if ($env:AIRENAMER_PICKER_INITIAL -and
        (Test-Path -LiteralPath $env:AIRENAMER_PICKER_INITIAL -PathType Container)) {
        $dialog.SelectedPath = $env:AIRENAMER_PICKER_INITIAL
    }
    [void]$owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [System.IO.File]::WriteAllText(
            $env:AIRENAMER_PICKER_RESULT,
            $dialog.SelectedPath,
            [System.Text.UTF8Encoding]::new($false))
    }
} finally {
    $dialog.Dispose()
    $owner.Close()
    $owner.Dispose()
}
"""
    with tempfile.TemporaryDirectory(prefix="airenamer-picker-") as temporary:
        result_file = os.path.join(temporary, "selected.txt")
        env = utils.sanitized_subprocess_environment()
        env["AIRENAMER_PICKER_INITIAL"] = initial
        env["AIRENAMER_PICKER_RESULT"] = result_file
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Sta", "-WindowStyle", "Hidden",
                 "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                capture_output=True, timeout=300, env=env, creationflags=0x08000000,
            )
        except subprocess.TimeoutExpired as exc:
            raise HostError("Project folder picker timed out") from exc
        if result.returncode:
            raise HostError("Could not open the project folder picker")
        if os.path.isfile(result_file):
            return Path(result_file).read_text(encoding="utf-8").strip()
        return ""


def _launch_native_drag(path: str) -> None:
    """Show a small Windows drag source that offers the real file as FileDrop."""
    if browser_platform.is_macos():
        raise HostError("Use the macOS drag helper through the Browser host")
    if os.name != "nt":
        raise HostError("Desktop drag is available only on Windows")
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$filePath = $env:AIRENAMER_DRAG_PATH
if (-not [System.IO.File]::Exists($filePath)) { exit 1 }
$form = New-Object System.Windows.Forms.Form
$form.Text = 'AIRenamer - Drag to app'
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.Size = [System.Drawing.Size]::new(390, 105)
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.KeyPreview = $true
$position = [System.Windows.Forms.Cursor]::Position
$area = [System.Windows.Forms.Screen]::FromPoint($position).WorkingArea
$form.Location = [System.Drawing.Point]::new(
    [Math]::Max($area.Left, [Math]::Min($position.X - 35, $area.Right - $form.Width)),
    [Math]::Max($area.Top, [Math]::Min($position.Y - 35, $area.Bottom - $form.Height)))
$label = New-Object System.Windows.Forms.Label
$label.Text = [System.IO.Path]::GetFileName($filePath)
$label.AutoEllipsis = $true
$label.Location = [System.Drawing.Point]::new(13, 10)
$label.Size = [System.Drawing.Size]::new(355, 27)
$label.Font = [System.Drawing.Font]::new('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$label.Cursor = [System.Windows.Forms.Cursors]::Hand
$hint = New-Object System.Windows.Forms.Label
$hint.Text = 'Drag the file name into another application. Esc closes this window.'
$hint.Location = [System.Drawing.Point]::new(13, 44)
$hint.Size = [System.Drawing.Size]::new(355, 20)
$form.Controls.Add($label)
$form.Controls.Add($hint)
$label.Add_MouseDown({
    $paths = New-Object System.Collections.Specialized.StringCollection
    [void]$paths.Add($filePath)
    $data = New-Object System.Windows.Forms.DataObject
    $data.SetFileDropList($paths)
    $effect = $label.DoDragDrop($data, [System.Windows.Forms.DragDropEffects]::Copy)
    if ($effect -ne [System.Windows.Forms.DragDropEffects]::None) { $form.Close() }
})
$form.Add_KeyDown({ if ($_.KeyCode -eq [System.Windows.Forms.Keys]::Escape) { $form.Close() } })
$form.Add_Shown({ $form.Activate() })
[void]$form.ShowDialog()
$form.Dispose()
"""
    env = utils.sanitized_subprocess_environment()
    env["AIRENAMER_DRAG_PATH"] = path
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Sta", "-WindowStyle", "Hidden",
         "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, creationflags=0x08000000,
    )


def _children(parent: str) -> list[str]:
    try:
        return sorted(
            (entry.name for entry in os.scandir(parent)
             if entry.is_dir() and not config.is_ignored(entry.path, entry.name)),
            key=str.casefold,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise HostError(f"Folder unavailable: {parent}: {exc}") from exc


def _project(name: str) -> tuple[str, str]:
    name = _component(name, "project")
    projects = config.get_projects()
    if name not in projects:
        raise HostError("Unknown project")
    path = os.path.normpath(projects[name])
    data = _structure_data()
    structure = _project_structure(name, data)
    mode = structure.get("layout") or structure.get("defaultLayout")
    signature = _layout_signature(path, structure)
    cached = data.get("detected", {}).get(name, {}) if isinstance(data.get("detected"), dict) else {}
    if mode not in config.PROJECT_LAYOUTS and isinstance(cached, dict) and cached.get("signature") == signature:
        mode = cached.get("layout")
    if mode not in config.PROJECT_LAYOUTS:
        mode = _detect_layout(path, structure, config.get_project_modes().get(name))
        try:
            _remember_layout(name, path, structure, mode)
        except OSError:
            pass
    return path, mode


_STRUCTURE_KEYS = ("scenePrefix", "shotPrefix", "targetPrefix", "imageFolder", "videoFolder")


def _structure_file() -> str:
    return os.path.join(config.get_local_base_dir(), "Browser", "structure.json")


def _structure_data() -> dict:
    data = config.read_json_file(_structure_file(), {})
    return data if isinstance(data, dict) else {}


def _default_structure(data: dict | None = None) -> dict:
    data = data if data is not None else _structure_data()
    result = {"scenePrefix": os.path.join("vfx", "shots"),
              "shotPrefix": "",
              "targetPrefix": "genai",
              "imageFolder": "KEYFRAMES",
              "videoFolder": "VIDEO",
              "defaultLayout": "auto"}
    stored = data.get("defaults", {})
    if isinstance(stored, dict):
        for key in (*_STRUCTURE_KEYS, "defaultLayout"):
            if key in stored:
                result[key] = stored[key]
    return result


def _project_structure(name: str, data: dict | None = None) -> dict:
    data = data if data is not None else _structure_data()
    result = _default_structure(data)
    override = data.get("projects", {}).get(name, {}) if isinstance(data.get("projects"), dict) else {}
    if isinstance(override, dict):
        result.update({key: override[key] for key in (*_STRUCTURE_KEYS, "layout") if key in override})
    return result


def _validated_structure(payload: dict, *, defaults: bool = False) -> dict:
    result = {}
    for key in ("scenePrefix", "shotPrefix", "targetPrefix"):
        try:
            result[key] = config._validate_relative_prefix(payload.get(key, ""), key)
        except ValueError as exc:
            raise HostError(str(exc)) from exc
    current = _default_structure()
    for key in ("imageFolder", "videoFolder"):
        value = config._valid_single_folder_name(payload.get(key, current[key]))
        if not value:
            raise HostError(f"{key} must be one folder name")
        result[key] = value
    if result["imageFolder"].casefold() == result["videoFolder"].casefold():
        raise HostError("Image and video folders must be different")
    layout = payload.get("defaultLayout" if defaults else "layout")
    allowed = {"auto", *config.PROJECT_LAYOUTS} if defaults else config.PROJECT_LAYOUTS
    if layout not in allowed:
        raise HostError("Choose a valid project layout")
    result["defaultLayout" if defaults else "layout"] = layout
    _check_category_folders(config.get_settings_snapshot(), result)
    return result


def _sequence_root(project_path: str, structure: dict) -> str:
    return config.join_prefixed(project_path, structure["scenePrefix"])


def _shots_root(project_path: str, sequence: str | None, structure: dict) -> str:
    parent = _sequence_root(project_path, structure)
    if sequence:
        parent = os.path.join(parent, sequence)
    return config.join_prefixed(parent, structure["shotPrefix"])


def _target_root(project_name: str, shot_path: str) -> str:
    return config.join_prefixed(shot_path, _project_structure(project_name)["targetPrefix"])


def _ensure_media_folders(project_name: str, shot_path: str, *, standard_only: bool = False) -> str:
    target = _target_root(project_name, shot_path)
    for category in _categories(project_name):
        if standard_only and category.id not in {"keyframes", "video"}:
            continue
        os.makedirs(os.path.join(target, category.folder), exist_ok=True)
    return target


def _categories(project: str | None = None):
    structure = _project_structure(project) if project else _default_structure()
    folders = {"keyframes": structure["imageFolder"], "video": structure["videoFolder"]}
    return [replace(category, folder=folders.get(category.id, category.folder))
            for category in config.get_media_categories()]


def _layout_signature(path: str, structure: dict) -> list[str]:
    return [os.path.normcase(os.path.normpath(path)),
            structure["scenePrefix"], structure["shotPrefix"]]


def _remember_layout(name: str, path: str, structure: dict, mode: str) -> None:
    def remember(data):
        data = dict(data) if isinstance(data, dict) else {}
        detected = dict(data.get("detected", {})) if isinstance(data.get("detected"), dict) else {}
        detected[name] = {"signature": _layout_signature(path, structure), "layout": mode}
        data["detected"] = detected
        return data
    config.update_json_file(_structure_file(), {}, remember)


def _detect_layout(path: str, structure: dict, fallback: str | None = None) -> str:
    settings = {"scene_prefix": structure["scenePrefix"],
                "shot_prefix": structure["shotPrefix"],
                "target_prefix": structure["targetPrefix"],
                "keyframe_folder": structure["imageFolder"],
                "video_folder": structure["videoFolder"],
                "additional_categories": [
                    {"folder": category.folder}
                    for category in config.get_media_categories()
                    if category.id not in {"keyframes", "video"}
                ]}
    return config.detect_project_layout(path, fallback, settings=settings)


def _structure_preview(project: str, values: dict) -> dict:
    projects = config.get_projects()
    if project not in projects:
        raise HostError("Unknown project")
    project_path = projects[project]
    scene_root = _sequence_root(project_path, values)
    layout = values["layout"]
    if layout == config.PROJECT_LAYOUT_SEQUENCES:
        sequences = _children(scene_root) if os.path.isdir(scene_root) else []
        examples = [{"sequence": name, "shotsRoot": _shots_root(project_path, name, values),
                     "shots": _children(_shots_root(project_path, name, values))[:5]
                     if os.path.isdir(_shots_root(project_path, name, values)) else []}
                    for name in sequences[:3]]
        return {"projectRoot": project_path, "sequenceRoot": scene_root,
                "exists": os.path.isdir(scene_root), "sequences": sequences[:8],
                "sequenceCount": len(sequences), "examples": examples}
    shots_root = _shots_root(project_path, None, values)
    shots = _children(shots_root) if os.path.isdir(shots_root) else []
    return {"projectRoot": project_path, "shotsRoot": shots_root,
            "exists": os.path.isdir(shots_root), "shots": shots[:8],
            "shotCount": len(shots)}


def _navigation(project: str, sequence: str | None = None) -> tuple[str, str, str]:
    project_path, mode = _project(project)
    structure = _project_structure(project)
    if mode == config.PROJECT_LAYOUT_SHOTS:
        if sequence:
            raise HostError("Project has no sequence level")
        return project_path, mode, _shots_root(project_path, None, structure)
    if not sequence:
        return project_path, mode, _sequence_root(project_path, structure)
    sequence = _component(sequence, "sequence")
    root = _sequence_root(project_path, structure)
    if sequence not in _children(root):
        raise HostError("Unknown sequence")
    return project_path, mode, _shots_root(project_path, sequence, structure)


def _shot(project: str, sequence: str | None, shot: str) -> tuple[str, str]:
    shot = _component(shot, "shot")
    _, mode, root = _navigation(project, sequence)
    if mode == config.PROJECT_LAYOUT_SEQUENCES and not sequence:
        raise HostError("Sequence required")
    if shot not in _children(root):
        raise HostError("Unknown shot")
    return os.path.join(root, shot), mode


def _check_creation_path(project_path: str, path: str) -> None:
    """Do not create folders inside excluded branches of a project."""
    project_path = os.path.abspath(project_path)
    path = os.path.abspath(path)
    if os.path.commonpath([project_path, path]) != project_path:
        raise HostError("Folder must be inside the project")
    while path != project_path:
        if config.is_ignored(path, os.path.basename(path)):
            raise HostError(f"Folder is ignored: {path}")
        path = os.path.dirname(path)


def _creation_root(project: str, sequence: str | None) -> str:
    project_path, mode, root = _navigation(project, sequence)
    if not os.path.isdir(project_path):
        raise HostError("Project folder unavailable")
    if mode == config.PROJECT_LAYOUT_SEQUENCES and not sequence:
        raise HostError("Select a sequence before creating a shot")
    _check_creation_path(project_path, root)
    return root


def _standard_media_paths(project: str, shot_path: str) -> list[str]:
    structure = _project_structure(project)
    target = config.join_prefixed(shot_path, structure["targetPrefix"])
    return [os.path.join(target, structure[key]) for key in ("imageFolder", "videoFolder")]


def _category(category_id: str, project: str | None = None):
    category_id = _component(category_id, "category")
    category = next((item for item in _categories(project) if item.id == category_id), None)
    if not category or category.id != category_id:
        raise HostError("Unknown category")
    return category


def _file(project: str, sequence: str | None, shot: str, category_id: str, name: str):
    name = _component(name, "file")
    shot_path, _ = _shot(project, sequence, shot)
    category = _category(category_id, project)
    path = os.path.join(_target_root(project, shot_path), category.folder, name)
    if not os.path.isfile(path):
        raise HostError("File unavailable")
    return path, category


def _media_record(path: str, category) -> dict:
    st = os.stat(path)
    version = utils.parse_version_from_filename(os.path.basename(path))
    drag_path = utils.resolve_converted_drag_path(path) if category.media_type == config.MEDIA_TYPE_VIDEO else path
    return {
        "name": os.path.basename(path),
        "path": path,
        "dragPath": drag_path,
        "category": category.id,
        "size": st.st_size,
        "modified": st.st_mtime,
        "version": version[0] if version else None,
        "subversion": version[1] if version else None,
        "converted": drag_path != path,
        "isDirectoryDrag": os.path.isdir(drag_path),
    }


def _sequence_destination(video_path: str) -> str:
    return os.path.splitext(video_path)[0]


def _rename_sequence_frames(folder: str, old_stem: str, new_stem: str) -> None:
    """Keep generated frame names aligned with a renamed video."""
    if old_stem == new_stem:
        return
    renamed = []
    try:
        for entry in os.scandir(folder):
            if not entry.is_file() or not entry.name.startswith(old_stem + "_"):
                continue
            suffix = entry.name[len(old_stem):]
            target = os.path.join(folder, new_stem + suffix)
            if os.path.exists(target):
                raise HostError(f"Sequence frame already exists: {target}")
            os.rename(entry.path, target)
            renamed.append((target, entry.path))
    except Exception:
        for target, original in reversed(renamed):
            os.rename(target, original)
        raise


def _rename_sequence_folder(source_folder: str, old_video: str, new_video: str) -> str:
    target = _sequence_destination(new_video)
    if os.path.exists(target):
        raise HostError(f"Sequence folder already exists: {target}")
    os.rename(source_folder, target)
    try:
        _rename_sequence_frames(target, Path(old_video).stem, Path(new_video).stem)
    except Exception:
        os.rename(target, source_folder)
        raise
    return target


def _psd_version(name: str):
    return re.search(r"_v(\d{3})(?:_(\d{2}))?(?=$|[_\-.])",
                     os.path.splitext(name)[0], re.IGNORECASE)


def _psd_matches(raster: str, candidates: list[str]) -> bool:
    stem = Path(raster).stem.casefold()
    if any(Path(item).stem.casefold() == stem for item in candidates):
        return True
    match = _psd_version(raster)
    return bool(match and any(
        (other := _psd_version(item)) and other.groups() == match.groups()
        for item in candidates
    ))


class BrowserHost:
    def __init__(self, *, initialize_settings=True):
        if initialize_settings:
            browser_settings.initialize()
        self._native_drag = browser_drag.NativeDrag()
        config.load_settings()
        self.transfers: dict[str, dict] = {}
        self.conversions: dict[str, dict] = {}
        self._conversion_lock = threading.Lock()
        self._drag_server = None
        self._drag_thread = None
        self._drag_entries: dict[str, tuple[str, float, bool]] = {}
        self._direct_files: dict[str, tuple[str, float]] = {}

    def _drag_url(self, path: str, *, inline: bool = False) -> str:
        if self._drag_server is None:
            owner = self

            class DragHandler(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    return

                def do_HEAD(self):
                    self._send_file(head_only=True)

                def do_GET(self):
                    self._send_file(head_only=False)

                def _send_file(self, head_only: bool):
                    route = urlsplit(self.path).path.split("/")
                    token = route[2] if len(route) == 3 and route[1] == "file" else ""
                    record = owner._drag_entries.get(token)
                    if not record or record[1] < time.time():
                        self.send_error(404)
                        return
                    path = record[0]
                    try:
                        handle = open(path, "rb")
                        size = os.fstat(handle.fileno()).st_size
                    except OSError:
                        self.send_error(404)
                        return
                    with handle:
                        name = os.path.basename(path)
                        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                        start, end = 0, size - 1
                        partial = False
                        requested = self.headers.get("Range")
                        if requested:
                            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
                            if match and size:
                                first, last = match.groups()
                                if first:
                                    start = int(first)
                                    end = min(int(last), size - 1) if last else size - 1
                                elif last:
                                    start = max(0, size - int(last))
                                else:
                                    start = size
                                partial = 0 <= start <= end < size
                            if not partial:
                                self.send_response(416)
                                self.send_header("Content-Range", f"bytes */{size}")
                                self.send_header("Accept-Ranges", "bytes")
                                self.send_header("Content-Length", "0")
                                self.end_headers()
                                return
                        length = end - start + 1 if partial else size
                        self.send_response(206 if partial else 200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(length))
                        self.send_header("Accept-Ranges", "bytes")
                        if partial:
                            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        disposition = "inline" if record[2] else "attachment"
                        self.send_header("Content-Disposition",
                                         disposition + "; filename*=UTF-8''" + quote(name))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        if not head_only:
                            try:
                                handle.seek(start)
                                remaining = length
                                while remaining:
                                    chunk = handle.read(min(64 * 1024, remaining))
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                                    remaining -= len(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                pass

            self._drag_server = ThreadingHTTPServer(("127.0.0.1", 0), DragHandler)
            self._drag_server.daemon_threads = True
            self._drag_thread = threading.Thread(target=self._drag_server.serve_forever,
                                                 name="AIRenamerDrag", daemon=True)
            self._drag_thread.start()
        now = time.time()
        if len(self._drag_entries) > 500:
            self._drag_entries = {key: value for key, value in self._drag_entries.items()
                                  if value[1] > now}
        token = secrets.token_urlsafe(24)
        self._drag_entries[token] = (path, now + 3600, inline)
        return f"http://127.0.0.1:{self._drag_server.server_port}/file/{token}"

    def close(self):
        self._native_drag.close()
        with self._conversion_lock:
            conversions = list(self.conversions.values())
        for job in conversions:
            job["cancel"].set()
        for job in conversions:
            if job["thread"].is_alive():
                job["thread"].join(timeout=10)
        if self._drag_server is not None:
            self._drag_server.shutdown()
            self._drag_server.server_close()
            self._drag_thread.join(timeout=2)
            self._drag_server = None
        for item in self.transfers.values():
            try:
                item["handle"].close()
            finally:
                try:
                    os.remove(item["path"])
                except OSError:
                    pass
        self.transfers.clear()

    def projects(self, _=None):
        projects = config.get_projects()
        modes = config.get_project_modes()
        data = _structure_data()
        def layout_for(name, path):
            structure = _project_structure(name, data)
            override = structure.get("layout") or structure.get("defaultLayout")
            cached = data.get("detected", {}).get(name, {}) if isinstance(data.get("detected"), dict) else {}
            if override in config.PROJECT_LAYOUTS:
                return override
            if isinstance(cached, dict) and cached.get("signature") == _layout_signature(path, structure):
                return cached.get("layout")
            return modes.get(name) if modes.get(name) in config.PROJECT_LAYOUTS else (
                config.PROJECT_LAYOUT_SHOTS if config.SKIP_SEQUENCE else config.PROJECT_LAYOUT_SEQUENCES)
        return {
            **browser_platform.capabilities(),
            "projects": [
                {"name": name, "path": path,
                 "layout": layout_for(name, path),
                 "structure": _project_structure(name, data),
                 "customStructure": name in data.get("projects", {}),
                 "available": os.path.isdir(path)}
                for name, path in sorted(projects.items(), key=lambda item: item[0].casefold())
            ],
            "preferences": {"subversionsEnabled": config.SUBVERSION_ENABLED,
                            "imageExtensions": list(config.ALLOWED_EXT_IMAGE),
                            "videoExtensions": list(config.ALLOWED_EXT_VIDEO)},
            "categories": [
                {"id": cat.id, "folder": cat.folder, "mediaType": cat.media_type, "typeSuffix": cat.type_suffix}
                for cat in _categories()
            ],
        }

    def preferences(self, _=None):
        names = [item for item in config.get_machine_ignored_folders()
                 if isinstance(item, str) and not ("\\" in item or "/" in item)]
        settings = config.get_settings_snapshot()
        return {"ignoredNames": sorted(set(names) | {"_shotcode"}, key=str.casefold),
                "settings": {key: settings[key] for key in PREFERENCE_KEYS},
                "preview": _naming_preview(settings)}

    def _candidate_preferences(self, payload):
        patch = payload.get("settings", {})
        if not isinstance(patch, dict) or set(patch) - set(PREFERENCE_KEYS):
            raise HostError("Unsupported Browser setting")
        try:
            settings = config._normalize_settings_snapshot({**config.get_settings_snapshot(), **patch})
        except ValueError as exc:
            raise HostError(str(exc)) from exc
        if set(settings["image_extensions"]) & set(settings["video_extensions"]):
            raise HostError("Image and video extensions must not overlap.")
        if config.parse_yes_no(settings["subversion_enabled"], True) and "{subversion}" not in settings["filename_template"]:
            raise HostError("Include {subversion} in the filename template or disable subversions.")
        if re.search(r"\{(?:version|subversion)\}\{(?:version|subversion)\}", settings["filename_template"]):
            raise HostError("Separate version and subversion tokens with text or punctuation.")
        data = _structure_data()
        _check_category_folders(settings, _default_structure(data))
        for name in data.get("projects", {}):
            _check_category_folders(settings, _project_structure(name, data))
        return settings

    def preview_naming(self, payload):
        settings = self._candidate_preferences(payload)
        sequence = payload.get("sequence", "SEQ010")
        shot = payload.get("shot", "SH010")
        if not isinstance(sequence, str) or not isinstance(shot, str):
            raise HostError("Invalid preview context")
        return _naming_preview(settings, sequence, shot)

    def set_preferences(self, payload):
        settings = self._candidate_preferences(payload)
        if "names" in payload:
            clean = self._clean_ignored_names(payload["names"])
            with config.config_store_lock():
                machine = config._current_machine_state_unlocked()
                machine["ignored_folders"] = [name for name in machine["ignored_folders"] if "\\" in name or "/" in name] + clean
                config.save_config_bundle(config._canonical_bundle(settings, machine))
        else:
            config.save_settings_snapshot(settings)
        config.load_settings()
        return self.preferences()

    def _import_options(self, payload, category, destination):
        subversion = payload.get("subversion", False)
        add_format = payload.get("addFormat", False)
        if not isinstance(subversion, bool) or not isinstance(add_format, bool):
            raise HostError("Import options must be true or false")
        existing = payload.get("targetExisting")
        if existing and config.SUBVERSION_ENABLED:
            name = _component(existing, "filename")
            path, target_category = _file(payload["project"], payload.get("sequence"),
                                          payload["shot"], payload["targetCategory"], name)
            if target_category.id != category.id or os.path.normcase(os.path.dirname(path)) != os.path.normcase(destination):
                raise HostError("Subversion must have the same media category as the selected file")
            if utils.parse_version_from_filename(name) is None:
                raise HostError("Fix the selected file name before adding a subversion")
            subversion = True
        else:
            existing = None
        return {"is_subversion": subversion and config.SUBVERSION_ENABLED,
                "target_existing_filename": existing, "add_format": add_format}

    def get_structure(self, payload):
        project = payload.get("project")
        data = _structure_data()
        if not project:
            return {"defaults": _default_structure(data)}
        project = _component(project, "project")
        path, layout = _project(project)
        values = _project_structure(project)
        values["layout"] = layout
        return {"defaults": _default_structure(data), "values": values,
                "custom": project in data.get("projects", {}),
                "preview": _structure_preview(project, values), "projectRoot": path}

    def preview_structure(self, payload):
        project = _component(payload.get("project"), "project")
        values = _validated_structure(payload)
        return _structure_preview(project, values)

    def set_default_structure(self, payload):
        values = _validated_structure(payload, defaults=True)
        def update(data):
            data = dict(data) if isinstance(data, dict) else {}
            data["defaults"] = values
            return data
        config.update_json_file(_structure_file(), {}, update)
        return {"defaults": values}

    def set_project_structure(self, payload):
        project = _component(payload.get("project"), "project")
        if project not in config.get_projects():
            raise HostError("Unknown project")
        values = _validated_structure(payload)
        def update(data):
            data = dict(data) if isinstance(data, dict) else {}
            projects = dict(data.get("projects", {})) if isinstance(data.get("projects"), dict) else {}
            projects[project] = values
            data["projects"] = projects
            return data
        config.update_json_file(_structure_file(), {}, update)
        return {"values": values, "preview": _structure_preview(project, values)}

    def reset_project_structure(self, payload):
        project = _component(payload.get("project"), "project")
        def update(data):
            data = dict(data) if isinstance(data, dict) else {}
            projects = dict(data.get("projects", {})) if isinstance(data.get("projects"), dict) else {}
            projects.pop(project, None)
            data["projects"] = projects
            detected = dict(data.get("detected", {})) if isinstance(data.get("detected"), dict) else {}
            detected.pop(project, None)
            data["detected"] = detected
            return data
        config.update_json_file(_structure_file(), {}, update)
        return {"reset": True}

    def choose_project_folder(self, payload):
        initial = payload.get("initial") or ""
        if not isinstance(initial, str):
            raise HostError("Invalid initial folder")
        return {"path": _choose_project_folder(initial)}

    def _clean_ignored_names(self, names):
        if not isinstance(names, list) or len(names) > 50:
            raise HostError("Provide up to 50 ignored folder names")
        clean = []
        for item in names:
            if not isinstance(item, str):
                raise HostError("Invalid ignored folder name")
            name = item.strip()
            if not name or len(name) > 80 or not config._is_windows_safe_component(name):
                raise HostError("Invalid ignored folder name")
            if name.casefold() not in {value.casefold() for value in clean}:
                clean.append(name)
        return [name for name in clean if name.casefold() != "_shotcode"]

    def set_ignored_names(self, payload):
        clean = self._clean_ignored_names(payload.get("names"))
        existing = [item for item in config.get_machine_ignored_folders()
                    if isinstance(item, str) and not ("\\" in item or "/" in item)]
        desired = {item.casefold() for item in clean}
        current = {item.casefold() for item in existing}
        for item in existing:
            if item.casefold() not in desired:
                config.restore_ignored_folder(item)
        for item in clean:
            if item.casefold() not in current and item.casefold() != "_shotcode":
                config.add_ignored_folder(item)
        config.load_settings()
        return self.preferences()

    def add_project(self, payload):
        folder = payload.get("path")
        if not folder:
            folder = _choose_project_folder()
        if not folder:
            return {"cancelled": True}
        if not isinstance(folder, str) or not os.path.isabs(folder):
            raise HostError("Project path must be absolute")
        folder = os.path.normpath(folder)
        if not os.path.isdir(folder):
            raise HostError("Project folder unavailable")
        projects = config.get_projects()
        if any(os.path.normcase(os.path.normpath(value)) == os.path.normcase(folder)
               for value in projects.values()):
            raise HostError("Project already exists")
        name = os.path.basename(folder.rstrip("\\/")) or folder
        if not config._is_windows_safe_component(name):
            raise HostError("Invalid project folder name")
        if any(existing.casefold() == name.casefold() for existing in projects):
            raise HostError("Project name already exists")
        projects[name] = folder
        modes = config.get_project_modes()
        defaults = _default_structure()
        modes[name] = (defaults["defaultLayout"] if defaults["defaultLayout"] in config.PROJECT_LAYOUTS
                       else _detect_layout(folder, defaults))
        config.save_projects(projects, project_modes=modes)
        if defaults["defaultLayout"] == "auto":
            try:
                _remember_layout(name, folder, defaults, modes[name])
            except OSError:
                pass
        return {"name": name, "layout": modes[name], "path": folder}

    def change_project_path(self, payload):
        old_name = _component(payload.get("project"), "project")
        projects = config.get_projects()
        if old_name not in projects:
            raise HostError("Unknown project")
        folder = payload.get("path")
        if not folder:
            folder = _choose_project_folder(projects[old_name])
        if not folder:
            return {"cancelled": True}
        if not isinstance(folder, str) or not os.path.isabs(folder):
            raise HostError("Project path must be absolute")
        folder = os.path.normpath(folder)
        if not os.path.isdir(folder):
            raise HostError("Project folder unavailable")
        name = os.path.basename(folder.rstrip("\\/")) or folder
        if not config._is_windows_safe_component(name):
            raise HostError("Invalid project folder name")
        if any(existing != old_name and (
            existing.casefold() == name.casefold() or
            os.path.normcase(os.path.normpath(path)) == os.path.normcase(folder)
        ) for existing, path in projects.items()):
            raise HostError("Project name or folder already exists")
        if name != old_name:
            del projects[old_name]
        projects[name] = folder
        modes = config.get_project_modes()
        modes.pop(old_name, None)
        structure = _project_structure(old_name)
        explicit_layout = structure.get("layout") or structure.get("defaultLayout")
        modes[name] = (explicit_layout if explicit_layout in config.PROJECT_LAYOUTS
                       else _detect_layout(folder, structure))
        config.save_projects(projects, project_modes=modes)
        if name != old_name:
            def rename(data):
                data = dict(data) if isinstance(data, dict) else {}
                overrides = dict(data.get("projects", {})) if isinstance(data.get("projects"), dict) else {}
                if old_name in overrides:
                    overrides[name] = overrides.pop(old_name)
                    data["projects"] = overrides
                detected = dict(data.get("detected", {})) if isinstance(data.get("detected"), dict) else {}
                detected.pop(old_name, None)
                data["detected"] = detected
                return data
            config.update_json_file(_structure_file(), {}, rename)
        if explicit_layout not in config.PROJECT_LAYOUTS:
            try:
                _remember_layout(name, folder, structure, modes[name])
            except OSError:
                pass
        return {"name": name, "path": folder, "layout": modes[name]}

    def remove_project(self, payload):
        name = _component(payload.get("project"), "project")
        projects = config.get_projects()
        if name not in projects:
            raise HostError("Unknown project")
        del projects[name]
        modes = config.get_project_modes()
        modes.pop(name, None)
        config.save_projects(projects, project_modes=modes)
        def forget(data):
            data = dict(data) if isinstance(data, dict) else {}
            for key in ("projects", "detected"):
                entries = dict(data.get(key, {})) if isinstance(data.get(key), dict) else {}
                entries.pop(name, None)
                data[key] = entries
            return data
        config.update_json_file(_structure_file(), {}, forget)
        return {"removed": name}

    def shot_creation_info(self, payload):
        project = payload["project"]
        root = _creation_root(project, payload.get("sequence") or None)
        structure = _project_structure(project)
        return {"shotsRoot": root, "mediaPrefix": structure["targetPrefix"],
                "mediaFolders": [structure["imageFolder"], structure["videoFolder"]]}

    def create_shot(self, payload):
        project = payload["project"]
        sequence = payload.get("sequence") or None
        name = _component(payload.get("shot"), "shot name")
        root = _creation_root(project, sequence)
        shot_path = os.path.join(root, name)
        paths = _standard_media_paths(project, shot_path)
        project_path = config.get_projects()[project]
        for path in paths:
            _check_creation_path(project_path, path)
        try:
            os.makedirs(root, exist_ok=True)
            # Check case-insensitively on every platform; mkdir still arbitrates races.
            with os.scandir(root) as entries:
                if any(entry.name.casefold() == name.casefold() for entry in entries):
                    raise HostError("A shot or file with this name already exists")
            os.mkdir(shot_path)
        except FileExistsError as exc:
            raise HostError("A shot or file with this name already exists") from exc
        except OSError as exc:
            raise HostError(f"Could not create shot: {exc}") from exc
        try:
            _ensure_media_folders(project, shot_path, standard_only=True)
        except OSError as exc:
            # Never delete a created shot: another process may already have used it.
            raise HostError(f"Shot {name} was created, but media folders could not be created. "
                            f"Select the shot and retry Create media folders: {exc}") from exc
        return {"project": project, "sequence": sequence, "shot": name,
                "path": shot_path, "folders": paths}

    def create_media_folders(self, payload):
        project = payload["project"]
        shot_path, _ = _shot(project, payload.get("sequence"), payload["shot"])
        paths = _standard_media_paths(project, shot_path)
        for path in paths:
            _check_creation_path(config.get_projects()[project], path)
        try:
            _ensure_media_folders(project, shot_path, standard_only=True)
        except OSError as exc:
            raise HostError(f"Could not create media folders: {exc}") from exc
        return {"folders": paths}

    def navigation(self, payload):
        name = payload["project"]
        path, mode = _project(name)
        if not os.path.isdir(path):
            raise HostError("Project folder unavailable")
        sequences = _children(_sequence_root(path, _project_structure(name))) if mode == config.PROJECT_LAYOUT_SEQUENCES else []
        sequence = payload.get("sequence") or None
        if mode == config.PROJECT_LAYOUT_SEQUENCES and sequence is None:
            return {"layout": mode, "sequences": sequences, "shots": [],
                    "categories": [
                        {"id": cat.id, "folder": cat.folder, "mediaType": cat.media_type,
                         "typeSuffix": cat.type_suffix} for cat in _categories(name)]}
        _, _, root = _navigation(name, sequence)
        return {"layout": mode, "sequences": sequences, "shots": _children(root),
                "categories": [
                    {"id": cat.id, "folder": cat.folder, "mediaType": cat.media_type,
                     "typeSuffix": cat.type_suffix} for cat in _categories(name)]}

    def files(self, payload):
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        target = _target_root(payload["project"], shot_path)
        result = []
        for category in _categories(payload["project"]):
            folder = os.path.join(target, category.folder)
            try:
                entries = [entry for entry in os.scandir(folder) if entry.is_file()]
            except FileNotFoundError:
                entries = []
            except (PermissionError, OSError) as exc:
                raise HostError(f"Category folder unavailable: {folder}: {exc}") from exc
            psds = [entry.name for entry in entries if entry.name.lower().endswith(".psd")]
            allowed = config.ALLOWED_EXT_VIDEO if category.media_type == config.MEDIA_TYPE_VIDEO else config.ALLOWED_EXT_IMAGE
            for entry in entries:
                if category.id == "keyframes" and entry.name.lower().endswith(".psd"):
                    continue
                if os.path.splitext(entry.name)[1].lower() not in allowed:
                    continue
                record = _media_record(entry.path, category)
                record["dragUrl"] = self._drag_url(entry.path)
                # Mint a short-lived capability while the shot is already scanned.
                # Starting a drag must not enumerate network folders again.
                token = secrets.token_urlsafe(24)
                self._direct_files[token] = (entry.path, time.time() + 3600)
                record["nativeDragToken"] = token
                record["hasPsd"] = category.id == "keyframes" and _psd_matches(entry.name, psds)
                result.append(record)
        result.sort(key=lambda item: item["modified"], reverse=True)
        if len(self._direct_files) > 1000:
            now = time.time()
            current = {item["nativeDragToken"] for item in result}
            self._direct_files = {token: record for token, record in self._direct_files.items()
                                  if token in current and record[1] > now}
        return {"files": result, "path": target}

    def import_file(self, payload):
        source = payload.get("source")
        if not isinstance(source, str) or not os.path.isfile(source):
            raise HostError("Source file unavailable")
        file_type = utils.get_file_type(source)
        if not file_type:
            raise HostError("Unsupported media type")
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        category = (_category(payload["category"], payload["project"])
                    if payload.get("category") else
                    _category(config.get_category_for_file_type(file_type).id, payload["project"]))
        if category.file_type != file_type:
            raise HostError("File does not match destination category")
        target = os.path.join(_ensure_media_folders(payload["project"], shot_path), category.folder)
        os.makedirs(target, exist_ok=True)
        new_path, new_name, _ = utils.copy_and_rename_file(
            source, target, payload.get("sequence") or "", payload["shot"],
            file_type, category=category, **self._import_options(payload, category, target),
        )
        return {"name": new_name, "path": new_path, "category": category.id}

    def rename_file(self, payload):
        path, category = _file(payload["project"], payload.get("sequence"),
                               payload["shot"], payload["category"], payload["name"])
        if any(job["path"] == path and job["state"] in ("queued", "running")
               for job in self.conversions.values()):
            raise HostError("Cannot rename a video while it is converting")
        sequence_folder = utils.find_converted_sequence_folder(path) if category.media_type == config.MEDIA_TYPE_VIDEO else ""
        messages, undo = utils.rename_target_files(
            os.path.dirname(path), payload.get("sequence") or "", payload["shot"],
            single_filename=os.path.basename(path), category=category,
        )
        if sequence_folder and undo:
            new_path = undo[0][1]
            try:
                _rename_sequence_folder(sequence_folder, path, new_path)
            except Exception:
                os.rename(new_path, path)
                raise
        return {"messages": messages}

    def move_file(self, payload):
        src, category = _file(payload["sourceProject"], payload.get("sourceSequence"),
                              payload["sourceShot"], payload["category"], payload["name"])
        if any(job["path"] == src and job["state"] in ("queued", "running")
               for job in self.conversions.values()):
            raise HostError("Cannot move a video while it is converting")
        target_shot, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        source_shot, _ = _shot(payload["sourceProject"], payload.get("sourceSequence"),
                               payload["sourceShot"])
        if os.path.normcase(os.path.abspath(target_shot)) == os.path.normcase(os.path.abspath(source_shot)):
            raise HostError("File is already in this shot")
        destination_category = _category(category.id, payload["project"])
        target = os.path.join(_ensure_media_folders(payload["project"], target_shot), destination_category.folder)
        os.makedirs(target, exist_ok=True)
        source_sequence = utils.find_converted_sequence_folder(src) if category.media_type == config.MEDIA_TYPE_VIDEO else ""
        new_path, new_name, _ = utils.copy_and_rename_file(
            src, target, payload.get("sequence") or "", payload["shot"],
            category.file_type, category=destination_category,
        )
        staged_sequence = ""
        try:
            if source_sequence:
                target_sequence = _sequence_destination(new_path)
                staged_sequence = target_sequence + ".airenamer-" + uuid.uuid4().hex
                shutil.copytree(source_sequence, staged_sequence)
                _rename_sequence_frames(staged_sequence, Path(src).stem, Path(new_path).stem)
                if os.path.exists(target_sequence):
                    raise HostError(f"Sequence folder already exists: {target_sequence}")
                os.rename(staged_sequence, target_sequence)
                staged_sequence = ""
        except Exception:
            if staged_sequence and os.path.isdir(staged_sequence):
                shutil.rmtree(staged_sequence)
            os.remove(new_path)
            raise
        try:
            os.remove(src)
            if source_sequence:
                shutil.rmtree(source_sequence)
        except OSError as exc:
            raise HostError(f"Copied to {new_path}, but could not remove original: {exc}") from exc
        return {"name": new_name, "path": new_path}

    def convert_begin(self, payload):
        path, category = _file(payload["project"], payload.get("sequence"),
                               payload["shot"], payload["category"], payload["name"])
        if category.media_type != config.MEDIA_TYPE_VIDEO:
            raise HostError("Convert to PNG is available only for videos")
        if utils.has_converted_sequence_folder(path):
            raise HostError("PNG sequence already exists")
        with self._conversion_lock:
            if any(job["path"] == path and job["state"] in ("queued", "running")
                   for job in self.conversions.values()):
                raise HostError("This video is already converting")
            if sum(job["state"] in ("queued", "running")
                   for job in self.conversions.values()) >= 1:
                raise HostError("Another video is converting")
            job_id = uuid.uuid4().hex
            job = {"path": path, "state": "queued", "error": "", "folder": "",
                   "cancel": threading.Event(), "thread": None}
            worker = threading.Thread(target=self._run_conversion, args=(job_id,),
                                      name="AIRenamerPNG", daemon=True)
            job["thread"] = worker
            self.conversions[job_id] = job
            worker.start()
        return {"jobId": job_id, "state": "queued"}

    def _run_conversion(self, job_id):
        job = self.conversions[job_id]
        try:
            if job["cancel"].is_set():
                job["state"] = "cancelled"
                return
            import browser_ffmpeg
            local_path = browser_ffmpeg.provision(config.FFMPEG_PATH, timeout=600)
            if job["cancel"].is_set():
                job["state"] = "cancelled"
                return
            job["state"] = "running"
            source = job["path"]
            folder, _, _ = utils.convert_to_sequence(
                source, os.path.dirname(source), utils.sequence_base_name_for_video(source),
                cancel_check=job["cancel"].is_set, ffmpeg_path=local_path,
            )
            job["folder"] = folder
            job["state"] = "complete"
        except Exception as exc:
            job["error"] = str(exc)
            job["state"] = "cancelled" if job["cancel"].is_set() else "error"

    def convert_status(self, payload):
        job = self.conversions.get(payload.get("jobId"))
        if not job:
            raise HostError("Unknown conversion")
        return {"state": job["state"], "folder": job["folder"], "error": job["error"]}

    def convert_cancel(self, payload):
        job = self.conversions.get(payload.get("jobId"))
        if not job:
            raise HostError("Unknown conversion")
        job["cancel"].set()
        return {"cancelled": True}

    def open_folder(self, payload):
        if not payload.get("shot"):
            path, _ = _project(payload["project"])
            if not os.path.isdir(path):
                raise HostError("Project folder unavailable")
            browser_platform.open_folder(path)
            return {"opened": True}
        if payload.get("file"):
            path, category = _file(payload["project"], payload.get("sequence"),
                                   payload["shot"], payload["category"], payload["file"])
            if payload.get("convertedFolder"):
                if category.media_type != config.MEDIA_TYPE_VIDEO:
                    raise HostError("PNG sequence is available only for video")
                directory = utils.find_converted_sequence_folder(path)
                if not directory or not os.path.isdir(directory):
                    raise HostError("PNG sequence folder unavailable")
                browser_platform.open_folder(directory, reveal=True)
                return {"opened": True}
            if not os.path.isdir(os.path.dirname(path)):
                raise HostError("Folder unavailable")
            browser_platform.open_folder(path, reveal=True)
            return {"opened": True}
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        directory = _target_root(payload["project"], shot_path)
        if not os.path.isdir(directory):
            raise HostError("Folder unavailable")
        browser_platform.open_folder(directory)
        return {"opened": True}

    def drag_to_app(self, payload):
        path, _ = _file(payload["project"], payload.get("sequence"),
                        payload["shot"], payload["category"], payload["name"])
        if browser_platform.is_macos():
            return self._native_drag.request("handle", path)
        _launch_native_drag(path)
        return {"opened": True}

    def drag_prepare(self, _payload):
        return self._native_drag.request("ping")

    def drag_status(self, _payload):
        return dict(self._native_drag.last)

    def drag_stop(self, _payload):
        self._native_drag.close()
        return {"stopped": True}

    def drag_probe(self, _payload):
        report = browser_update.installation_file().parent / "drag-probe.jsonl"
        report.parent.mkdir(parents=True, exist_ok=True)
        return self._native_drag.request("probe", str(report))

    def drag_direct(self, payload):
        issued = payload.get("issued")
        if not isinstance(issued, (int, float)) or not 0 <= time.time() * 1000 - issued < 750:
            raise HostError("Native drag request expired. Hover the file, then try again.")
        token = payload.get("fileToken")
        if token is not None:
            record = self._direct_files.get(token) if isinstance(token, str) else None
            if not record or record[1] < time.time():
                raise HostError("File drag access expired. Refresh Files and try again.")
            path = record[0]
        else:
            path, _ = _file(payload["project"], payload.get("sequence"),
                            payload["shot"], payload["category"], payload["name"])
        return self._native_drag.request("begin", path, issued=issued)

    def _preview_path(self, path: str):
        if not os.path.isfile(path):
            raise HostError("File unavailable")
        media_type = utils.get_file_type(path)
        if media_type == config.TYPE_VID:
            return {"available": True, "mediaType": "video",
                    "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
                    "url": self._drag_url(path, inline=True)}
        if media_type != config.TYPE_IMG:
            return {"available": False}
        from PIL import Image, ImageOps
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((420, 420))
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=75)
        except (OSError, ValueError):
            return {"available": False}
        value = output.getvalue()
        if len(value) > MAX_IMAGE_BYTES:
            raise HostError("Preview too large")
        return {"available": True, "mime": "image/jpeg", "data": base64.b64encode(value).decode("ascii")}

    def preview(self, payload):
        path, _ = _file(payload["project"], payload.get("sequence"),
                        payload["shot"], payload["category"], payload["name"])
        return self._preview_path(path)

    def preview_download(self, payload):
        path = payload.get("path")
        if not isinstance(path, str) or not os.path.isabs(path):
            raise HostError("Invalid download path")
        return self._preview_path(os.path.normpath(path))

    def transfer_begin(self, payload):
        if len(self.transfers) >= 4:
            raise HostError("Too many active transfers")
        name = _component(payload.get("name"), "filename")
        file_type = utils.get_file_type(name)
        if not file_type:
            raise HostError("Unsupported media type")
        _shot(payload["project"], payload.get("sequence"), payload["shot"])
        stage_root = os.path.join(config.get_local_base_dir(), "Browser", "Staging")
        os.makedirs(stage_root, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="airenamer-", suffix=Path(name).suffix,
                                    dir=stage_root)
        transfer_id = uuid.uuid4().hex
        handle = os.fdopen(fd, "wb")
        self.transfers[transfer_id] = {
            "path": path, "handle": handle, "size": 0,
            "destination": {key: payload[key] for key in ("project", "shot")},
            "sequence": payload.get("sequence"),
            "category": payload.get("category"),
            "options": {key: payload[key] for key in ("subversion", "targetExisting", "targetCategory", "addFormat") if key in payload},
        }
        return {"transferId": transfer_id}

    def transfer_chunk(self, payload):
        item = self.transfers.get(payload.get("transferId"))
        if not item:
            raise HostError("Unknown transfer")
        data = base64.b64decode(payload.get("data", ""), validate=True)
        if not data or len(data) > MAX_CHUNK:
            raise HostError("Invalid chunk size")
        item["handle"].write(data)
        item["size"] += len(data)
        return {"received": item["size"]}

    def transfer_finish(self, payload):
        transfer_id = payload.get("transferId")
        item = self.transfers.pop(transfer_id, None)
        if not item:
            raise HostError("Unknown transfer")
        try:
            item["handle"].flush()
            os.fsync(item["handle"].fileno())
            item["handle"].close()
            if not item["size"]:
                raise HostError("Empty transfer")
            destination = dict(item["destination"])
            destination["sequence"] = item["sequence"]
            destination["category"] = item["category"]
            destination.update(item["options"])
            destination["source"] = item["path"]
            result = self.import_file(destination)
            return result
        finally:
            try:
                item["handle"].close()
                os.remove(item["path"])
            except OSError:
                pass

    def transfer_abort(self, payload):
        item = self.transfers.pop(payload.get("transferId"), None)
        if item:
            item["handle"].close()
            os.remove(item["path"])
        return {"aborted": True}

    def dispatch(self, message):
        if not isinstance(message, dict):
            raise HostError("Message must be an object")
        kind = message.get("type")
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            raise HostError("Payload must be an object")
        commands = {
            "ping": lambda _: {"version": config.get_app_version(), **browser_platform.capabilities()},
            "start_update": self.start_update,
            "projects": self.projects,
            "preferences": self.preferences,
            "set_preferences": self.set_preferences,
            "preview_naming": self.preview_naming,
            "get_structure": self.get_structure,
            "preview_structure": self.preview_structure,
            "set_default_structure": self.set_default_structure,
            "set_project_structure": self.set_project_structure,
            "reset_project_structure": self.reset_project_structure,
            "choose_project_folder": self.choose_project_folder,
            "set_ignored_names": self.set_ignored_names,
            "add_project": self.add_project,
            "change_project_path": self.change_project_path,
            "remove_project": self.remove_project,
            "navigation": self.navigation,
            "shot_creation_info": self.shot_creation_info,
            "create_shot": self.create_shot,
            "create_media_folders": self.create_media_folders,
            "files": self.files,
            "import_file": self.import_file,
            "rename_file": self.rename_file,
            "move_file": self.move_file,
            "convert_begin": self.convert_begin,
            "convert_status": self.convert_status,
            "convert_cancel": self.convert_cancel,
            "open_folder": self.open_folder,
            "drag_to_app": self.drag_to_app,
            "drag_prepare": self.drag_prepare,
            "drag_status": self.drag_status,
            "drag_stop": self.drag_stop,
            "drag_probe": self.drag_probe,
            "drag_direct": self.drag_direct,
            "ffmpeg_status": self.ffmpeg_status,
            "ffmpeg_setup": self.ffmpeg_setup,
            "preview": self.preview,
            "preview_download": self.preview_download,
            "transfer_begin": self.transfer_begin,
            "transfer_chunk": self.transfer_chunk,
            "transfer_finish": self.transfer_finish,
            "transfer_abort": self.transfer_abort,
        }
        if kind not in commands:
            raise HostError("Unknown command")
        return commands[kind](payload)

    def ffmpeg_status(self, _payload):
        import browser_ffmpeg
        return browser_ffmpeg.tool_status(Path(config.FFMPEG_PATH))

    def ffmpeg_setup(self, _payload):
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [sys.executable, os.path.abspath(__file__)])
        import browser_ffmpeg
        target = Path(config.FFMPEG_PATH)
        state = browser_ffmpeg.tool_status(target)
        if state.get('state') == 'ready':
            return {'started': False, **state}
        if state.get("state") in browser_ffmpeg.ACTIVE_STATES:
            return {"started": True}
        platform = 'macos' if browser_platform.is_macos() else 'windows'
        browser_ffmpeg._report(target, "queued", platform=platform)
        try:
            subprocess.Popen([*command, "--install-ffmpeg"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000 if os.name == 'nt' else 0,
                             env=browser_platform.detached_host_environment(), close_fds=True)
        except OSError as exc:
            browser_ffmpeg._report(target, "error", error=str(exc), platform=platform)
            raise
        return {"started": True}

    def start_update(self, _payload):
        browser_update.read_installation()
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [sys.executable, os.path.abspath(__file__)])
        command.append("--auto-update")
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=0x08000000 if os.name == "nt" else 0,
                         env=browser_platform.detached_host_environment(),
                         close_fds=True)
        return {"started": True}


def _read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("Truncated native message")
        data.extend(chunk)
    return bytes(data)


def _run_protocol(reader=None, writer=None):
    reader = reader or sys.stdin.buffer
    writer = writer or sys.stdout.buffer
    host = BrowserHost()
    try:
        while True:
            header = reader.read(4)
            if not header:
                return
            if len(header) != 4:
                raise EOFError("Truncated native header")
            size = struct.unpack("<I", header)[0]
            if not 0 < size <= MAX_INBOUND:
                raise HostError("Invalid message size")
            message = json.loads(_read_exact(reader, size))
            try:
                result = host.dispatch(message)
                response = {"id": message.get("id"), "ok": True, "result": result}
            except Exception as exc:
                response = {"id": message.get("id"), "ok": False, "error": str(exc)}
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_OUTBOUND:
                encoded = json.dumps({"id": message.get("id"), "ok": False,
                                      "error": "Response too large"}).encode()
            writer.write(struct.pack("<I", len(encoded)))
            writer.write(encoded)
            writer.flush()
    finally:
        host.close()


def run(reader=None, writer=None):
    reader = reader or sys.stdin.buffer
    writer = writer or sys.stdout.buffer
    # Shared helpers log to stdout. Keep every diagnostic (including worker
    # threads) off Chrome's binary channel for the entire host lifetime.
    with redirect_stdout(sys.stderr):
        _run_protocol(reader, writer)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--install-browser"]:
        import browser_mac_install
        try:
            raise SystemExit(browser_mac_install.main(sys.argv[2:]))
        except (OSError, ValueError, RuntimeError) as exc:
            print("Browser setup failed: " + str(exc), file=sys.stderr)
            raise SystemExit(1)
    if sys.argv[1:] == ["--auto-update"]:
        raise SystemExit(browser_update.run_auto_update(config.get_app_version()))
    if sys.argv[1:] == ["--install-ffmpeg"]:
        try:
            browser_settings.initialize()
            import browser_ffmpeg
            browser_ffmpeg.provision(config.FFMPEG_PATH, timeout=600)
            raise SystemExit(0)
        except Exception as exc:
            log = browser_update.installation_file().parent / "ffmpeg-setup.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"FFmpeg setup failed: {exc}\n", encoding="utf-8")
            raise SystemExit(1)
    run()
