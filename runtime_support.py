# SPDX-License-Identifier: GPL-3.0-only

import datetime
import json
import ntpath
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass


DEFAULT_FILENAME_TEMPLATE = "{sequence}_{shot}_{type}_v{version}_{subversion}{format}"
DEFAULT_EXT_VIDEO = [".mp4", ".mov", ".avi", ".mkv"]
DEFAULT_EXT_IMAGE = [".png", ".jpg", ".jpeg", ".tiff"]
DEFAULT_IGNORED_FOLDERS = ["_MASTERS", ".DS_Store", "Thumbs.db"]
SERVER_CONFIG_FILENAME = "server_config.json"
SERVER_ROOT_ENV = "MEDIARENAMER_SERVER_ROOT"
APP_LOCAL_DIR_NAME = "MediaRenamer"
RUNTIME_CONTAINER_NAME = "MediaRenamer"
DEPLOY_LOCK_FILENAME = "MediaRenamer.deploy.lock"
CONFIG_BUNDLE_SCHEMA = "MediaRenamer.config.bundle"
CONFIG_BUNDLE_SCHEMA_VERSION = 1
SETTINGS_SCHEMA_VERSION = 3
DEFAULT_ADDITIONAL_CATEGORIES = []


class RuntimePathError(RuntimeError):
    """Raised when MediaRenamer cannot trust a runtime path."""


@dataclass(frozen=True)
class LocalRuntimePaths:
    """Writable per-user paths used by the standalone application."""

    root: str
    config: str
    state: str
    tools: str
    logs: str
    diagnostics: str


@dataclass(frozen=True)
class ServerRootResolution:
    root: str
    source: str
    source_path: str = ""


def default_settings() -> dict:
    return {
        "settings_schema_version": SETTINGS_SCHEMA_VERSION,
        "app_name": "MediaRenamer",
        "video_extensions": list(DEFAULT_EXT_VIDEO),
        "image_extensions": list(DEFAULT_EXT_IMAGE),
        "keyframe_folder": "KEYFRAMES",
        "video_folder": "VIDEO",
        "image_type_suffix": "IMG",
        "video_type_suffix": "VID",
        "additional_categories": [dict(item) for item in DEFAULT_ADDITIONAL_CATEGORIES],
        "subversion_enabled": "Yes",
        "skip_sequence": "No",
        "filename_template": DEFAULT_FILENAME_TEMPLATE,
        "ignored_folders": list(DEFAULT_IGNORED_FOLDERS),
        "scene_prefix": "",
        "//1": "scene_prefix is added automatically after the project path (e.g., 'vfx/shots'). Leave empty to disable.",
        "shot_prefix": "",
        "//2": "shot_prefix is added automatically after the sequence path. Leave empty to disable.",
        "target_prefix": "",
        "//3": "target_prefix is added automatically after the shot path before KEYFRAME/VIDEO folders. Leave empty to disable.",
    }


def empty_machine_state() -> dict:
    return {"projects": {}, "project_modes": {}, "ignored_folders": []}


def get_machine_id(hostname: str | None = None) -> str:
    raw_name = socket.gethostname() if hostname is None else str(hostname)
    machine_id = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name.strip())
    machine_id = machine_id.strip(" ._")
    return machine_id or "UNKNOWN_MACHINE"


def read_json_file(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def read_server_root_from_config(config_path: str) -> str:
    root = read_json_file(config_path).get("server_root", "")
    if isinstance(root, str) and root.strip():
        return ntpath.normpath(root.strip())
    return ""


def _identity_text(environ=None) -> str:
    env = environ if environ is not None else os.environ
    user = env.get("USERNAME") or env.get("USER") or "unknown-user"
    return f"user={user}, host={socket.gethostname()}"


def is_unc_path(path: str) -> bool:
    normalized = str(path or "").replace("/", "\\")
    return bool(re.match(r"^\\\\[^\\]+\\[^\\]+", normalized))


def _is_drive_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:\\", str(path or "").replace("/", "\\")))


def _is_private_or_build_path(path: str) -> bool:
    normalized = ntpath.normpath(str(path or "")).replace("/", "\\")
    lower = normalized.lower()
    if re.match(r"^[a-z]:\\users\\[^\\]+($|\\)", lower):
        return True
    private_names = {"desktop", "downloads", "documents", "onedrive"}
    parts = {part.lower() for part in normalized.split("\\")}
    if parts.intersection(private_names):
        return True
    return any(part.lower() in {"dist", "build"} or part.lower().startswith(("dist_", "build_")) for part in normalized.split("\\"))


def _format_path_error(message: str, root: str = "", source: str = "", source_path: str = "", environ=None) -> RuntimePathError:
    details = [message]
    if root:
        details.append(f"resolved root: {root}")
    if source:
        details.append(f"source: {source}")
    if source_path:
        details.append(f"source path: {source_path}")
    details.append(_identity_text(environ))
    details.append(
        "Fix: run MediaRenamer_Launcher.exe from the server UNC folder and keep "
        f"{SERVER_CONFIG_FILENAME} beside it with "
        '{ "server_root": "\\\\SERVER\\MediaRenamer" }.'
    )
    return RuntimePathError("\n".join(details))


def validate_server_root(
    root: str,
    *,
    source: str = "",
    source_path: str = "",
    require_unc: bool = False,
    reject_private: bool = True,
    environ=None,
) -> str:
    normalized = ntpath.normpath(str(root or "").strip().strip('"'))
    if not normalized:
        raise _format_path_error("MediaRenamer server root is not configured.", source=source, source_path=source_path, environ=environ)
    if not ntpath.isabs(normalized):
        raise _format_path_error("MediaRenamer server root must be an absolute path.", normalized, source, source_path, environ)
    if require_unc and not is_unc_path(normalized):
        if _is_drive_path(normalized):
            if _is_private_or_build_path(normalized):
                reason = "Production server_root cannot point to a local user/build folder."
            else:
                reason = "Production server_root must use a UNC path, not a local or mapped drive."
        else:
            reason = "Production server_root must use a UNC path such as \\\\SERVER\\MediaRenamer."
        raise _format_path_error(reason, normalized, source, source_path, environ)
    if reject_private and _is_private_or_build_path(normalized):
        raise _format_path_error("MediaRenamer server_root cannot point to a private user/profile or build folder.", normalized, source, source_path, environ)
    return normalized


def resolve_launcher_server_root(
    launcher_dir: str,
    *,
    environ=None,
    strict_production: bool = False,
    allow_dev_fallback: bool = True,
) -> ServerRootResolution:
    config_path = ntpath.join(launcher_dir, SERVER_CONFIG_FILENAME)
    configured_root = read_server_root_from_config(config_path)
    if configured_root:
        return ServerRootResolution(
            validate_server_root(
                configured_root,
                source="launcher server_config.json",
                source_path=config_path,
                require_unc=strict_production,
                reject_private=strict_production,
                environ=environ,
            ),
            "launcher server_config.json",
            config_path,
        )
    if strict_production or not allow_dev_fallback:
        raise _format_path_error(
            f"Missing {SERVER_CONFIG_FILENAME} beside the server launcher.",
            source="launcher directory",
            source_path=launcher_dir,
            environ=environ,
        )
    return ServerRootResolution(ntpath.normpath(launcher_dir), "dev launcher directory", launcher_dir)


def resolve_app_server_root(
    app_dir: str,
    local_base_dir: str,
    *,
    environ=None,
    allow_dev_fallback: bool = True,
) -> ServerRootResolution:
    env = environ if environ is not None else os.environ
    strict_production = not allow_dev_fallback
    env_root = env.get(SERVER_ROOT_ENV, "")
    if isinstance(env_root, str) and env_root.strip():
        return ServerRootResolution(
            validate_server_root(
                env_root,
                source=SERVER_ROOT_ENV,
                require_unc=strict_production,
                reject_private=strict_production,
                environ=env,
            ),
            SERVER_ROOT_ENV,
        )

    local_config = ntpath.join(local_base_dir, SERVER_CONFIG_FILENAME) if local_base_dir else ""
    local_root = read_server_root_from_config(local_config) if local_config else ""
    if local_root:
        return ServerRootResolution(
            validate_server_root(
                local_root,
                source="local AppData server_config.json",
                source_path=local_config,
                require_unc=strict_production,
                reject_private=strict_production,
                environ=env,
            ),
            "local AppData server_config.json",
            local_config,
        )

    app_config = ntpath.join(app_dir, SERVER_CONFIG_FILENAME)
    app_root = read_server_root_from_config(app_config)
    if app_root:
        return ServerRootResolution(
            validate_server_root(
                app_root,
                source="app server_config.json",
                source_path=app_config,
                require_unc=strict_production,
                reject_private=strict_production,
                environ=env,
            ),
            "app server_config.json",
            app_config,
        )

    if allow_dev_fallback:
        return ServerRootResolution(ntpath.normpath(app_dir), "dev app directory", app_dir)

    raise _format_path_error(
        "MediaRenamer server root is not configured. Run MediaRenamer_Launcher.exe from the server.",
        source="app startup",
        source_path=app_dir,
        environ=env,
    )


def get_local_base_dir(environ=None, *, required: bool = True) -> str:
    env = environ if environ is not None else os.environ
    local_app_data = env.get("LOCALAPPDATA", "")
    if not local_app_data:
        if required:
            raise RuntimePathError(
                "LOCALAPPDATA is not available. MediaRenamer cannot create its standalone runtime.\n"
                f"{_identity_text(env)}"
            )
        return ""
    return ntpath.normpath(ntpath.join(local_app_data, APP_LOCAL_DIR_NAME))


def get_local_runtime_paths(environ=None, *, required: bool = True) -> LocalRuntimePaths:
    """Return the complete standalone runtime layout below ``%LOCALAPPDATA%``."""
    root = get_local_base_dir(environ, required=required)
    return LocalRuntimePaths(
        root=root,
        config=ntpath.join(root, "Config") if root else "",
        state=ntpath.join(root, "State") if root else "",
        tools=ntpath.join(root, "Tools") if root else "",
        logs=ntpath.join(root, "Logs") if root else "",
        diagnostics=ntpath.join(root, "Diagnostics") if root else "",
    )


def emergency_diagnostics_dir(environ=None) -> str:
    env = environ if environ is not None else os.environ
    base = env.get("LOCALAPPDATA") or env.get("TEMP") or tempfile.gettempdir()
    return ntpath.normpath(ntpath.join(base, APP_LOCAL_DIR_NAME, "Diagnostics"))


def write_emergency_diagnostic(message: str, environ=None) -> str:
    diagnostics_dir = emergency_diagnostics_dir(environ)
    try:
        os.makedirs(diagnostics_dir, exist_ok=True)
        today = datetime.datetime.now()
        path = ntpath.join(diagnostics_dir, today.strftime("%Y-%m-%d.txt"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{today.strftime('%H:%M:%S')} {message}\n")
        return path
    except Exception:
        return ""


def _lock_owner_status(lock_path: str) -> str:
    """Return ``alive``, ``dead``, or ``unknown`` for lock ownership."""
    try:
        with open(lock_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("host") != socket.gethostname():
            return "unknown"
        pid = int(payload.get("pid", 0))
        if pid <= 0:
            return "unknown"
        if pid == os.getpid():
            # Another thread in this process may legitimately own the lock.
            return "alive"
        if os.name == "nt":
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if process:
                ctypes.windll.kernel32.CloseHandle(process)
                return "alive"
            return (
                "dead"
                if ctypes.windll.kernel32.GetLastError() == 87
                else "unknown"
            )
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "dead"
        except (PermissionError, OSError):
            return "unknown"
        return "alive"
    except Exception:
        return "unknown"


def _lock_owner_is_dead(lock_path: str) -> bool:
    """Backward-compatible predicate used by older tests/integrations."""
    return _lock_owner_status(lock_path) == "dead"


def _renew_lock_lease(lock_path: str, token: str, stop_event, interval: float) -> None:
    """Keep a live cross-machine lock younger than its stale threshold."""
    while not stop_event.wait(interval):
        try:
            with open(lock_path, "r", encoding="utf-8") as handle:
                current = json.load(handle)
            if current.get("token") != token:
                return
            os.utime(lock_path, None)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return


_RECOVERY_GUARD_PROCESS_LOCK = threading.Lock()


def _lock_observation(lock_path: str):
    """Return immutable identity data for the currently named lock file."""
    try:
        with open(lock_path, "rb") as handle:
            content = handle.read()
            stat = os.fstat(handle.fileno())
        return (
            getattr(stat, "st_dev", 0),
            getattr(stat, "st_ino", 0),
            stat.st_size,
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            content,
        )
    except (FileNotFoundError, OSError):
        return None


@contextmanager
def _lock_recovery_guard(
    lock_path: str,
    *,
    timeout: float,
    poll_interval: float,
):
    """Serialize stale recovery with a crash-safe operating-system file lock."""
    guard_path = f"{lock_path}.recovery.guard"
    parent = os.path.dirname(guard_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    started = time.monotonic()
    with _RECOVERY_GUARD_PROCESS_LOCK:
        handle = open(guard_path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())

            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(
                            handle.fileno(),
                            msvcrt.LK_NBLCK,
                            1,
                        )
                        break
                    except OSError:
                        if time.monotonic() - started >= timeout:
                            raise TimeoutError(
                                f"Timed out waiting for lock recovery guard: "
                                f"{guard_path}"
                            )
                        time.sleep(max(0.01, poll_interval))
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(
                            handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        break
                    except BlockingIOError:
                        if time.monotonic() - started >= timeout:
                            raise TimeoutError(
                                f"Timed out waiting for lock recovery guard: "
                                f"{guard_path}"
                            )
                        time.sleep(max(0.01, poll_interval))
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def exclusive_file_lock(
    lock_path: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.1,
    stale_seconds: float = 600.0,
):
    """Acquire a cross-process lock file with conservative stale-lock recovery.

    A unique ownership token prevents one process from deleting a successor's
    lock during cleanup. Locks are considered stale by age, or immediately when
    they belong to a dead process on this machine.
    """
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "thread": threading.get_ident(),
        "created": time.time(),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    started = time.monotonic()
    descriptor = None
    lease_stop = threading.Event()
    lease_thread = None
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, encoded)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            break
        except FileExistsError:
            observed = _lock_observation(lock_path)
            if observed is None:
                continue
            stale = False
            try:
                stale = stale_seconds >= 0 and time.time() - os.path.getmtime(lock_path) > stale_seconds
            except OSError:
                continue
            owner_status = _lock_owner_status(lock_path)
            if owner_status == "dead" or (
                stale and owner_status != "alive"
            ):
                try:
                    remaining = max(
                        0.01,
                        timeout - (time.monotonic() - started),
                    )
                    removed = False
                    with _lock_recovery_guard(
                        lock_path,
                        timeout=remaining,
                        poll_interval=poll_interval,
                    ):
                        # A successor may have replaced the stale path while
                        # this contender waited for the recovery guard. Never
                        # delete a file other than the one actually observed.
                        if _lock_observation(lock_path) == observed:
                            stale_now = False
                            try:
                                stale_now = (
                                    stale_seconds >= 0
                                    and time.time()
                                    - os.path.getmtime(lock_path)
                                    > stale_seconds
                                )
                            except OSError:
                                stale_now = False
                            owner_status_now = _lock_owner_status(lock_path)
                            if owner_status_now == "dead" or (
                                stale_now
                                and owner_status_now != "alive"
                            ):
                                os.remove(lock_path)
                                removed = True
                    if removed:
                        continue
                except TimeoutError:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError(
                            f"Timed out waiting for lock: {lock_path}"
                        )
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
            if time.monotonic() - started >= timeout:
                raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
            time.sleep(max(0.01, poll_interval))

    if stale_seconds > 0:
        lease_interval = max(0.05, min(30.0, stale_seconds / 3.0))
        lease_thread = threading.Thread(
            target=_renew_lock_lease,
            args=(lock_path, token, lease_stop, lease_interval),
            name="MediaRenamerFileLockLease",
            daemon=True,
        )
        lease_thread.start()

    try:
        yield lock_path
    finally:
        lease_stop.set()
        if lease_thread is not None:
            lease_thread.join(timeout=1.0)
        if descriptor is not None:
            os.close(descriptor)
        try:
            with _lock_recovery_guard(
                lock_path,
                timeout=1.0,
                poll_interval=min(0.05, max(0.01, poll_interval)),
            ):
                with open(lock_path, "r", encoding="utf-8") as handle:
                    current = json.load(handle)
                if current.get("token") == token:
                    os.remove(lock_path)
        except (
            FileNotFoundError,
            OSError,
            ValueError,
            TypeError,
            TimeoutError,
        ):
            pass


@contextmanager
def deploy_file_lock(runtime_dir: str, timeout: float = 60.0, stale_seconds: float = 600.0):
    lock_path = ntpath.join(runtime_dir, DEPLOY_LOCK_FILENAME)
    with exclusive_file_lock(
        lock_path,
        timeout=timeout,
        poll_interval=0.25,
        stale_seconds=stale_seconds,
    ):
        yield lock_path


def copy_tree_missing(source_dir: str, target_dir: str):
    """Copy source files into missing destination paths without overwriting."""
    if not source_dir or not target_dir or not os.path.isdir(source_dir):
        return

    for source_root, dirs, files in os.walk(source_dir):
        relative = os.path.relpath(source_root, source_dir)
        destination_root = target_dir if relative == "." else os.path.join(target_dir, relative)
        os.makedirs(destination_root, exist_ok=True)
        for directory in dirs:
            os.makedirs(os.path.join(destination_root, directory), exist_ok=True)
        for filename in files:
            source_path = os.path.join(source_root, filename)
            destination_path = os.path.join(destination_root, filename)
            if not os.path.exists(destination_path):
                shutil.copy2(source_path, destination_path)
