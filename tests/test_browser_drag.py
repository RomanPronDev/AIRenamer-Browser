"""Windows bridge protocol integration and host validation, no synthetic mouse input."""
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import unittest

import browser_drag
import browser_native


class DirectDragValidation(unittest.TestCase):
    def test_live_file_capability_avoids_another_network_tree_scan(self):
        from unittest.mock import Mock
        host = browser_native.BrowserHost.__new__(browser_native.BrowserHost)
        host._native_drag = Mock()
        host._direct_files = {"granted": ("X:\\Project\\clip.mov", time.time() + 60),
                              "old": ("X:\\Project\\old.mov", time.time() - 1)}
        issued = time.time() * 1000
        with patch.object(browser_native, "_file") as lookup:
            host.drag_direct({"fileToken": "granted", "issued": issued})
            host._native_drag.request.assert_called_once_with("begin", "X:\\Project\\clip.mov", issued=issued)
            for token in ("old", "unknown", ["granted"]):
                with self.assertRaisesRegex(browser_native.HostError, "access expired"):
                    host.drag_direct({"fileToken": token, "issued": issued})
            lookup.assert_not_called()
    def test_drag_status_returns_completion_without_restarting_helper(self):
        from unittest.mock import Mock
        host = browser_native.BrowserHost.__new__(browser_native.BrowserHost)
        host._native_drag = Mock()
        host._native_drag.last = {"id": "drag-1", "phase": "finished", "ok": False, "error": "Target rejected file"}
        result = host.drag_status({})
        self.assertEqual(result, host._native_drag.last)
        result["error"] = "changed"
        self.assertEqual(host._native_drag.last["error"], "Target rejected file")
        host._native_drag.request.assert_not_called()

    def test_expired_gesture_does_not_resolve_file_or_launch_helper(self):
        host = browser_native.BrowserHost.__new__(browser_native.BrowserHost)
        with patch.object(browser_native, "_file") as lookup:
            for value in (None, "now", time.time() * 1000 - 5000, time.time() * 1000 + 5000):
                with self.assertRaisesRegex(browser_native.HostError, "expired"):
                    host.drag_direct({"issued": value})
            lookup.assert_not_called()

    def test_only_a_validated_project_file_reaches_the_helper(self):
        host = browser_native.BrowserHost.__new__(browser_native.BrowserHost)
        from unittest.mock import Mock
        host._native_drag = Mock()
        issued = time.time() * 1000
        with patch.object(browser_native, "_file", return_value=("X:\\Project\\clip.mov", None)) as lookup:
            host.drag_direct({"project": "Project", "sequence": "45", "shot": "ham0010",
                              "category": "video", "name": "clip.mov", "issued": issued})
            lookup.assert_called_once_with("Project", "45", "ham0010", "video", "clip.mov")
            host._native_drag.request.assert_called_once_with("begin", "X:\\Project\\clip.mov", issued=issued)


@unittest.skipUnless(os.name == "nt" and (Path(__file__).resolve().parents[1] /
                    "build_assets/BrowserDragBridge.exe").is_file(), "Compiled Windows helper required")
class WindowsDragProtocol(unittest.TestCase):
    def setUp(self):
        self.bridge = browser_drag.NativeDrag()

    def tearDown(self):
        self.bridge.close()

    def test_file_drop_unicode_paths_sta_and_no_button_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "відео with spaces.mov"
            path.write_bytes(b"probe")
            self.assertTrue(self.bridge.request("ping")["ready"])
            process = self.bridge.process
            answer = self.bridge.request("inspect", str(path))
            self.assertEqual(answer["paths"], [str(path)])
            self.assertEqual(answer["nativePaths"], [str(path)])
            self.assertEqual(answer["cfHdrop"], 15)
            self.assertEqual(answer["format"], "FileDrop")
            self.assertEqual(answer["apartment"], "STA")
            self.assertIs(self.bridge.process, process)
            with self.assertRaisesRegex(RuntimeError, "expired"):
                self.bridge.request("begin", str(path), issued=0)
            with self.assertRaisesRegex(RuntimeError, "no longer exists"):
                self.bridge.request("inspect", str(path) + ".missing")
            # A fresh ping proves earlier rejected commands did not corrupt the STA loop.
            self.assertTrue(self.bridge.request("ping")["ready"])
            self.bridge.close()
            self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
