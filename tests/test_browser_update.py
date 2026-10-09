# SPDX-License-Identifier: GPL-3.0-only
"""Unattended release verification avoids downgrades and untrusted archives."""
import hashlib
import json
import zipfile

import pytest

import browser_update


def asset(version, data=b"release"):
    name = f"AIRenamer-Browser-{version}.zip"
    return {"name": name, "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "browser_download_url":
                f"https://github.com/{browser_update.REPOSITORY}/releases/download/v{version}/{name}"}


def test_select_release_only_accepts_newer_exact_verified_asset():
    release = {"tag_name": "v0.26.29", "draft": False, "prerelease": False,
               "assets": [asset("0.26.29")]}
    assert browser_update.select_release(release, "0.26.28", target_suffix="")[0] == "0.26.29"
    assert browser_update.select_release(release, "0.26.29", target_suffix="") is None
    assert browser_update.select_release(release, "0.27.0", target_suffix="") is None
    release["assets"][0]["digest"] = None
    with pytest.raises(browser_update.UpdateError, match="SHA-256"):
        browser_update.select_release(release, "0.26.28", target_suffix="")


def test_extract_release_checks_version_and_blocks_path_escape(tmp_path):
    package = tmp_path / "release.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("version.json", json.dumps({"version": "0.26.29"}))
        archive.writestr("browser/manifest.json", json.dumps({"version": "0.26.29"}))
        archive.writestr("Install-BrowserNative.ps1", "install")
        archive.writestr("dist/MediaRenamerBrowserNative.exe", "exe")
    browser_update.extract_release(package, tmp_path / "okay", "0.26.29", target_suffix="")
    with pytest.raises(browser_update.UpdateError, match="version"):
        browser_update.extract_release(package, tmp_path / "mismatch", "0.26.30", target_suffix="")
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("../escape.txt", "unsafe")
    with pytest.raises(browser_update.UpdateError, match="unsafe path"):
        browser_update.extract_release(package, tmp_path / "unsafe", "0.26.29", target_suffix="")
    assert not (tmp_path / "escape.txt").exists()
