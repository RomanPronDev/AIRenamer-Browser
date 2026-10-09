import json
import os
import datetime
import threading
import time
from pathlib import Path

import pytest

import config
from runtime_support import (
    CONFIG_BUNDLE_SCHEMA,
    CONFIG_BUNDLE_SCHEMA_VERSION,
    RuntimePathError,
    get_local_base_dir,
    get_local_runtime_paths,
)


def _write_legal_material_source(root: Path, *, license_text="project license"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "LICENSE").write_text(license_text, encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.md").write_text(
        "third-party notices",
        encoding="utf-8",
    )
    (root / "SOURCE_OFFER.md").write_text(
        "source availability",
        encoding="utf-8",
    )
    third_party = root / "third_party_licenses"
    third_party.mkdir(exist_ok=True)
    (third_party / "Dependency-A.txt").write_text(
        "dependency A license",
        encoding="utf-8",
    )
    (third_party / "Dependency-B.txt").write_text(
        "dependency B license",
        encoding="utf-8",
    )


def _patch_standalone_paths(monkeypatch, tmp_path, machine_id="WORKSTATION01"):
    runtime_dir = tmp_path / "MediaRenamer"
    config_dir = runtime_dir / "Config"
    state_dir = runtime_dir / "State"
    paths = {
        "LOCAL_BASE_DIR": runtime_dir,
        "SERVER_ROOT": runtime_dir,
        "RUNTIME_DIR": runtime_dir,
        "CONFIG_DIR": config_dir,
        "STATE_DIR": state_dir,
        "LOGS_DIR": runtime_dir / "Logs",
        "TOOLS_DIR": runtime_dir / "Tools",
        "DIAGNOSTICS_DIR": runtime_dir / "Diagnostics",
        "BACKUPS_DIR": config_dir / "Backups",
        "FFMPEG_PATH": runtime_dir / "Tools" / "ffmpeg.exe",
        "PROJECTS_DIR": state_dir / "Projects",
        "CACHE_DIR": state_dir / "Cache",
        "HISTORY_DIR": state_dir / "History",
        "PROJECTS_FILE_LEGACY": config_dir / "projects.txt",
        "PROJECTS_FILE": config_dir / "projects.json",
        "EXTENSIONS_FILE": config_dir / "extensions.json",
        "FOLDERS_FILE": config_dir / "folders.json",
        "SETTINGS_FILE": config_dir / "settings.json",
        "CACHE_FILE": config_dir / "cache.json",
        "HISTORY_FILE": config_dir / "history.json",
        "HELP_FILE": config_dir / "help.html",
        "SERVER_CONFIG_FILE": "",
        "BUNDLE_LOCK_FILE": state_dir / "config-bundle.lock",
    }
    for name, value in paths.items():
        monkeypatch.setattr(config, name, str(value) if isinstance(value, Path) else value)
    monkeypatch.setattr(config, "SERVER_ROOT_ERROR", "")
    monkeypatch.setattr(config, "get_machine_id", lambda hostname=None: machine_id)
    return runtime_dir


def test_resolve_server_root_ignores_legacy_environment_and_bootstrap(tmp_path):
    app_dir = tmp_path / "app"
    local_dir = tmp_path / "local"
    app_dir.mkdir()
    local_dir.mkdir()
    (local_dir / "server_config.json").write_text(
        json.dumps({"server_root": str(tmp_path / "from_local")}),
        encoding="utf-8",
    )

    resolved = config.resolve_server_root(
        str(app_dir),
        environ={config.SERVER_ROOT_ENV: str(tmp_path / "from_env")},
        local_base_dir=str(local_dir),
    )

    assert resolved == str(local_dir)
    assert config.SERVER_ROOT_SOURCE == "local AppData standalone runtime"


def test_resolve_server_root_uses_localappdata_in_dev_and_frozen_modes(tmp_path):
    local_app_data = tmp_path / "Local"
    resolved = config.resolve_server_root(
        str(tmp_path / "app"),
        environ={"LOCALAPPDATA": str(local_app_data)},
        allow_dev_fallback=False,
    )

    assert Path(resolved) == local_app_data / "MediaRenamer"


def test_resolve_server_root_requires_localappdata():
    with pytest.raises(RuntimePathError, match="LOCALAPPDATA"):
        config.resolve_server_root(
            "C:\\app",
            environ={},
            allow_dev_fallback=False,
        )


def test_local_runtime_paths_have_standalone_layout(tmp_path):
    paths = get_local_runtime_paths({"LOCALAPPDATA": str(tmp_path)})

    assert Path(paths.root) == tmp_path / "MediaRenamer"
    assert Path(paths.config) == tmp_path / "MediaRenamer" / "Config"
    assert Path(paths.state) == tmp_path / "MediaRenamer" / "State"
    assert Path(paths.tools) == tmp_path / "MediaRenamer" / "Tools"
    assert Path(paths.logs) == tmp_path / "MediaRenamer" / "Logs"
    assert Path(paths.diagnostics) == tmp_path / "MediaRenamer" / "Diagnostics"


def test_config_runtime_paths_include_user_accessible_licenses(
    monkeypatch,
    tmp_path,
):
    runtime_dir = tmp_path / "MediaRenamer"
    monkeypatch.setattr(config, "RUNTIME_DIR", str(runtime_dir))

    assert Path(config.get_runtime_paths()["licenses"]) == runtime_dir / "Licenses"


def test_local_base_dir_requires_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(RuntimePathError, match="LOCALAPPDATA"):
        get_local_base_dir(required=True)


def test_setup_directories_requires_local_runtime(monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_DIR", "")

    try:
        config.setup_directories()
    except RuntimeError as exc:
        assert "LOCALAPPDATA" in str(exc)
    else:
        raise AssertionError("setup_directories should reject a missing local runtime")


def test_atomic_write_json_replaces_file_contents(tmp_path):
    target = tmp_path / "settings.json"
    config.atomic_write_json(str(target), {"value": 1})
    config.atomic_write_json(str(target), {"value": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 2}
    assert not Path(f"{target}.lock").exists()


def test_update_json_file_reads_modifies_and_writes_under_lock(tmp_path):
    target = tmp_path / "history.json"

    def updater(data):
        data["items"] = data.get("items", []) + ["entry"]
        return data

    updated = config.update_json_file(str(target), {}, updater)

    assert updated == {"items": ["entry"]}
    assert json.loads(target.read_text(encoding="utf-8")) == {"items": ["entry"]}
    assert not Path(f"{target}.lock").exists()


def test_log_action_writes_to_server_logs(monkeypatch, tmp_path):
    config.clear_runtime_warnings()
    logs_dir = tmp_path / "MediaRenamer" / "Logs"
    monkeypatch.setattr(config, "LOGS_DIR", str(logs_dir))

    assert config.log_action("hello from test") is True

    log_file = logs_dir / f"{datetime.datetime.now():%Y-%m-%d}.txt"
    assert log_file.exists()
    assert "hello from test" in log_file.read_text(encoding="utf-8")
    assert config.get_runtime_warnings() == []


def test_log_action_failure_creates_emergency_diagnostic(monkeypatch, tmp_path):
    config.clear_runtime_warnings()
    local_app_data = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    blocked_logs_path = tmp_path / "blocked"
    blocked_logs_path.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(config, "LOGS_DIR", str(blocked_logs_path))

    assert config.log_action("will fall back", is_error=True) is False

    diagnostics = local_app_data / "MediaRenamer" / "Diagnostics"
    diagnostic_files = list(diagnostics.glob("*.txt"))
    assert diagnostic_files
    assert "will fall back" in diagnostic_files[0].read_text(encoding="utf-8")
    assert config.DEGRADED_MODE is True
    assert config.get_runtime_warnings()


def test_default_settings_use_fixed_app_name():
    assert config.default_settings()["app_name"] == "MediaRenamer"


def test_load_settings_normalizes_custom_app_name(monkeypatch, tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"app_name": "Studio Media Tool"}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(config, "APP_DISPLAY_NAME", "MediaRenamer")
    monkeypatch.setattr(config, "get_machine_ignored_folders", lambda: [])

    config.load_settings()

    assert config.APP_DISPLAY_NAME == "MediaRenamer"


def test_prefix_helpers_build_full_structure_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCENE_PREFIX", "04_ONLINE\\AI")
    monkeypatch.setattr(config, "SHOT_PREFIX", "shots")
    monkeypatch.setattr(config, "TARGET_PREFIX", "publish\\review")

    project_path = str(tmp_path / "ProjectA")
    scene_path = config.get_scene_path(project_path, "SC010")
    shot_path = config.get_shot_path(scene_path, "SH020")
    target_base = config.get_target_base(shot_path)

    expected = tmp_path / "ProjectA" / "04_ONLINE" / "AI" / "SC010" / "shots" / "SH020" / "publish" / "review"
    assert Path(target_base) == expected


def test_project_shot_root_skips_sequence_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCENE_PREFIX", "vfx")
    monkeypatch.setattr(config, "SHOT_PREFIX", "shots")

    expected = tmp_path / "ProjectA" / "vfx" / "shots"
    assert Path(config.get_project_shot_root(str(tmp_path / "ProjectA"))) == expected


def test_normalize_prefix_strips_absolute_parts():
    assert config.normalize_prefix(" C:\\server\\prefix\\shots\\ ") == os.path.normpath("server\\prefix\\shots")
    assert config.normalize_prefix("\\\\prefix\\shots\\") == os.path.normpath("prefix\\shots")


def test_parse_yes_no_accepts_settings_values():
    assert config.parse_yes_no("Yes") is True
    assert config.parse_yes_no("No") is False
    assert config.parse_yes_no(True) is True
    assert config.parse_yes_no(False) is False
    assert config.parse_yes_no("unexpected", default=False) is False


def test_normalize_folder_name_keeps_custom_target_folder_names():
    assert config.normalize_folder_name("  KEYFRAME_REVIEW\\ ", "KEYFRAMES") == "KEYFRAME_REVIEW"
    assert config.normalize_folder_name("", "KEYFRAMES") == "KEYFRAMES"

def test_type_suffix_helpers_keep_defaults_and_custom_values(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_TYPE_SUFFIX", "KEY")
    monkeypatch.setattr(config, "VIDEO_TYPE_SUFFIX", "MOV")

    assert config.normalize_type_suffix("", config.DEFAULT_IMAGE_TYPE_SUFFIX) == "IMG"
    assert config.get_type_suffix(config.TYPE_IMG) == "KEY"
    assert config.get_type_suffix(config.TYPE_VID) == "MOV"


def test_get_machine_id_sanitizes_for_filename():
    assert config.get_machine_id("WORK/STATION:01*?") == "WORK_STATION_01"
    assert config.get_machine_id("  ...  ") == "UNKNOWN_MACHINE"


def test_get_projects_file_uses_machine_specific_server_path(monkeypatch, tmp_path):
    projects_dir = tmp_path / "_config" / "projects"
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))

    assert Path(config.get_projects_file("WORKSTATION01")) == projects_dir / "WORKSTATION01.json"


def test_get_projects_creates_empty_machine_file(monkeypatch, tmp_path):
    projects_dir = tmp_path / "_config" / "projects"
    legacy_file = tmp_path / "_config" / "projects.json"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(json.dumps({"Legacy": "V:\\Legacy"}), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))

    projects = config.get_projects("WORKSTATION01")

    machine_file = projects_dir / "WORKSTATION01.json"
    assert projects == {}
    assert json.loads(machine_file.read_text(encoding="utf-8")) == {"projects": {}, "project_modes": {}, "ignored_folders": []}


def test_get_projects_migrates_legacy_machine_map(monkeypatch, tmp_path):
    projects_dir = tmp_path / "_config" / "projects"
    machine_file = projects_dir / "WORKSTATION01.json"
    machine_file.parent.mkdir(parents=True)
    machine_file.write_text(json.dumps({"Legacy": "V:\\Legacy"}), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))

    assert config.get_projects("WORKSTATION01") == {"Legacy": "V:\\Legacy"}
    assert json.loads(machine_file.read_text(encoding="utf-8")) == {
        "projects": {"Legacy": "V:\\Legacy"},
        "ignored_folders": [],
        "project_modes": {},
    }


def test_add_ignored_folder_writes_machine_state(monkeypatch, tmp_path):
    projects_dir = tmp_path / "_config" / "projects"
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))
    monkeypatch.setattr(config, "IGNORED_FOLDERS", [])
    monkeypatch.setattr(config, "IGNORED_PATHS", set())

    ignored = str(tmp_path / "IgnoreMe")
    config.add_ignored_folder(ignored)

    data = json.loads((projects_dir / f"{config.get_machine_id()}.json").read_text(encoding="utf-8"))
    assert data["ignored_folders"] == [os.path.normpath(ignored)]


def test_save_projects_writes_only_machine_file(monkeypatch, tmp_path):
    projects_dir = tmp_path / "_config" / "projects"
    legacy_file = tmp_path / "_config" / "projects.json"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(json.dumps({"Legacy": "V:\\Legacy"}), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))

    config.save_projects({"Local": "V:\\Local"}, "WORKSTATION01")

    machine_file = projects_dir / "WORKSTATION01.json"
    assert json.loads(machine_file.read_text(encoding="utf-8")) == {
        "projects": {"Local": "V:\\Local"},
        "ignored_folders": [],
        "project_modes": {},
    }
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == {"Legacy": "V:\\Legacy"}


def test_setup_directories_does_not_overwrite_or_delete_existing_config(monkeypatch, tmp_path):
    config_dir = tmp_path / "_config"
    logs_dir = tmp_path / "Logs"
    tools_dir = tmp_path / "_tools"
    server_config_file = tmp_path / "server_config.json"
    settings_file = config_dir / "settings.json"
    help_file = config_dir / "help.html"
    cache_file = config_dir / "cache.json"
    history_file = config_dir / "history.json"
    projects_file = config_dir / "projects" / "WORKSTATION01.json"
    extensions_file = config_dir / "extensions.json"
    folders_file = config_dir / "folders.json"

    projects_file.parent.mkdir(parents=True)
    server_config_file.write_text(json.dumps({"server_root": "custom"}), encoding="utf-8")
    settings_file.write_text(json.dumps({"keyframe_folder": "CUSTOM_KF"}), encoding="utf-8")
    help_file.write_text("custom help", encoding="utf-8")
    cache_file.write_text(json.dumps({"cache": True}), encoding="utf-8")
    history_file.write_text(json.dumps({"host": [{"path": "P:\\Shot"}]}), encoding="utf-8")
    projects_file.write_text(json.dumps({"Project": "P:\\Project"}), encoding="utf-8")
    extensions_file.write_text(json.dumps({"video_extensions": [".custom"]}), encoding="utf-8")
    folders_file.write_text(json.dumps({"video_folder": "CUSTOM_VIDEO"}), encoding="utf-8")

    monkeypatch.setattr(config, "SERVER_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "State"))
    monkeypatch.setattr(config, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(config, "TOOLS_DIR", str(tools_dir))
    monkeypatch.setattr(config, "DIAGNOSTICS_DIR", str(tmp_path / "Diagnostics"))
    monkeypatch.setattr(config, "BACKUPS_DIR", str(config_dir / "Backups"))
    monkeypatch.setattr(config, "BUNDLE_LOCK_FILE", str(tmp_path / "State" / "config-bundle.lock"))
    monkeypatch.setattr(config, "LEGACY_CONFIG_DIR", str(tmp_path / "legacy_config"))
    monkeypatch.setattr(config, "LEGACY_LOGS_DIR", str(tmp_path / "legacy_logs"))
    monkeypatch.setattr(config, "LEGACY_TOOLS_DIR", str(tmp_path / "legacy_tools"))
    monkeypatch.setattr(config, "PROJECTS_DIR", str(config_dir / "projects"))
    monkeypatch.setattr(config, "CACHE_DIR", str(config_dir / "cache"))
    monkeypatch.setattr(config, "HISTORY_DIR", str(config_dir / "history"))
    monkeypatch.setattr(config, "SERVER_CONFIG_FILE", str(server_config_file))
    monkeypatch.setattr(config, "PROJECTS_FILE_LEGACY", str(config_dir / "projects.txt"))
    monkeypatch.setattr(config, "PROJECTS_FILE", str(config_dir / "projects.json"))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(config, "CACHE_FILE", str(cache_file))
    monkeypatch.setattr(config, "HISTORY_FILE", str(history_file))
    monkeypatch.setattr(config, "HELP_FILE", str(help_file))
    monkeypatch.setattr(config, "EXTENSIONS_FILE", str(extensions_file))
    monkeypatch.setattr(config, "FOLDERS_FILE", str(folders_file))
    monkeypatch.setattr(config.socket, "gethostname", lambda: "WORKSTATION01")

    config.setup_directories()

    assert json.loads(server_config_file.read_text(encoding="utf-8")) == {"server_root": "custom"}
    updated_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert updated_settings["keyframe_folder"] == "CUSTOM_KF"
    assert updated_settings["app_name"] == "MediaRenamer"
    assert updated_settings["video_folder"] == "VIDEO"
    assert updated_settings["filename_template"] == config.DEFAULT_FILENAME_TEMPLATE
    settings_backups = list(config_dir.glob("settings.backup.*.json"))
    assert len(settings_backups) == 1
    assert json.loads(settings_backups[0].read_text(encoding="utf-8")) == {"keyframe_folder": "CUSTOM_KF"}
    assert help_file.read_text(encoding="utf-8") == "custom help"
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"cache": True}
    assert json.loads(history_file.read_text(encoding="utf-8")) == {"host": [{"path": "P:\\Shot"}]}
    assert json.loads(projects_file.read_text(encoding="utf-8")) == {
        "projects": {"Project": "P:\\Project"},
        "ignored_folders": [],
        "project_modes": {},
    }
    assert extensions_file.exists()
    assert folders_file.exists()
    assert logs_dir.is_dir()
    assert tools_dir.is_dir()


def test_migrate_settings_defaults_skips_backup_when_keys_exist(monkeypatch, tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(config.default_settings()), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

    assert config.migrate_settings_defaults() is False
    assert list(tmp_path.glob("settings.backup.*.json")) == []


def test_setup_directories_creates_full_standalone_runtime(monkeypatch, tmp_path):
    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path, "WORK_STATION_01")
    config_dir = runtime_dir / "Config"

    config.setup_directories()

    assert (config_dir / "settings.json").exists()
    assert not (config_dir / "help.html").exists()
    assert json.loads((runtime_dir / "State" / "Cache" / "WORK_STATION_01.json").read_text(encoding="utf-8")) == {}
    assert json.loads((runtime_dir / "State" / "History" / "WORK_STATION_01.json").read_text(encoding="utf-8")) == []
    assert json.loads((runtime_dir / "State" / "Projects" / "WORK_STATION_01.json").read_text(encoding="utf-8")) == {
        "projects": {},
        "ignored_folders": [],
        "project_modes": {},
    }
    for folder in ("Config", "State", "Tools", "Logs", "Diagnostics", "Licenses"):
        assert (runtime_dir / folder).is_dir()
    assert not (runtime_dir / "server_config.json").exists()


def test_setup_materializes_updates_and_reuses_legal_files(monkeypatch, tmp_path):
    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    _write_legal_material_source(source_root, license_text="project license v1")
    monkeypatch.setattr(config, "APP_DIR", str(source_root))
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    monkeypatch.setattr(config, "write_emergency_diagnostic", lambda *_: "")

    destination = runtime_dir / "Licenses"
    destination.mkdir(parents=True)
    (destination / "user-notes.txt").write_text("keep me", encoding="utf-8")
    destination_third_party = destination / "third_party_licenses"
    destination_third_party.mkdir()
    (destination_third_party / "local-note.txt").write_text(
        "also keep me",
        encoding="utf-8",
    )

    config.setup_directories()

    assert (destination / "LICENSE").read_text(encoding="utf-8") == "project license v1"
    assert (
        destination / "THIRD_PARTY_NOTICES.md"
    ).read_text(encoding="utf-8") == "third-party notices"
    assert (
        destination / "SOURCE_OFFER.md"
    ).read_text(encoding="utf-8") == "source availability"
    assert (
        destination_third_party / "Dependency-A.txt"
    ).read_text(encoding="utf-8") == "dependency A license"
    assert (destination / "user-notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (
        destination_third_party / "local-note.txt"
    ).read_text(encoding="utf-8") == "also keep me"

    real_replace = config.os.replace
    legal_replacements = []

    def observe_replace(source, target):
        if config._path_is_within(str(destination), str(target)):
            legal_replacements.append(os.path.normpath(str(target)))
        return real_replace(source, target)

    monkeypatch.setattr(config.os, "replace", observe_replace)
    config.setup_directories()
    assert legal_replacements == []

    (source_root / "LICENSE").write_text("project license v2", encoding="utf-8")
    config.setup_directories()
    assert (destination / "LICENSE").read_text(encoding="utf-8") == "project license v2"
    assert legal_replacements == [os.path.normpath(str(destination / "LICENSE"))]
    assert not list(destination.rglob("*.tmp"))


def test_frozen_legal_materials_use_meipass_not_executable_directory(
    monkeypatch,
    tmp_path,
):
    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path)
    embedded_root = tmp_path / "embedded"
    app_root = tmp_path / "exe-directory"
    _write_legal_material_source(embedded_root, license_text="embedded license")
    _write_legal_material_source(app_root, license_text="adjacent untrusted license")
    monkeypatch.setattr(config, "APP_DIR", str(app_root))
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "_MEIPASS", str(embedded_root), raising=False)
    monkeypatch.setattr(config, "write_emergency_diagnostic", lambda *_: "")

    config.setup_directories()

    assert (
        runtime_dir / "Licenses" / "LICENSE"
    ).read_text(encoding="utf-8") == "embedded license"


def test_legal_material_paths_reject_traversal_and_source_links(
    monkeypatch,
    tmp_path,
):
    with pytest.raises(ValueError, match="Unsafe legal-material path"):
        config._normalize_legal_relative_path("../outside.txt")
    with pytest.raises(ValueError, match="must be relative"):
        config._normalize_legal_relative_path(str(tmp_path / "absolute.txt"))

    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    _write_legal_material_source(source_root)
    runtime_dir.mkdir(parents=True)
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("must not be copied", encoding="utf-8")
    linked_license = source_root / "third_party_licenses" / "Linked.txt"
    try:
        os.symlink(secret, linked_license)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")
    monkeypatch.setattr(config, "APP_DIR", str(source_root))
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)

    with pytest.raises(RuntimeError, match="non-linked"):
        config.materialize_legal_materials()

    assert not (
        runtime_dir / "Licenses" / "third_party_licenses" / "Linked.txt"
    ).exists()


def test_legal_material_safety_rejects_reported_source_and_destination_links(
    monkeypatch,
    tmp_path,
):
    source_root = tmp_path / "source"
    _write_legal_material_source(source_root)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    destination = runtime_root / "Licenses"
    destination.mkdir()

    real_link_check = config._path_is_link_or_junction
    reported_links = {
        os.path.normcase(os.path.abspath(source_root / "LICENSE")),
        os.path.normcase(os.path.abspath(destination)),
    }

    def report_selected_links(path):
        return (
            os.path.normcase(os.path.abspath(path)) in reported_links
            or real_link_check(path)
        )

    monkeypatch.setattr(
        config,
        "_path_is_link_or_junction",
        report_selected_links,
    )

    with pytest.raises(ValueError, match="source cannot be a link"):
        config._safe_legal_source_path(str(source_root), "LICENSE")
    with pytest.raises(ValueError, match="destination cannot be a link"):
        config._ensure_safe_legal_destination_directory(
            str(destination),
            trusted_root=str(runtime_root),
        )


def test_setup_refuses_linked_licenses_destination_and_continues(
    monkeypatch,
    tmp_path,
):
    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    outside = tmp_path / "outside"
    _write_legal_material_source(source_root)
    runtime_dir.mkdir(parents=True)
    outside.mkdir()
    try:
        os.symlink(
            outside,
            runtime_dir / "Licenses",
            target_is_directory=True,
        )
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")
    monkeypatch.setattr(config, "APP_DIR", str(source_root))
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    monkeypatch.setattr(config, "write_emergency_diagnostic", lambda *_: "")
    config.clear_runtime_warnings()

    config.setup_directories()

    assert (runtime_dir / "Config" / "settings.json").is_file()
    assert not (outside / "LICENSE").exists()
    assert any(
        "destination cannot be a link" in warning
        for warning in config.get_runtime_warnings()
    )


def test_setup_records_legal_copy_failure_without_aborting(monkeypatch, tmp_path):
    runtime_dir = _patch_standalone_paths(monkeypatch, tmp_path)
    warnings = []
    monkeypatch.setattr(
        config,
        "materialize_legal_materials",
        lambda: (_ for _ in ()).throw(PermissionError("read-only destination")),
    )
    monkeypatch.setattr(config, "record_runtime_warning", warnings.append)

    config.setup_directories()

    assert (runtime_dir / "Config" / "settings.json").is_file()
    assert any(
        "Failed to make bundled legal materials user-accessible" in warning
        and "read-only destination" in warning
        for warning in warnings
    )


def test_default_settings_have_only_the_two_built_in_categories():
    defaults = config.default_settings()

    assert defaults["settings_schema_version"] == 3
    assert defaults["additional_categories"] == []


def test_settings_migration_removes_only_the_retired_default_upscale(
    monkeypatch, tmp_path
):
    settings_file = tmp_path / "settings.json"
    custom = {
        "id": "review",
        "folder": "REVIEW",
        "media_type": "image",
        "type_suffix": "REV",
    }
    settings_file.write_text(
        json.dumps(
            {
                "additional_categories": [
                    dict(config.LEGACY_DEFAULT_UPSCALE_CATEGORY),
                    custom,
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

    assert config.migrate_settings_defaults() is True
    migrated = json.loads(settings_file.read_text(encoding="utf-8"))
    assert migrated["settings_schema_version"] == 3
    assert migrated["additional_categories"] == [custom]
    assert len(list(tmp_path.glob("settings.backup.*.json"))) == 1


def test_current_schema_can_add_upscale_as_an_explicit_custom_category():
    settings = config.default_settings()
    settings["additional_categories"] = [
        dict(config.LEGACY_DEFAULT_UPSCALE_CATEGORY)
    ]

    normalized = config._normalize_settings_snapshot(settings)

    assert normalized["additional_categories"] == [
        config.LEGACY_DEFAULT_UPSCALE_CATEGORY
    ]


def test_media_category_validation_skips_invalid_and_casefold_duplicates(monkeypatch):
    warnings = []
    monkeypatch.setattr(config, "record_runtime_warning", warnings.append)
    monkeypatch.setattr(config, "DIR_KEYFRAME", "KEYFRAMES")
    monkeypatch.setattr(config, "DIR_VIDEO", "VIDEO")

    categories = config.normalize_media_categories([
        {"id": "upscale", "folder": "UPSCALE", "media_type": "image", "type_suffix": "UPS"},
        {"id": "bad id!", "folder": "REVIEW", "media_type": "image", "type_suffix": "REV"},
        {"id": "nested", "folder": "foo/bar", "media_type": "image", "type_suffix": "NEST"},
        {"id": "duplicate", "folder": "upscale", "media_type": "video", "type_suffix": "DUP"},
        {"id": "wrong", "folder": "OTHER", "media_type": "audio", "type_suffix": "AUD"},
    ])

    assert [item.id for item in categories] == ["keyframes", "video", "upscale"]
    assert categories[-1].file_type == config.TYPE_IMG
    assert any("id must match" in warning for warning in warnings)
    assert any("duplicate folder" in warning for warning in warnings)


def test_load_settings_exposes_custom_categories(monkeypatch, tmp_path):
    original_scalars = {
        name: getattr(config, name)
        for name in ("DIR_KEYFRAME", "DIR_VIDEO", "IMAGE_TYPE_SUFFIX", "VIDEO_TYPE_SUFFIX")
    }
    original_categories = list(config.MEDIA_CATEGORIES)
    original_additional = list(config.ADDITIONAL_CATEGORIES)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({
            "settings_schema_version": 2,
            "keyframe_folder": "STILLS",
            "video_folder": "MOVIES",
            "image_type_suffix": "KEY",
            "video_type_suffix": "MOV",
            "additional_categories": [
                {"id": "upscale", "folder": "UPSCALE", "media_type": "image", "type_suffix": "UPS"}
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(config, "get_machine_ignored_folders", lambda: [])

    try:
        config.load_settings()

        categories = config.get_media_categories()
        assert [(item.id, item.folder, item.type_suffix) for item in categories] == [
            ("keyframes", "STILLS", "KEY"),
            ("video", "MOVIES", "MOV"),
            ("upscale", "UPSCALE", "UPS"),
        ]
        assert config.get_media_category("UPSCALE") == categories[-1]
    finally:
        for name, value in original_scalars.items():
            setattr(config, name, value)
        config.MEDIA_CATEGORIES[:] = original_categories
        config.ADDITIONAL_CATEGORIES[:] = original_additional


def test_json_file_lock_recovers_stale_lock(tmp_path):
    target = tmp_path / "state.json"
    lock_path = Path(f"{target}.lock")
    lock_path.write_text("abandoned", encoding="utf-8")
    old = datetime.datetime.now().timestamp() - 3600
    os.utime(lock_path, (old, old))

    with config.json_file_lock(str(target), timeout=0.2, stale_seconds=1):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_exclusive_file_lock_recovers_recent_abandoned_empty_lock(tmp_path):
    lock_path = tmp_path / "abandoned.lock"
    lock_path.write_bytes(b"")
    old = time.time() - 10
    os.utime(lock_path, (old, old))

    with config.exclusive_file_lock(
        str(lock_path), timeout=0.3, poll_interval=0.01, stale_seconds=300
    ):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_exclusive_file_lock_renews_live_lease(tmp_path):
    lock_path = tmp_path / "live.lock"

    with config.exclusive_file_lock(
        str(lock_path), timeout=0.2, poll_interval=0.01, stale_seconds=0.15
    ):
        first_mtime = lock_path.stat().st_mtime
        time.sleep(0.25)
        assert lock_path.stat().st_mtime > first_mtime
        with pytest.raises(TimeoutError):
            with config.exclusive_file_lock(
                str(lock_path), timeout=0.1, poll_interval=0.01, stale_seconds=0.15
            ):
                pass

    assert not lock_path.exists()


def test_exclusive_file_lock_retries_transient_cleanup_sharing_conflict(monkeypatch, tmp_path):
    lock_path = tmp_path / "sharing.lock"
    real_remove = os.remove
    failed_once = False

    def sharing_conflict(path, *args, **kwargs):
        nonlocal failed_once
        if os.fspath(path) == str(lock_path) and not failed_once:
            failed_once = True
            raise PermissionError("File is temporarily open by another reader")
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", sharing_conflict)
    with config.exclusive_file_lock(str(lock_path), timeout=0.2):
        assert lock_path.exists()

    assert failed_once
    assert not lock_path.exists()
    with config.exclusive_file_lock(str(lock_path), timeout=0.2):
        pass


def test_stale_lock_recovery_never_deletes_a_live_successor(tmp_path):
    lock_path = tmp_path / "stale-race.lock"
    lock_path.write_text("abandoned", encoding="utf-8")
    old = datetime.datetime.now().timestamp() - 3600
    os.utime(lock_path, (old, old))
    start = threading.Barrier(3)
    state_guard = threading.Lock()
    active = 0
    maximum_active = 0
    errors = []

    def contender():
        nonlocal active, maximum_active
        try:
            start.wait(timeout=2)
            with config.exclusive_file_lock(
                str(lock_path),
                timeout=2,
                poll_interval=0.005,
                stale_seconds=0,
            ):
                with state_guard:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.05)
                with state_guard:
                    active -= 1
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=contender) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=4)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1
    assert not lock_path.exists()


def test_restore_ignored_folder_is_atomic_and_does_not_require_existing_path(monkeypatch, tmp_path):
    projects_dir = tmp_path / "projects"
    machine_file = projects_dir / "WORKSTATION.json"
    machine_file.parent.mkdir(parents=True)
    missing_path = os.path.normpath(str(tmp_path / "already_deleted"))
    kept_path = os.path.normpath(str(tmp_path / "keep"))
    machine_file.write_text(
        json.dumps({"projects": {}, "ignored_folders": [missing_path, kept_path]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PROJECTS_DIR", str(projects_dir))

    assert config.restore_ignored_folder(missing_path, "WORKSTATION") is True
    assert json.loads(machine_file.read_text(encoding="utf-8"))["ignored_folders"] == [kept_path]
    assert config.restore_ignored_folder(missing_path, "WORKSTATION") is False


def test_detect_project_layout_distinguishes_direct_shots_and_sequences(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "SCENE_PREFIX", "")
    monkeypatch.setattr(config, "SHOT_PREFIX", "")
    monkeypatch.setattr(config, "TARGET_PREFIX", "")
    monkeypatch.setattr(config, "IGNORED_FOLDERS", [])
    monkeypatch.setattr(config, "IGNORED_PATHS", set())
    monkeypatch.setattr(config, "MEDIA_CATEGORIES", [])
    monkeypatch.setattr(config, "ADDITIONAL_CATEGORIES", [])
    config._set_media_categories([])

    direct = tmp_path / "Direct"
    (direct / "SH010" / config.DIR_KEYFRAME).mkdir(parents=True)
    sequenced = tmp_path / "Sequenced"
    (sequenced / "SQ010" / "SH010" / config.DIR_KEYFRAME).mkdir(parents=True)

    assert config.detect_project_layout(str(direct)) == config.PROJECT_LAYOUT_SHOTS
    assert (
        config.detect_project_layout(str(sequenced))
        == config.PROJECT_LAYOUT_SEQUENCES
    )


def test_detect_project_layout_treats_work_folders_beneath_shot_as_direct(tmp_path):
    project = tmp_path / "Sponsor"
    for name in ("animation", "compo", "render", "_shotcode"):
        (project / "pge0050" / name).mkdir(parents=True)
    assert config.detect_project_layout(
        str(project), config.PROJECT_LAYOUT_SEQUENCES
    ) == config.PROJECT_LAYOUT_SHOTS
    assert config.is_ignored(str(project / "pge0050" / "_shotcode"), "_shotcode")


def test_detect_project_layout_finds_numeric_sequence_by_genai_and_skips_ignored(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "SCENE_PREFIX", "")
    monkeypatch.setattr(config, "SHOT_PREFIX", "")
    monkeypatch.setattr(config, "IGNORED_NAMES", {"Reformat"})
    monkeypatch.setattr(config, "IGNORED_PATHS", set())
    project = tmp_path / "NumericSequences"
    (project / "30" / "0010" / "genai").mkdir(parents=True)
    (project / "30" / "0020" / "genai").mkdir(parents=True)
    (project / "Reformat" / "30" / "genai").mkdir(parents=True)

    assert config.detect_project_layout(
        str(project), config.PROJECT_LAYOUT_SHOTS
    ) == config.PROJECT_LAYOUT_SEQUENCES
    assert "Reformat" not in [
        os.path.basename(path) for path in config._layout_child_directories(str(project))
    ]


def test_detect_project_layout_bounds_directory_scans(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCENE_PREFIX", "")
    monkeypatch.setattr(config, "SHOT_PREFIX", "")
    project = tmp_path / "LargeNetworkStyleProject"
    for index in range(80):
        (project / f"SQ{index:03d}" / "SH010" / "genai").mkdir(parents=True)
    original = config._layout_child_directories
    limits = []

    def checked_children(path, limit=None):
        limits.append(limit)
        return original(path, limit=limit)

    monkeypatch.setattr(config, "_layout_child_directories", checked_children)
    assert config.detect_project_layout(str(project)) == config.PROJECT_LAYOUT_SEQUENCES
    assert limits and all(limit is not None and limit <= 24 for limit in limits)
    assert len(limits) <= 4


def test_detect_project_layout_ignores_genai_in_excluded_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCENE_PREFIX", "")
    monkeypatch.setattr(config, "SHOT_PREFIX", "")
    monkeypatch.setattr(config, "IGNORED_NAMES", {"Reformat"})
    monkeypatch.setattr(config, "IGNORED_PATHS", set())
    project = tmp_path / "DirectShots"
    (project / "0010" / "genai").mkdir(parents=True)
    (project / "Reformat" / "30" / "genai").mkdir(parents=True)
    assert config.detect_project_layout(
        str(project), config.PROJECT_LAYOUT_SEQUENCES
    ) == config.PROJECT_LAYOUT_SHOTS




def test_detect_project_layout_uses_pending_settings_paths(tmp_path):
    project = tmp_path / "PendingSettings"
    (project / "ROOT" / "SQ020" / "SH020" / "PUBLISH" / "STILLS").mkdir(
        parents=True
    )
    settings = config.default_settings()
    settings.update(
        {
            "scene_prefix": "ROOT",
            "shot_prefix": "",
            "target_prefix": "PUBLISH",
            "keyframe_folder": "STILLS",
            "video_folder": "MOVIES",
        }
    )

    assert (
        config.detect_project_layout(str(project), settings=settings)
        == config.PROJECT_LAYOUT_SEQUENCES
    )

def test_detect_project_layout_retains_fallback_for_empty_or_unavailable_project(
    tmp_path,
):
    empty = tmp_path / "Empty"
    empty.mkdir()
    empty_sequence_project = tmp_path / "EmptySequence"
    (empty_sequence_project / "SQ010").mkdir(parents=True)

    assert (
        config.detect_project_layout(str(empty), config.PROJECT_LAYOUT_SHOTS)
        == config.PROJECT_LAYOUT_SHOTS
    )
    assert (
        config.detect_project_layout(str(empty_sequence_project))
        == config.PROJECT_LAYOUT_SEQUENCES
    )
    assert (
        config.detect_project_layout(
            str(tmp_path / "Offline"), config.PROJECT_LAYOUT_SEQUENCES
        )
        == config.PROJECT_LAYOUT_SEQUENCES
    )


def test_save_projects_persists_and_prunes_project_modes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJECTS_DIR", str(tmp_path / "projects"))

    config.save_projects(
        {"Direct": r"P:\Direct"},
        "WORKSTATION",
        project_modes={
            "Direct": config.PROJECT_LAYOUT_SHOTS,
            "Removed": config.PROJECT_LAYOUT_SEQUENCES,
        },
    )

    state = config.get_machine_state("WORKSTATION")
    assert state["projects"] == {"Direct": os.path.normpath(r"P:\Direct")}
    assert state["project_modes"] == {"Direct": config.PROJECT_LAYOUT_SHOTS}
