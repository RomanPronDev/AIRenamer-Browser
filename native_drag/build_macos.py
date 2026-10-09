# SPDX-License-Identifier: GPL-3.0-only
"""Build a stable-identity AppKit helper on macOS arm64."""
import os
import json
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build():
    if sys.platform != "darwin":
        raise RuntimeError("Build the AppKit helper on macOS.")
    bundle = ROOT / "build_assets/BrowserDragBridge.app"
    executable = bundle / "Contents/MacOS/BrowserDragBridge"
    executable.parent.mkdir(parents=True, exist_ok=True)
    info = {"CFBundleIdentifier": "com.airenamer.browser.drag", "CFBundleName": "AIRenamer Drag",
            "CFBundleExecutable": "BrowserDragBridge", "CFBundlePackageType": "APPL",
            "CFBundleVersion": json.loads((ROOT / "version.json").read_text())["version"],
            "CFBundleShortVersionString": json.loads((ROOT / "version.json").read_text())["version"],
            "LSMinimumSystemVersion": "14.0", "LSUIElement": True,
            "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication"}
    with (bundle / "Contents/Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O", "-target", "arm64-apple-macos14.0",
                    "-framework", "AppKit", str(ROOT / "native_drag/DragBridge.swift"), "-o", str(executable)], check=True)
    subprocess.run(["codesign", "--force", "--sign", os.environ.get("AIRENAMER_SIGNING_IDENTITY", "-"),
                    str(bundle)], check=True)
    return bundle


if __name__ == "__main__":
    print(build())
