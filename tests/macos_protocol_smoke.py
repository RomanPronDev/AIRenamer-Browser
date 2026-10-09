# SPDX-License-Identifier: GPL-3.0-only
"""Run the real frozen macOS host, install and tools without touching the user's HOME."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import browser_update


class NativeHost:
    def __init__(self, executable, environment, log):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=log, env=environment)
        self.answers = queue.Queue()
        self.count = 0
        self.log = log
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            stream = self.process.stdout
            while header := stream.read(4):
                size = struct.unpack("<I", header)[0]
                if size > 1024 * 1024:
                    raise RuntimeError("Native stdout was polluted with non-protocol output")
                data = bytearray()
                while len(data) < size:
                    chunk = stream.read(size - len(data))
                    if not chunk:
                        raise EOFError("Truncated native response")
                    data.extend(chunk)
                self.answers.put(json.loads(data))
        except Exception as exc:
            self.answers.put(exc)

    def call(self, kind, payload=None, *, timeout=30):
        self.count += 1
        data = json.dumps({"id": self.count, "type": kind, "payload": payload or {}}).encode()
        self.process.stdin.write(struct.pack("<I", len(data)) + data)
        self.process.stdin.flush()
        try:
            answer = self.answers.get(timeout=timeout)
        except queue.Empty as exc:
            diagnostic = Path(self.log.name).read_text(errors="replace")[-4000:]
            raise RuntimeError(f"Native host did not answer {kind}; exit={self.process.poll()}.\n{diagnostic}") from exc
        if isinstance(answer, Exception):
            raise answer
        assert answer["id"] == self.count and answer["ok"], answer
        return answer["result"]

    def close(self):
        self.process.stdin.close()
        try:
            assert self.process.wait(timeout=20) == 0
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
            raise
        finally:
            self.reader.join(timeout=2)
            self.process.stdout.close()


def main():
    if sys.platform != "darwin":
        raise RuntimeError("This smoke test must run on macOS arm64.")
    version = json.loads((ROOT / "version.json").read_text())["version"]
    package = ROOT / "dist" / f"AIRenamer-Browser-{version}-macos-arm64.zip"
    with tempfile.TemporaryDirectory(prefix="airenamer-macos-smoke-") as temporary:
        root = Path(temporary)
        user_home = root / "home with spaces"
        user_home.mkdir()
        environment = dict(os.environ, HOME=str(user_home))
        environment.pop("LOCALAPPDATA", None)
        desktop = user_home / "Library/Application Support/MediaRenamer"
        sentinel = desktop / "Config/settings.json"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text('{"desktop":"unchanged"}')
        extracted = root / "release"
        browser_update.extract_release(package, extracted, version)
        executable = extracted / "dist/MediaRenamerBrowserNative/MediaRenamerBrowserNative"
        installed = subprocess.run([str(executable), "--install-browser", "--package-folder", str(extracted),
                                    "--extension-id", "a" * 32, "--no-open", "--no-ffmpeg"],
                                   env=environment, capture_output=True, text=True, timeout=60)
        assert installed.returncode == 0, installed.stdout + installed.stderr
        base = desktop / "Browser"
        installation = json.loads((base / "installation.json").read_text())
        native_manifest = user_home / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.airenamer.browser.json"
        assert json.loads(native_manifest.read_text())["path"] == installation["hostPath"]
        executable = Path(installation["hostPath"])
        with (root / "native-stderr.log").open("wb") as log:
            host = NativeHost(executable, environment, log)
            try:
                assert host.call("ping")["platform"] == "macos"
                assert host.call("projects")["projects"] == []
                project = root / "Проєкт with spaces"
                shot = project / "vfx/shots/SH010"
                (shot / "genai").mkdir(parents=True)
                registered = host.call("add_project", {"path": str(project)})
                name = registered["name"]
                navigation = host.call("navigation", {"project": name})
                assert navigation["layout"] == "shots" and "SH010" in navigation["shots"], navigation
                preferences = host.call("preferences")
                settings = preferences["settings"]
                settings["filename_template"] = "mac_{shot}_{type}_v{version}_{subversion}{format}"
                host.call("set_preferences", {"settings": settings, "names": ["mac_ignore"]})
                source = root / "frame.png"
                from PIL import Image
                Image.new("RGB", (32, 18), "red").save(source)
                target = {"project": name, "shot": "SH010", "sequence": None, "category": "keyframes"}
                imported = host.call("import_file", {**target, "source": str(source)})
                assert imported["name"].startswith("mac_SH010_") and "_v001_00" in imported["name"], imported
                source = root / "another.png"
                Image.new("RGB", (32, 18), "blue").save(source)
                subversion = host.call("import_file", {**target, "source": str(source), "subversion": True,
                                                       "targetExisting": imported["name"], "targetCategory": "keyframes"})
                assert "_v001_01" in subversion["name"], subversion
                preview = host.call("preview", {**target, "name": imported["name"]})
                assert preview["available"] and preview["mime"] == "image/jpeg"
                files = host.call("files", target)["files"]
                assert len(files) == 2 and all(item["nativeDragToken"] for item in files)
                assert host.call("drag_prepare")["ready"]
                host.call("drag_stop")
                # Real native AppKit pasteboard serialization, without an injected mouse gesture.
                helper = executable.parent / "BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge"
                probe = subprocess.run([str(helper)], input=json.dumps({"id": "inspect", "action": "inspect", "path": imported["path"]}) + "\n",
                                       text=True, capture_output=True, env=environment, timeout=20)
                assert probe.returncode == 0, probe.stderr
                result = json.loads(probe.stdout.strip())
                assert result["nativePaths"] == [imported["path"]], result
            finally:
                host.close()
            tools = subprocess.run([str(executable), "--install-ffmpeg"], env=environment,
                                   capture_output=True, timeout=600)
            assert tools.returncode == 0, (base / "ffmpeg-setup.log").read_text() if (base / "ffmpeg-setup.log").exists() else tools.stderr
            ffmpeg = desktop / "Tools/ffmpeg"
            video = root / "source.mov"
            subprocess.run([str(ffmpeg), "-hide_banner", "-nostdin", "-f", "lavfi", "-i", "color=red:size=32x18:rate=2",
                            "-t", "1", "-c:v", "mpeg4", str(video)], check=True, capture_output=True)
            host = NativeHost(executable, environment, log)
            try:
                assert host.call("preferences")["settings"]["filename_template"] == settings["filename_template"]
                assert host.call("projects")["projects"][0]["path"] == str(project)
                assert host.call("ffmpeg_status")["state"] == "ready"
                clip = host.call("import_file", {"project": name, "shot": "SH010", "source": str(video)})
                job = host.call("convert_begin", {"project": name, "shot": "SH010", "category": "video", "name": clip["name"]})
                import time
                for _ in range(60):
                    result = host.call("convert_status", {"jobId": job["jobId"]})
                    if result["state"] not in ("queued", "running"):
                        break
                    time.sleep(0.25)
                assert result["state"] == "complete", result
                assert len(list(Path(clip["path"]).with_suffix("").glob("*.png"))) == 2
            finally:
                host.close()
        assert sentinel.read_text() == '{"desktop":"unchanged"}'
        print("Packaged macOS host: install, independent settings, imports, subversions, persistence, pasteboard and FFmpeg conversion passed.")


if __name__ == "__main__":
    main()
