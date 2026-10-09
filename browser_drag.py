# SPDX-License-Identifier: GPL-3.0-only
"""Optional single-gesture Windows drag bridge; starts once and blocks when idle."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import browser_platform


class NativeDrag:
    def __init__(self):
        self.process = None
        self.pending = {}
        self.lock = threading.Lock()
        self.last = {}
        self.reader = None

    def start(self):
        if os.name != "nt" and not browser_platform.is_macos():
            raise RuntimeError("Native drag requires Windows or macOS.")
        if self.process is not None and self.process.poll() is None:
            return
        path = browser_platform.bridge_executable()
        if not path.is_file():
            raise RuntimeError("Native drag helper is missing. Install the complete Browser package.")
        self.process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=0x08000000 if os.name == "nt" else 0)
        self.reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self.reader.start()

    def _read(self, process):
        try:
            for line in process.stdout:
                answer = json.loads(line)
                if answer.get("phase") == "finished":
                    self.last = answer
                    continue
                with self.lock:
                    request = self.pending.get(answer.get("id"))
                    if request:
                        request["answer"] = answer
                        request["event"].set()
        finally:
            with self.lock:
                for request in self.pending.values():
                    request["answer"] = {"ok": False, "error": "Native drag helper stopped."}
                    request["event"].set()

    def request(self, action, path=None, *, issued=None):
        self.start()
        identifier = uuid.uuid4().hex
        request = {"event": threading.Event()}
        message = {"id": identifier, "action": action}
        if path is not None:
            message["path"] = path
        if action == "begin":
            message["expires"] = int(issued if issued is not None else time.time() * 1000) + 750
            self.last = {}
        with self.lock:
            self.pending[identifier] = request
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
                self.process.stdin.flush()
            except Exception:
                self.pending.pop(identifier, None)
                raise
        try:
            if not request["event"].wait(300 if action == "choose_folder" else 4):
                raise RuntimeError("Native drag helper did not respond. Release the mouse and retry.")
            answer = request["answer"]
            if not answer.get("ok"):
                raise RuntimeError(answer.get("error", "Native drag failed."))
            return answer
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=2)
            finally:
                if self.reader is not None:
                    self.reader.join(timeout=2)
                self.process.stdout.close()
                self.process = None
