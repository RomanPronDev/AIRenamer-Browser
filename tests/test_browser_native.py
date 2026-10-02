import io
import json
import os
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import browser_native
import config


class BrowserNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "DEMO"
        self.shots = self.project / "vfx" / "shots"
        self.shot = self.shots / "SH010"
        self.shot.mkdir(parents=True)
        self.source = root / "download.png"
        self.source.write_bytes(b"test-image")
        self.patches = [
            patch.object(config, "get_projects", return_value={"DEMO": str(self.project)}),
            patch.object(config, "get_project_modes", return_value={"DEMO": "shots"}),
            patch.object(config, "STATE_DIR", str(root / "state")),
            patch.object(config, "log_action", return_value=None),
            patch.object(browser_native.utils, "log_action", return_value=None),
            patch.object(browser_native.config, "get_local_base_dir", return_value=str(root)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = browser_native.BrowserHost(initialize_settings=False)
        self.addCleanup(self.host.close)

    def target(self):
        return {"project": "DEMO", "sequence": None, "shot": "SH010"}

    def test_browser_media_defaults_ignore_desktop_prefixes_and_folders(self):
        with patch.object(config, "TARGET_PREFIX", "desktop-media"), \
             patch.object(config, "DIR_KEYFRAME", "desktop-images"), \
             patch.object(config, "DIR_VIDEO", "desktop-videos"):
            image = self.host.import_file({**self.target(), "source": str(self.source)})
            self.assertEqual(Path(image["path"]).parent, self.shot / "genai" / "KEYFRAMES")
            self.assertTrue((self.shot / "genai" / "VIDEO").is_dir())
            source_video = Path(self.temp.name) / "clip.mp4"
            source_video.write_bytes(b"video")
            video = self.host.import_file({**self.target(), "source": str(source_video)})
            self.assertEqual(Path(video["path"]).parent, self.shot / "genai" / "VIDEO")
            self.assertEqual(len(self.host.files(self.target())["files"]), 2)
            self.assertFalse((self.shot / "desktop-media").exists())

    def test_custom_media_defaults_and_project_overrides_control_real_imports_after_reopen(self):
        defaults = {"scenePrefix": "vfx/shots", "shotPrefix": "", "targetPrefix": "ai-work",
                    "imageFolder": "STILLS", "videoFolder": "CLIPS", "defaultLayout": "shots"}
        self.host.set_default_structure(defaults)
        image = self.host.import_file({**self.target(), "source": str(self.source)})
        self.assertEqual(Path(image["path"]).parent, self.shot / "ai-work" / "STILLS")
        self.assertTrue((self.shot / "ai-work" / "CLIPS").is_dir())
        override = {**defaults, "project": "DEMO", "targetPrefix": "custom-media",
                    "imageFolder": "CUSTOM-IMAGES", "videoFolder": "CUSTOM-VIDEO", "layout": "shots"}
        self.host.set_project_structure(override)
        second = browser_native.BrowserHost(initialize_settings=False)
        try:
            values = second.get_structure({"project": "DEMO"})
            self.assertEqual(values["values"]["imageFolder"], "CUSTOM-IMAGES")
            image = second.import_file({**self.target(), "source": str(self.source)})
            self.assertEqual(Path(image["path"]).parent, self.shot / "custom-media" / "CUSTOM-IMAGES")
            video_source = Path(self.temp.name) / "custom.mp4"
            video_source.write_bytes(b"video")
            video = second.import_file({**self.target(), "source": str(video_source)})
            self.assertEqual(Path(video["path"]).parent, self.shot / "custom-media" / "CUSTOM-VIDEO")
            self.assertFalse((self.shot / "genai").exists())
            second.reset_project_structure({"project": "DEMO"})
            self.assertEqual(second.get_structure({"project": "DEMO"})["values"]["imageFolder"], "STILLS")
        finally:
            second.close()

    def test_projects_navigation_files_and_real_import(self):
        projects = self.host.dispatch({"type": "projects"})["projects"]
        self.assertEqual(projects[0]["name"], "DEMO")
        self.assertEqual(self.host.navigation({"project": "DEMO"})["shots"], ["SH010"])
        result = self.host.import_file({**self.target(), "source": str(self.source)})
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(result["category"], "keyframes")
        files = self.host.files(self.target())["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], result["name"])
        self.assertEqual(files[0]["version"], 1)
        self.assertFalse(files[0]["hasPsd"])
        self.assertEqual(self.source.read_bytes(), b"test-image")

    def test_project_path_changes_preserve_other_shortcuts(self):
        root = Path(self.temp.name)
        another = root / "ANOTHER"
        another.mkdir()
        relocated = root / "RELOCATED"
        relocated.mkdir()
        saved = {}
        modes = {"DEMO": "shots", "ANOTHER": "sequences"}
        saved.update({"DEMO": str(self.project), "ANOTHER": str(another)})

        def persist(projects, *, project_modes):
            saved.clear()
            saved.update(projects)
            modes.clear()
            modes.update(project_modes)

        with patch.object(config, "get_projects", side_effect=lambda: dict(saved)), \
             patch.object(config, "get_project_modes", side_effect=lambda: dict(modes)), \
             patch.object(config, "save_projects", side_effect=persist), \
             patch.object(config, "detect_project_layout", return_value="shots"):
            result = self.host.change_project_path({"project": "DEMO", "path": str(relocated)})
            self.assertEqual(result["name"], "RELOCATED")
            self.assertEqual(saved, {"ANOTHER": str(another), "RELOCATED": str(relocated)})
            self.assertEqual(modes["ANOTHER"], "sequences")
            self.host.remove_project({"project": "RELOCATED"})
            self.assertEqual(saved, {"ANOTHER": str(another)})
            self.assertTrue(relocated.is_dir())

    def test_existing_project_list_is_read_without_rewriting_paths(self):
        original = str(self.project)
        with patch.object(config, "save_projects", side_effect=AssertionError("unexpected write")):
            self.assertEqual(self.host.projects()["projects"][0]["path"], original)

    def test_detected_layout_is_cached_after_first_navigation(self):
        self.host.navigation({"project": "DEMO"})
        with patch.object(config, "detect_project_layout",
                          side_effect=AssertionError("unexpected project scan")):
            self.assertEqual(self.host.projects()["projects"][0]["layout"], "shots")
            self.assertEqual(self.host.navigation({"project": "DEMO"})["shots"], ["SH010"])

    def test_standard_shots_tree_detects_optional_sequences(self):
        root = Path(self.temp.name)
        sequenced = root / "SEQUENCED"
        (sequenced / "vfx" / "shots" / "sep0010" / "sep0070" / "genai").mkdir(parents=True)
        (sequenced / "vfx" / "shots" / "sep0020" / "sep0080" / "genai").mkdir(parents=True)
        (sequenced / "vfx" / "assets").mkdir()
        direct = root / "DIRECT"
        (direct / "vfx" / "shots" / "neo0010" / "genai").mkdir(parents=True)
        with patch.object(config, "get_projects", return_value={
            "SEQUENCED": str(sequenced), "DIRECT": str(direct)}), \
             patch.object(config, "get_project_modes", return_value={
                 "SEQUENCED": "shots", "DIRECT": "sequences"}):
            first = self.host.navigation({"project": "SEQUENCED"})
            self.assertEqual(first["layout"], "sequences")
            self.assertEqual(first["sequences"], ["sep0010", "sep0020"])
            self.assertEqual(self.host.navigation({"project": "SEQUENCED", "sequence": "sep0010"})["shots"],
                             ["sep0070"])
            self.assertEqual(self.host.navigation({"project": "SEQUENCED", "sequence": "sep0020"})["shots"],
                             ["sep0080"])
            imported = self.host.import_file({"project": "SEQUENCED", "sequence": "sep0010",
                                              "shot": "sep0070", "source": str(self.source)})
            self.assertTrue(imported["path"].startswith(str(
                sequenced / "vfx" / "shots" / "sep0010" / "sep0070" / "genai")))
            self.assertEqual(len(self.host.files({"project": "SEQUENCED", "sequence": "sep0010",
                                                  "shot": "sep0070"})["files"]), 1)
            second = self.host.navigation({"project": "DIRECT"})
            self.assertEqual(second["layout"], "shots")
            self.assertEqual(second["shots"], ["neo0010"])

    def test_project_structure_override_persists_and_changes_media_destination(self):
        custom = self.project / "custom" / "SQ010" / "cuts" / "SH020"
        (custom / "genai").mkdir(parents=True)
        values = {"project": "DEMO", "scenePrefix": "custom", "shotPrefix": "cuts",
                  "targetPrefix": "media", "imageFolder": "genai",
                  "videoFolder": "VIDEO", "layout": "sequences"}
        self.host.set_project_structure(values)
        preview = self.host.get_structure({"project": "DEMO"})
        self.assertTrue(preview["custom"])
        self.assertEqual(preview["preview"]["sequences"], ["SQ010"])
        self.assertEqual(preview["preview"]["examples"][0]["shots"], ["SH020"])
        self.assertEqual(self.host.navigation({"project": "DEMO", "sequence": "SQ010"})["shots"],
                         ["SH020"])
        result = self.host.import_file({"project": "DEMO", "sequence": "SQ010",
                                        "shot": "SH020", "source": str(self.source)})
        self.assertTrue(result["path"].startswith(str(custom / "media" / "genai")))
        self.assertTrue(Path(result["path"]).is_file())
        another_host = browser_native.BrowserHost(initialize_settings=False)
        try:
            self.assertEqual(another_host.navigation({"project": "DEMO", "sequence": "SQ010"})["shots"],
                             ["SH020"])
        finally:
            another_host.close()
        self.host.reset_project_structure({"project": "DEMO"})
        self.assertFalse(self.host.get_structure({"project": "DEMO"})["custom"])
        self.assertEqual(self.host.navigation({"project": "DEMO"})["shots"], ["SH010"])

    def test_default_structure_is_editable_and_rejects_unsafe_paths(self):
        self.assertEqual(self.host.get_structure({})["defaults"]["scenePrefix"],
                         os.path.join("vfx", "shots"))
        self.host.set_default_structure({"scenePrefix": "production\\shots",
                                         "shotPrefix": "", "targetPrefix": "",
                                         "defaultLayout": "shots"})
        self.assertEqual(self.host.get_structure({})["defaults"]["scenePrefix"],
                         os.path.join("production", "shots"))
        for invalid in ("..\\other", "X:\\other"):
            with self.assertRaises(browser_native.HostError):
                self.host.set_project_structure({"project": "DEMO",
                    "scenePrefix": invalid, "shotPrefix": "", "targetPrefix": "",
                    "layout": "shots"})

    def test_first_project_folder_is_saved_only_after_selection(self):
        with patch.object(config, "get_projects", return_value={}), \
             patch.object(config, "get_project_modes", return_value={}), \
             patch.object(config, "save_projects") as save, \
             patch.object(browser_native, "_choose_project_folder", return_value=""):
            self.assertEqual(self.host.add_project({}), {"cancelled": True})
            save.assert_not_called()
        with patch.object(config, "get_projects", return_value={}), \
             patch.object(config, "get_project_modes", return_value={}), \
             patch.object(config, "save_projects") as save, \
             patch.object(config, "detect_project_layout", return_value="shots"), \
             patch.object(browser_native, "_choose_project_folder", return_value=str(self.project)):
            result = self.host.add_project({})
            self.assertEqual(result["name"], "DEMO")
            save.assert_called_once_with({"DEMO": str(self.project)},
                                         project_modes={"DEMO": "shots"})

    def test_folder_picker_returns_a_path_without_saving_it(self):
        with patch.object(browser_native, "_choose_project_folder",
                          return_value=str(self.project)) as picker, \
             patch.object(config, "save_projects") as save:
            result = self.host.dispatch({"type": "choose_project_folder",
                                         "payload": {"initial": str(self.project)}})
            self.assertEqual(result, {"path": str(self.project)})
            picker.assert_called_once_with(str(self.project))
            save.assert_not_called()
        with patch.object(browser_native, "_choose_project_folder", return_value=""):
            self.assertEqual(self.host.choose_project_folder({}), {"path": ""})
        with self.assertRaises(browser_native.HostError):
            self.host.choose_project_folder({"initial": 42})

    def test_drag_url_streams_validated_project_file_from_loopback(self):
        imported = self.host.import_file({**self.target(), "source": str(self.source)})
        record = self.host.files(self.target())["files"][0]
        self.assertTrue(record["dragUrl"].startswith("http://127.0.0.1:"))
        with urllib.request.urlopen(record["dragUrl"], timeout=5) as response:
            self.assertEqual(response.read(), b"test-image")
            self.assertIn(imported["name"], response.headers["Content-Disposition"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(record["dragUrl"] + "unknown", timeout=5)
        self.assertEqual(error.exception.code, 404)

    def test_desktop_drag_uses_the_validated_local_file(self):
        imported = self.host.import_file({**self.target(), "source": str(self.source)})
        with patch.object(browser_native, "_launch_native_drag") as launch:
            result = self.host.dispatch({"type": "drag_to_app", "payload": {
                **self.target(), "category": imported["category"], "name": imported["name"]
            }})
            self.assertEqual(result, {"opened": True})
            launch.assert_called_once_with(imported["path"])
            with self.assertRaises(browser_native.HostError):
                self.host.drag_to_app({**self.target(), "category": imported["category"],
                                       "name": "missing.png"})

    def test_video_preview_streams_inline_and_supports_byte_ranges(self):
        source = Path(self.temp.name) / "clip.mp4"
        source.write_bytes(bytes(range(256)) * 4)
        imported = self.host.import_file({**self.target(), "source": str(source)})
        preview = self.host.preview({**self.target(), "category": imported["category"],
                                     "name": imported["name"]})
        self.assertTrue(preview["available"])
        self.assertEqual(preview["mediaType"], "video")
        self.assertEqual(preview["mime"], "video/mp4")
        self.assertTrue(preview["url"].startswith("http://127.0.0.1:"))
        request = urllib.request.Request(preview["url"], headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 10-19/1024")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
            self.assertEqual(response.read(), source.read_bytes()[10:20])
        request = urllib.request.Request(preview["url"], headers={"Range": "bytes=-5"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.read(), source.read_bytes()[-5:])
        request = urllib.request.Request(preview["url"], headers={"Range": "bytes=9999-"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 416)
        self.assertEqual(error.exception.headers["Content-Range"], "bytes */1024")

    def test_preview_completed_downloads_without_importing(self):
        from PIL import Image
        picture = Path(self.temp.name) / "download-preview.png"
        Image.new("RGB", (20, 12), "red").save(picture)
        result = self.host.dispatch({"type": "preview_download", "payload": {"path": str(picture)}})
        self.assertTrue(result["available"])
        self.assertEqual(result["mime"], "image/jpeg")
        self.assertTrue(result["data"])
        movie = Path(self.temp.name) / "download-preview.mp4"
        movie.write_bytes(bytes(range(128)) * 2)
        video = self.host.dispatch({"type": "preview_download", "payload": {"path": str(movie)}})
        self.assertEqual(video["mediaType"], "video")
        request = urllib.request.Request(video["url"], headers={"Range": "bytes=5-9"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), movie.read_bytes()[5:10])
        with self.assertRaises(browser_native.HostError):
            self.host.preview_download({"path": "relative.png"})
        with self.assertRaises(browser_native.HostError):
            self.host.preview_download({"path": str(movie.with_name("missing.mp4"))})

    def test_move_video_keeps_converted_frame_sequence(self):
        video = Path(self.temp.name) / "source.mov"
        video.write_bytes(b"video")
        imported = self.host.import_file({**self.target(), "source": str(video)})
        original = Path(imported["path"])
        frames = original.with_suffix("")
        frames.mkdir()
        (frames / f"{original.stem}_00001.png").write_bytes(b"frame")
        (self.shots / "SH020").mkdir()
        moved = self.host.move_file({
            "sourceProject": "DEMO", "sourceSequence": None, "sourceShot": "SH010",
            "category": imported["category"], "name": imported["name"],
            "project": "DEMO", "sequence": None, "shot": "SH020",
        })
        target = Path(moved["path"])
        self.assertFalse(original.exists())
        self.assertFalse(frames.exists())
        self.assertEqual((target.with_suffix("") / f"{target.stem}_00001.png").read_bytes(), b"frame")
        listing = self.host.files({"project": "DEMO", "sequence": None, "shot": "SH020"})["files"]
        self.assertTrue(listing[0]["converted"])

    def test_convert_video_uses_existing_local_ffmpeg_without_resolver(self):
        video = Path(self.temp.name) / "source.mov"
        video.write_bytes(b"video")
        imported = self.host.import_file({**self.target(), "source": str(video)})
        payload = {**self.target(), "category": imported["category"], "name": imported["name"]}
        local_ffmpeg = str(Path(self.temp.name) / "Tools" / "ffmpeg.exe")

        def make_sequence(source, target, base, **kwargs):
            self.assertEqual(kwargs["ffmpeg_path"], local_ffmpeg)
            folder = Path(target) / base
            folder.mkdir()
            (folder / f"{base}_00001.png").write_bytes(b"frame")
            return str(folder), base, ("folder_create", str(folder))

        with patch.object(config, "FFMPEG_PATH", local_ffmpeg), \
             patch.object(browser_native.utils, "resolve_ffmpeg", return_value=local_ffmpeg) as resolve, \
             patch.object(browser_native.utils, "convert_to_sequence", side_effect=make_sequence):
            job_id = self.host.convert_begin(payload)["jobId"]
            self.host.conversions[job_id]["thread"].join(timeout=5)
            result = self.host.convert_status({"jobId": job_id})
        self.assertEqual(result["state"], "complete")
        resolve.assert_called_once_with(target_path=local_ffmpeg, allow_frozen_download=True, timeout=600)
        self.assertTrue(Path(result["folder"]).is_dir())
        self.assertTrue(self.host.files(self.target())["files"][0]["converted"])

    def test_convert_without_local_ffmpeg_reports_download_failure(self):
        video = Path(self.temp.name) / "source.mov"
        video.write_bytes(b"video")
        imported = self.host.import_file({**self.target(), "source": str(video)})
        payload = {**self.target(), "category": imported["category"], "name": imported["name"]}
        with patch.object(config, "FFMPEG_PATH", str(Path(self.temp.name) / "missing.exe")), \
             patch.object(browser_native.utils, "resolve_ffmpeg", side_effect=RuntimeError("Network unavailable")):
            job_id = self.host.convert_begin(payload)["jobId"]
            self.host.conversions[job_id]["thread"].join(timeout=5)
            result = self.host.convert_status({"jobId": job_id})
        self.assertEqual(result["state"], "error")
        self.assertIn("Network unavailable", result["error"])

    def test_convert_can_be_cancelled(self):
        video = Path(self.temp.name) / "source.mov"
        video.write_bytes(b"video")
        imported = self.host.import_file({**self.target(), "source": str(video)})
        payload = {**self.target(), "category": imported["category"], "name": imported["name"]}
        started, release = threading.Event(), threading.Event()

        def wait_for_cancel(_source, _target, _base, **kwargs):
            started.set()
            release.wait(timeout=5)
            self.assertTrue(kwargs["cancel_check"]())
            raise RuntimeError("Sequence conversion cancelled.")

        with patch.object(config, "FFMPEG_PATH", "local-ffmpeg.exe"), \
             patch.object(browser_native.utils, "validate_ffmpeg_executable", return_value=True), \
             patch.object(browser_native.utils, "convert_to_sequence", side_effect=wait_for_cancel):
            job_id = self.host.convert_begin(payload)["jobId"]
            self.assertTrue(started.wait(timeout=5))
            self.assertEqual(self.host.convert_cancel({"jobId": job_id}), {"cancelled": True})
            release.set()
            self.host.conversions[job_id]["thread"].join(timeout=5)
            result = self.host.convert_status({"jobId": job_id})
        self.assertEqual(result["state"], "cancelled")

    def test_show_converted_folder_selects_validated_sequence_in_explorer(self):
        video = Path(self.temp.name) / "source.mov"
        video.write_bytes(b"video")
        imported = self.host.import_file({**self.target(), "source": str(video)})
        payload = {**self.target(), "category": imported["category"],
                   "file": imported["name"], "convertedFolder": True}
        with self.assertRaises(browser_native.HostError):
            self.host.open_folder(payload)
        frames = Path(imported["path"]).with_suffix("")
        frames.mkdir()
        (frames / f"{frames.name}_00001.png").write_bytes(b"frame")
        with patch.object(browser_native.subprocess, "Popen") as launched:
            self.assertEqual(self.host.open_folder(payload), {"opened": True})
        self.assertEqual(launched.call_args.args[0], ["explorer.exe", "/select,", str(frames)])
        image = self.host.import_file({**self.target(), "source": str(self.source)})
        with self.assertRaises(browser_native.HostError):
            self.host.open_folder({**self.target(), "category": image["category"],
                                   "file": image["name"], "convertedFolder": True})

    def test_show_file_selects_validated_file_in_explorer(self):
        imported = self.host.import_file({**self.target(), "source": str(self.source)})
        payload = {**self.target(), "category": imported["category"], "file": imported["name"]}
        with patch.object(browser_native.subprocess, "Popen") as launched:
            self.assertEqual(self.host.open_folder(payload), {"opened": True})
        self.assertEqual(launched.call_args.args[0],
                         ["explorer.exe", "/select,", imported["path"]])
        with self.assertRaises(browser_native.HostError):
            self.host.open_folder({**payload, "file": "not-in-shot.png"})

    def test_fix_video_name_keeps_converted_frame_sequence(self):
        video_category = next(cat for cat in config.get_media_categories() if cat.media_type == config.MEDIA_TYPE_VIDEO)
        folder = Path(browser_native._target_root("DEMO", str(self.shot))) / video_category.folder
        folder.mkdir(parents=True)
        original = folder / "draft.mov"
        original.write_bytes(b"video")
        frames = folder / "draft_sequence"
        frames.mkdir()
        (frames / "draft_00001.png").write_bytes(b"frame")
        answer = self.host.rename_file({**self.target(), "category": video_category.id, "name": original.name})
        self.assertTrue(any(item.startswith("Renamed:") for item in answer["messages"]))
        listing = self.host.files(self.target())["files"]
        self.assertEqual(len(listing), 1)
        renamed = Path(listing[0]["path"])
        self.assertTrue(listing[0]["converted"])
        self.assertFalse(frames.exists())
        self.assertEqual((renamed.with_suffix("") / f"{renamed.stem}_00001.png").read_bytes(), b"frame")

    def test_simultaneous_import_reserves_distinct_versions(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            names = list(pool.map(
                lambda _: self.host.import_file({**self.target(), "source": str(self.source)})["name"],
                range(2),
            ))
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(len(self.host.files(self.target())["files"]), 2)

    def test_transfer_chunks_stream_to_target_and_cleanup_stage(self):
        start = self.host.transfer_begin({**self.target(), "name": "from-site.png"})
        transfer_id = start["transferId"]
        import base64
        self.host.transfer_chunk({
            "transferId": transfer_id,
            "data": base64.b64encode(b"from browser").decode("ascii"),
        })
        result = self.host.transfer_finish({"transferId": transfer_id})
        self.assertEqual(Path(result["path"]).read_bytes(), b"from browser")
        self.assertFalse(list(Path(self.temp.name).glob("airenamer-*")))

    def test_reject_path_traversal_and_wrong_category(self):
        with self.assertRaises(browser_native.HostError):
            self.host.files({**self.target(), "shot": ".."})
        with self.assertRaises(browser_native.HostError):
            self.host.import_file({**self.target(), "source": str(self.source), "category": "video"})
        with self.assertRaises(browser_native.HostError):
            self.host.dispatch({"type": "unsupported"})

    def test_protocol_frame(self):
        request = json.dumps({"id": 7, "type": "projects"}).encode()
        reader = io.BytesIO(struct.pack("<I", len(request)) + request)
        writer = io.BytesIO()
        browser_native.run(reader, writer)
        writer.seek(0)
        size = struct.unpack("<I", writer.read(4))[0]
        response = json.loads(writer.read(size))
        self.assertEqual(response["id"], 7)
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["projects"][0]["name"], "DEMO")


if __name__ == "__main__":
    unittest.main()
