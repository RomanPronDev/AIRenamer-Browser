# SPDX-License-Identifier: GPL-3.0-only
"""Chrome Native Messaging host for AIRenamer.

The browser sends project/shot/category identifiers; only this process resolves
filesystem paths. stdout is reserved for length-prefixed protocol messages.
"""
from __future__ import annotations

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

MAX_INBOUND = 64 * 1024 * 1024
MAX_OUTBOUND = 1024 * 1024
MAX_CHUNK = 256 * 1024
MAX_IMAGE_BYTES = 600 * 1024


class HostError(ValueError):
    pass


def _component(value: object, name: str) -> str:
    if not isinstance(value, str) or not config._is_windows_safe_component(value):
        raise HostError(f"Invalid {name}")
    return value


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
    mode = config.detect_project_layout(path, config.get_project_modes().get(name))
    return path, mode


def _navigation(project: str, sequence: str | None = None) -> tuple[str, str, str]:
    project_path, mode = _project(project)
    if mode == config.PROJECT_LAYOUT_SHOTS:
        if sequence:
            raise HostError("Project has no sequence level")
        return project_path, mode, config.get_project_shot_root(project_path)
    if not sequence:
        return project_path, mode, config.get_scene_root(project_path)
    sequence = _component(sequence, "sequence")
    root = config.get_scene_root(project_path)
    if sequence not in _children(root):
        raise HostError("Unknown sequence")
    return project_path, mode, config.get_shot_root(config.get_scene_path(project_path, sequence))


def _shot(project: str, sequence: str | None, shot: str) -> tuple[str, str]:
    shot = _component(shot, "shot")
    _, mode, root = _navigation(project, sequence)
    if mode == config.PROJECT_LAYOUT_SEQUENCES and not sequence:
        raise HostError("Sequence required")
    if shot not in _children(root):
        raise HostError("Unknown shot")
    return os.path.join(root, shot), mode


def _category(category_id: str):
    category_id = _component(category_id, "category")
    category = config.get_media_category(category_id)
    if not category or category.id != category_id:
        raise HostError("Unknown category")
    return category


def _file(project: str, sequence: str | None, shot: str, category_id: str, name: str):
    name = _component(name, "file")
    shot_path, _ = _shot(project, sequence, shot)
    category = _category(category_id)
    path = os.path.join(config.get_target_base(shot_path), category.folder, name)
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
    def __init__(self):
        config.load_settings()
        self.transfers: dict[str, dict] = {}
        self.conversions: dict[str, dict] = {}
        self._conversion_lock = threading.Lock()
        self._drag_server = None
        self._drag_thread = None
        self._drag_entries: dict[str, tuple[str, float, bool]] = {}

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
        return {
            "projects": [
                {"name": name, "path": path,
                 "layout": config.detect_project_layout(path, modes.get(name)),
                 "available": os.path.isdir(path)}
                for name, path in sorted(projects.items(), key=lambda item: item[0].casefold())
            ],
            "categories": [
                {"id": cat.id, "folder": cat.folder, "mediaType": cat.media_type, "typeSuffix": cat.type_suffix}
                for cat in config.get_media_categories()
            ],
        }

    def preferences(self, _=None):
        names = [item for item in config.get_machine_ignored_folders()
                 if isinstance(item, str) and not ("\\" in item or "/" in item)]
        return {"ignoredNames": sorted(set(names) | {"_shotcode"}, key=str.casefold)}

    def set_ignored_names(self, payload):
        names = payload.get("names")
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
            # User gesture in the extension initiates the native OS picker.
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                folder = filedialog.askdirectory(
                    parent=root, title="Select project folder", mustexist=True
                )
            finally:
                root.destroy()
        if not folder:
            return {"cancelled": True}
        folder = os.path.normpath(os.path.abspath(folder))
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
        modes[name] = config.detect_project_layout(folder)
        config.save_projects(projects, project_modes=modes)
        return {"name": name, "layout": modes[name], "path": folder}

    def navigation(self, payload):
        name = payload["project"]
        path, mode = _project(name)
        if not os.path.isdir(path):
            raise HostError("Project folder unavailable")
        sequences = _children(config.get_scene_root(path)) if mode == config.PROJECT_LAYOUT_SEQUENCES else []
        sequence = payload.get("sequence") or None
        if mode == config.PROJECT_LAYOUT_SEQUENCES and sequence is None:
            return {"layout": mode, "sequences": sequences, "shots": []}
        _, _, root = _navigation(name, sequence)
        return {"layout": mode, "sequences": sequences, "shots": _children(root)}

    def files(self, payload):
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        target = config.get_target_base(shot_path)
        result = []
        for category in config.get_media_categories():
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
                record["hasPsd"] = category.id == "keyframes" and _psd_matches(entry.name, psds)
                result.append(record)
        result.sort(key=lambda item: item["modified"], reverse=True)
        return {"files": result, "path": target}

    def import_file(self, payload):
        source = payload.get("source")
        if not isinstance(source, str) or not os.path.isfile(source):
            raise HostError("Source file unavailable")
        file_type = utils.get_file_type(source)
        if not file_type:
            raise HostError("Unsupported media type")
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        category = _category(payload["category"]) if payload.get("category") else config.get_category_for_file_type(file_type)
        if category.file_type != file_type:
            raise HostError("File does not match destination category")
        target = utils.get_target_directory(shot_path, category=category)
        new_path, new_name, _ = utils.copy_and_rename_file(
            source, target, payload.get("sequence") or "", payload["shot"],
            file_type, category=category,
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
        if os.path.normcase(os.path.abspath(target_shot)) == os.path.normcase(
            os.path.abspath(os.path.dirname(os.path.dirname(src)))
        ):
            raise HostError("File is already in this shot")
        target = utils.get_target_directory(target_shot, category=category)
        source_sequence = utils.find_converted_sequence_folder(src) if category.media_type == config.MEDIA_TYPE_VIDEO else ""
        new_path, new_name, _ = utils.copy_and_rename_file(
            src, target, payload.get("sequence") or "", payload["shot"],
            category.file_type, category=category,
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
            local_path = utils.resolve_ffmpeg(
                target_path=config.FFMPEG_PATH, allow_frozen_download=True, timeout=600,
            )
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
        if payload.get("file"):
            path, category = _file(payload["project"], payload.get("sequence"),
                                   payload["shot"], payload["category"], payload["file"])
            if payload.get("convertedFolder"):
                if category.media_type != config.MEDIA_TYPE_VIDEO:
                    raise HostError("PNG sequence is available only for video")
                directory = utils.find_converted_sequence_folder(path)
                if not directory or not os.path.isdir(directory):
                    raise HostError("PNG sequence folder unavailable")
                subprocess.Popen(["explorer.exe", "/select,", directory])
                return {"opened": True}
            if not os.path.isdir(os.path.dirname(path)):
                raise HostError("Folder unavailable")
            subprocess.Popen(["explorer.exe", "/select,", path])
            return {"opened": True}
        shot_path, _ = _shot(payload["project"], payload.get("sequence"), payload["shot"])
        directory = config.get_target_base(shot_path)
        if not os.path.isdir(directory):
            raise HostError("Folder unavailable")
        os.startfile(directory)
        return {"opened": True}

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
            "ping": lambda _: {"version": config.get_app_version()},
            "start_update": self.start_update,
            "projects": self.projects,
            "preferences": self.preferences,
            "set_ignored_names": self.set_ignored_names,
            "add_project": self.add_project,
            "navigation": self.navigation,
            "files": self.files,
            "import_file": self.import_file,
            "rename_file": self.rename_file,
            "move_file": self.move_file,
            "convert_begin": self.convert_begin,
            "convert_status": self.convert_status,
            "convert_cancel": self.convert_cancel,
            "open_folder": self.open_folder,
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

    def start_update(self, _payload):
        browser_update.read_installation()
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [sys.executable, os.path.abspath(__file__)])
        command.append("--auto-update")
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=0x08000000 if os.name == "nt" else 0,
                         env=utils.sanitized_subprocess_environment(),
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


def run(reader=None, writer=None):
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


if __name__ == "__main__":
    if sys.argv[1:] == ["--auto-update"]:
        raise SystemExit(browser_update.run_auto_update(config.get_app_version()))
    if sys.argv[1:] == ["--install-ffmpeg"]:
        try:
            utils.resolve_ffmpeg(target_path=config.FFMPEG_PATH,
                                 allow_frozen_download=True, timeout=600)
            raise SystemExit(0)
        except Exception as exc:
            log = browser_update.installation_file().parent / "ffmpeg-setup.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"FFmpeg setup failed: {exc}\n", encoding="utf-8")
            raise SystemExit(1)
    run()
