# SPDX-License-Identifier: GPL-3.0-only
"""Platform routing, transactional installation and verified tool provisioning."""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import stat
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

import browser_ffmpeg
import browser_mac_install
import browser_mac_bundle
import browser_native
import browser_platform
import browser_release
import browser_update
import runtime_support


def mac_package(root, version="0.28.0"):
    root.mkdir(parents=True, exist_ok=True)
    contents = {
        "version.json": json.dumps({"version": version}),
        "package-target.json": json.dumps({"platform": "macos", "architecture": "arm64", "minimumMacOS": "14.0"}),
        "browser/manifest.json": json.dumps({"name": "AIRenamer · Browser", "version": version}),
        "browser/live.js": "// " + version,
        "Setup-Browser.command": "#!/bin/bash\n",
        "dist/MediaRenamerBrowserNative/MediaRenamerBrowserNative": "host",
        "dist/MediaRenamerBrowserNative/_internal/Python.framework/Python": "python library",
        "dist/MediaRenamerBrowserNative/_internal/Python.framework/Resources/Info.plist": "python plist",
        "dist/MediaRenamerBrowserNative/BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge": "bridge",
        "dist/MediaRenamerBrowserNative/BrowserDragBridge.app/Contents/Info.plist": "plist",
    }
    for name, content in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_macos_runtime_uses_posix_home_without_localappdata(monkeypatch):
    monkeypatch.setattr(runtime_support.sys, "platform", "darwin")
    paths = runtime_support.get_local_runtime_paths({"HOME": "/Users/artist", "LOCALAPPDATA": "ignored"})
    assert paths.root == "/Users/artist/Library/Application Support/MediaRenamer"
    assert paths.tools == paths.root + "/Tools"
    assert paths.state == paths.root + "/State"
    assert runtime_support.emergency_diagnostics_dir({"HOME": "/Users/artist"}) == paths.root + "/Diagnostics"
    with pytest.raises(runtime_support.RuntimePathError):
        runtime_support.get_local_base_dir({"HOME": "relative"})


def test_macos_routes_picker_and_finder_without_shell_interpolation(monkeypatch):
    monkeypatch.setattr(browser_platform, "is_macos", lambda: True)
    launcher = Mock()
    monkeypatch.setattr(browser_platform.subprocess, "Popen", launcher)
    path = "/Volumes/Production/відео $(literal).mov"
    browser_platform.open_folder(path, reveal=True)
    assert launcher.call_args.args[0] == ["/usr/bin/open", "-R", path]
    bridge = Mock()
    bridge.request.return_value = {"path": "/Volumes/Project"}
    monkeypatch.setattr(browser_native.browser_drag, "NativeDrag", lambda: bridge)
    assert browser_native._choose_project_folder("/Volumes") == "/Volumes/Project"
    bridge.request.assert_called_once_with("choose_folder", "/Volumes")
    bridge.close.assert_called_once()


def test_mac_asset_selection_never_chooses_windows_package():
    version = "0.28.0"
    def asset(suffix):
        name = f"AIRenamer-Browser-{version}{suffix}.zip"
        return {"name": name, "size": 100, "digest": "sha256:" + "a" * 64,
                "browser_download_url": f"https://github.com/{browser_update.REPOSITORY}/releases/download/v{version}/{name}"}
    release = {"tag_name": "v" + version, "assets": [asset(""), asset("-macos-arm64")]}
    assert browser_update.select_release(release, "0.27.9", target_suffix="-macos-arm64")[1]["name"].endswith("-macos-arm64.zip")
    assert browser_update.select_release(release, "0.27.9", target_suffix="")[1]["name"] == "AIRenamer-Browser-0.28.0.zip"
    release["assets"].pop()
    with pytest.raises(browser_update.UpdateError, match="missing"):
        browser_update.select_release(release, "0.27.9", target_suffix="-macos-arm64")


def test_mac_extraction_requires_platform_marker_and_complete_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", lambda root: None)
    source = mac_package(tmp_path / "package")
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for file in source.rglob("*"):
            if file.is_file():
                output.write(file, file.relative_to(source).as_posix())
    browser_update.extract_release(archive, tmp_path / "valid", "0.28.0", target_suffix="-macos-arm64")
    (source / "package-target.json").write_text('{}')
    with zipfile.ZipFile(archive, "w") as output:
        for file in source.rglob("*"):
            if file.is_file():
                output.write(file, file.relative_to(source).as_posix())
    with pytest.raises(browser_update.UpdateError, match="macOS"):
        browser_update.extract_release(archive, tmp_path / "invalid", "0.28.0", target_suffix="-macos-arm64")


def test_install_update_and_rollback_preserve_settings_and_native_registration(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", lambda root: None)
    monkeypatch.setattr(browser_mac_install, "get_local_base_dir", lambda: str(tmp_path / "Application Support/MediaRenamer"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    source = mac_package(tmp_path / "source")
    installed = browser_mac_install.install(source, extension_id="a" * 32, open_chrome=False, prepare_ffmpeg=False)
    base = Path(installed["extensionFolder"]).parent
    config = base / "Config/settings.json"
    config.parent.mkdir()
    config.write_text('{"custom":"keep"}')
    native = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.airenamer.browser.json"
    saved_native, saved_installation = native.read_bytes(), (base / "installation.json").read_bytes()
    source = mac_package(tmp_path / "update", "0.28.1")
    original_write = browser_mac_install._write
    def failing_write(path, data):
        if path.name == "installation.json":
            raise OSError("disk full")
        original_write(path, data)
    monkeypatch.setattr(browser_mac_install, "_write", failing_write)
    with pytest.raises(OSError, match="disk full"):
        browser_mac_install.install(source, open_chrome=False, prepare_ffmpeg=False)
    assert native.read_bytes() == saved_native
    assert (base / "installation.json").read_bytes() == saved_installation
    assert json.loads((base / "Extension/manifest.json").read_text())["version"] == "0.28.0"
    assert config.read_text() == '{"custom":"keep"}'
    monkeypatch.setattr(browser_mac_install, "_write", original_write)
    browser_mac_install.install(source, open_chrome=False, prepare_ffmpeg=False)
    assert json.loads((base / "Extension/manifest.json").read_text())["version"] == "0.28.1"
    assert config.read_text() == '{"custom":"keep"}'
    assert json.loads(native.read_text())["allowed_origins"] == ["chrome-extension://" + "a" * 32 + "/"]


def macho(cpu=0x0100000C, minimum=0xE0000):
    return struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, 1, 24, 0, 0) + struct.pack("<6I", 0x32, 24, 1, minimum, 0xE0000, 0)


def test_ffmpeg_rejects_wrong_architecture_and_newer_deployment_target(tmp_path):
    file = tmp_path / "ffmpeg"
    file.write_bytes(macho())
    browser_ffmpeg.check_macho(file)
    for data in (macho(cpu=0x01000007), macho(minimum=0xF0000), b"not a Mach-O"):
        file.write_bytes(data)
        with pytest.raises(RuntimeError):
            browser_ffmpeg.check_macho(file)


def ffmpeg_download(monkeypatch, content, *, corrupted=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("ffmpeg", content)
    data = stream.getvalue()
    monkeypatch.setattr(browser_ffmpeg, "SHA256", "0" * 64 if corrupted else hashlib.sha256(data).hexdigest())
    response = io.BytesIO(data)
    response.headers = {"Content-Length": str(len(data))}
    request = Mock(return_value=response)
    monkeypatch.setattr(browser_ffmpeg.urllib.request, "urlopen", request)
    monkeypatch.setattr(browser_ffmpeg.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, b"ffmpeg version 9.0.2", b"")))
    return request


def test_ffmpeg_pinned_download_installs_and_reuses_verified_binary(tmp_path, monkeypatch):
    target = tmp_path / "Tools/ffmpeg"
    downloader = ffmpeg_download(monkeypatch, macho())
    assert browser_ffmpeg.resolve(str(target)) == str(target)
    assert target.read_bytes() == macho()
    assert browser_ffmpeg.status(target)["state"] == "ready"
    assert browser_ffmpeg.resolve(str(target)) == str(target)
    assert downloader.call_count == 1


def test_ffmpeg_bad_checksum_does_not_commit_and_can_retry(tmp_path, monkeypatch):
    target = tmp_path / "Tools/ffmpeg"
    ffmpeg_download(monkeypatch, macho(), corrupted=True)
    with pytest.raises(Exception, match="SHA-256"):
        browser_ffmpeg.resolve(str(target))
    assert not target.exists()
    assert browser_ffmpeg.status(target)["state"] == "error"
    ffmpeg_download(monkeypatch, macho())
    assert browser_ffmpeg.resolve(str(target)) == str(target)


def test_source_archive_contains_macos_installer_helper_and_workflow():
    sources = browser_release.source_files()
    assert len(sources) == len(set(sources))
    for name in ("browser_mac_install.py", "browser_mac_bundle.py", "native_drag/DragBridge.swift", "Setup-Browser.command",
                 ".github/workflows/browser-macos.yml", "tests/macos_protocol_smoke.py"):
        assert name in sources


def test_mac_package_builder_includes_helper_and_executable_setup_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mac_bundle, "sign_runtime", lambda root: None)
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", lambda root: None)
    source = mac_package(tmp_path / "source")
    (source / "browser/README.md").write_text("macOS test package")
    helper = source / "build_assets/BrowserDragBridge.app"
    shutil.copytree(source / "dist/MediaRenamerBrowserNative/BrowserDragBridge.app", helper)
    monkeypatch.setattr(browser_release, "ROOT", source)
    monkeypatch.setattr(browser_release.browser_platform, "package_suffix", lambda: "-macos-arm64")
    monkeypatch.setattr(browser_release, "source_files", lambda: ["version.json", "browser/manifest.json", "browser/README.md", "Setup-Browser.command"])
    package = browser_release.package(build=False)
    assert package.name == "AIRenamer-Browser-0.28.0-macos-arm64.zip"
    with zipfile.ZipFile(package) as archive:
        assert (archive.getinfo("Setup-Browser.command").external_attr >> 16) & 0o777 == 0o755
        assert "dist/MediaRenamerBrowserNative/BrowserDragBridge.app/Contents/MacOS/BrowserDragBridge" in archive.namelist()
        assert not any(name.endswith(".exe") for name in archive.namelist())
    browser_update.extract_release(package, tmp_path / "extracted", "0.28.0", target_suffix="-macos-arm64")


def test_source_package_is_buildable_handoff_without_runtime_binaries(tmp_path, monkeypatch):
    root = browser_release.ROOT
    # Keep the checked-in source selection; direct only the output to a test root.
    source = tmp_path / "source"
    browser_release.stage_source(source)
    monkeypatch.setattr(browser_release, "ROOT", source)
    package = browser_release.source_package()
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert json.loads(archive.read("version.json"))["version"] == json.loads((root / "version.json").read_text())["version"]
        assert b"\r\n" not in archive.read("Setup-Browser.command")
        assert b"(browser/MACOS.md)" in archive.read("README.md")
        assert not any(name.endswith(".exe") or name.startswith("dist/") for name in archive.namelist())


def test_corrupt_ffmpeg_receipt_and_abandoned_status_are_recoverable(tmp_path):
    target = tmp_path / "ffmpeg"
    target.write_bytes(macho())
    target.with_name("ffmpeg-receipt.json").write_text("[]")
    assert not browser_ffmpeg.valid(target)
    browser_ffmpeg.status_path(target).write_text('{"state":"downloading","updatedAt":null}')
    assert browser_ffmpeg.status(target)["state"] == "error"


def create_link_or_skip(target, link):
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip("This host cannot create POSIX symlinks: " + str(exc))


def linked_runtime(tmp_path):
    package = mac_package(tmp_path / "package")
    runtime = package / "dist/MediaRenamerBrowserNative"
    framework = runtime / "_internal/Python.framework"
    version = framework / "Versions/3.14"
    version.mkdir(parents=True)
    os.replace(framework / "Python", version / "Python")
    os.replace(framework / "Resources", version / "Resources")
    create_link_or_skip("3.14", framework / "Versions/Current")
    create_link_or_skip("Versions/Current/Python", framework / "Python")
    create_link_or_skip("Versions/Current/Resources", framework / "Resources")
    return package, runtime


def test_framework_links_survive_archive_and_installer_copy(tmp_path, monkeypatch):
    source, runtime = linked_runtime(tmp_path)
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for item in source.rglob("*"):
            if item.is_file() and not item.is_relative_to(runtime):
                archive.write(item, item.relative_to(source).as_posix())
        browser_mac_bundle.add_tree(archive, runtime)
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", lambda root: None)
    extracted = tmp_path / "extracted"
    browser_update.extract_release(archive_path, extracted, "0.28.0", target_suffix="-macos-arm64")
    copied = extracted / "dist/MediaRenamerBrowserNative/_internal/Python.framework"
    assert copied.joinpath("Python").is_symlink()
    assert os.readlink(copied / "Resources") == "Versions/Current/Resources"
    assert (copied / "Resources/Info.plist").read_text() == "python plist"
    monkeypatch.setattr(browser_mac_install, "get_local_base_dir", lambda: str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    result = browser_mac_install.install(extracted, extension_id="a" * 32, open_chrome=False, prepare_ffmpeg=False)
    installed = Path(result["hostPath"]).parent / "_internal/Python.framework"
    assert installed.joinpath("Python").is_symlink()
    assert os.readlink(installed / "Versions/Current") == "3.14"
    assert (installed / "Resources/Info.plist").read_bytes() == (copied / "Resources/Info.plist").read_bytes()


@pytest.mark.parametrize("target", ["/tmp/outside", "../../../../outside", "C:/outside", "..\\outside"])
def test_macos_archive_rejects_escaping_links_before_writing(tmp_path, target):
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("version.json", '{"version":"0.28.0"}')
        info = zipfile.ZipInfo("dist/MediaRenamerBrowserNative/_internal/escape")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, target)
    destination = tmp_path / "extracted"
    with pytest.raises(browser_update.UpdateError, match="symlink"):
        browser_update.extract_release(package, destination, "0.28.0", target_suffix="-macos-arm64")
    assert not destination.exists()


def test_archive_rejects_children_under_links_and_windows_rejects_all_links(tmp_path):
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        info = zipfile.ZipInfo("dist/MediaRenamerBrowserNative/_internal/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "actual")
        archive.writestr(info.filename + "/child", "bad")
    with pytest.raises(browser_update.UpdateError, match="beneath a symlink"):
        browser_update.extract_release(package, tmp_path / "mac", "0.28.0", target_suffix="-macos-arm64")
    with pytest.raises(browser_update.UpdateError, match="unsafe path"):
        browser_update.extract_release(package, tmp_path / "windows", "0.28.0", target_suffix="")


def test_broken_or_cyclic_framework_link_is_rejected(tmp_path):
    _, runtime = linked_runtime(tmp_path)
    current = runtime / "_internal/Python.framework/Versions/Current"
    current.unlink()
    create_link_or_skip("Current", current)
    with pytest.raises(ValueError, match="cyclic"):
        browser_mac_bundle.validate_layout(runtime)


def test_sign_collected_framework_then_verify_final_runtime(tmp_path, monkeypatch):
    runtime = mac_package(tmp_path / "package") / "dist/MediaRenamerBrowserNative"
    calls = []
    def codesign(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(browser_mac_bundle.subprocess, "run", codesign)
    monkeypatch.delenv("AIRENAMER_SIGNING_IDENTITY", raising=False)
    browser_mac_bundle.sign_runtime(runtime)
    signed = [call for call in calls if "--sign" in call]
    verified = [call for call in calls if "--verify" in call]
    assert len(signed) == len(verified) == 3
    assert signed[0][-1].endswith("Python.framework")
    assert all("--deep" not in call for call in signed)
    assert all("--strict" in call and "--deep" in call for call in verified)


def test_signature_failure_stops_registration_and_preserves_existing_install(tmp_path, monkeypatch):
    source = mac_package(tmp_path / "package")
    monkeypatch.setattr(browser_mac_install, "get_local_base_dir", lambda: str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(browser_mac_install, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", lambda root: None)
    browser_mac_install.install(source, extension_id="a" * 32, open_chrome=False, prepare_ffmpeg=False)
    base = tmp_path / "local/Browser"
    before = (base / "installation.json").read_bytes()
    def broken(root):
        raise ValueError("Code signature check/signing failed: resources missing")
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", broken)
    with pytest.raises(ValueError, match="signature"):
        browser_mac_install.install(source, open_chrome=False, prepare_ffmpeg=False)
    assert (base / "installation.json").read_bytes() == before
    assert len(list((base / "Host").iterdir())) == 1


def test_real_codesign_failure_reports_missing_resources(tmp_path, monkeypatch):
    runtime = mac_package(tmp_path / "package") / "dist/MediaRenamerBrowserNative"
    monkeypatch.setattr(browser_mac_bundle.subprocess, "run", Mock(return_value=
        subprocess.CompletedProcess([], 1, "", "a sealed resource is missing or invalid")))
    with pytest.raises(ValueError, match="Python.framework.*sealed resource"):
        browser_mac_bundle.verify_signatures(runtime)


def test_copied_runtime_is_verified_before_replacing_extension(tmp_path, monkeypatch):
    source = mac_package(tmp_path / "package")
    monkeypatch.setattr(browser_mac_install, "get_local_base_dir", lambda: str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(browser_mac_install, "sys", SimpleNamespace(platform="darwin"))
    checked = []
    def verify(root):
        checked.append(root)
        if len(checked) == 2:
            raise ValueError("copied framework signature failed")
    monkeypatch.setattr(browser_mac_bundle, "verify_signatures", verify)
    with pytest.raises(ValueError, match="copied framework"):
        browser_mac_install.install(source, extension_id="a" * 32, open_chrome=False, prepare_ffmpeg=False)
    assert len(checked) == 2
    assert not (tmp_path / "local/Browser/installation.json").exists()
    assert not (tmp_path / "local/Browser/Extension").exists()
    assert not list((tmp_path / "local/Browser/Host").iterdir())
