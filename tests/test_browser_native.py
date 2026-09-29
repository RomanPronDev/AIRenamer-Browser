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
        self.shots = Path(config.get_project_shot_root(str(self.project)))
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
        self.host = browser_native.BrowserHost()
        self.addCleanup(self.host.close)

    def target(self):
        return {"project": "DEMO", "sequence": None, "shot": "SH010"}

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
        folder = Path(config.get_target_base(str(self.shot))) / video_category.folder
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
