# AIRenamer Browser for macOS — 0.30.0 test build

The macOS port targets **Apple Silicon and macOS 14 or newer**, using the same
Chrome side panel and independent settings as Browser 0.27.9. Intel Macs and
Safari are outside this first port. Windows keeps its existing package format.

The refreshed 0.29.0 Mac build uses a **directory runtime**: the host executable,
`_internal/Python.framework` and its libraries travel together. Framework links
remain links in the ZIP, during automatic extraction, and when setup copies the
runtime. The builder re-signs the collected framework's final resource set and
verifies the framework, AppKit helper and host, including after ZIP round-trip.
Setup checks signatures before launching the host; the installer checks again
before native-host registration and after copying. Broken bundles fail before
replacing an existing installation. No security settings or quarantine flags
are changed automatically.

If an earlier build reported **Python.framework is damaged**, cancel that prompt
and rebuild from the refreshed source ZIP using **Build-Browser.command** on the
Mac. Extract the newly generated package into a new folder and run its setup;
copying only the host executable from the directory runtime will not work.
The source changes and simulated packaging tests were checked on Windows;
real macOS compilation/signature verification and Gatekeeper acceptance still
need confirmation on a Mac. Valid ad-hoc signatures do not constitute Developer
ID notarization: trusted-download approval may still be required for this test
build. A frictionless team distribution needs Developer ID signing/notarization.

## Installation

1. Extract `AIRenamer-Browser-0.30.0-macos-arm64.zip` into a local folder.
2. Open `Setup-Browser.command`. The test build is not Developer ID signed or
   notarized. If macOS blocks it, review the launch message and use the system's
   approval for this trusted download; setup does not change security settings.
3. Open `chrome://extensions`, enable **Developer mode**, and select **Load unpacked**.
   Select the `Extension` folder printed by setup:
   `~/Library/Application Support/MediaRenamer/Browser/Extension`.
4. Copy the ID displayed on the AIRenamer extension card back into setup.
   This registers the local helper for that exact extension ID.
5. Pin AIRenamer and open its panel, or press **Command+Shift+Y**.

An update reuses the stored extension ID. Existing project paths, naming rules,
folder structures, exclusions and Chrome preferences remain in place.
User data is under `~/Library/Application Support/MediaRenamer/Browser`;
the installer replaces only extension and host files. Failed installation
restores the previous extension and native-host registration.

Use **Browse…** to select a local project or a mounted network project.
Mounted server paths normally start with `/Volumes/`. Reconnect the server in
Finder if it is unavailable; AIRenamer does not translate Windows drive letters
or mount SMB shares. **Show in Finder** selects the original file or sequence.

## Video tools

Setup prepares FFmpeg in the background. No Python, Homebrew or administrator
installation is required. **Settings → Video tools** shows preparation status,
download progress and a retry action. Conversion also prepares the tool on
first use if setup was deferred or failed.

The pinned FFmpeg 9.0.2 arm64 ZIP comes directly from
[Martin Riedl's build server](https://ffmpeg.martin-riedl.de/). The expected archive
SHA-256 is `c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924`.
The downloaded binary must be arm64, compatible with macOS 14, and runnable.
It is stored in `~/Library/Application Support/MediaRenamer/Tools/ffmpeg`,
with a receipt containing its binary hash and source URL. FFmpeg is not inside
the Browser ZIP. Image operations remain available if video-tool setup fails.

## Dragging files into applications

Native drag is prepared automatically. Drag the file's row or name while holding
the mouse button; the AppKit helper offers the **original file URL** with Copy
semantics. It does not copy media into a temporary download. The move handle or
Shift+drag retains shot-to-shot movement. The file menu also offers a native
drag window with an ordinary AppKit drag handle.

**Acceptance remains pending on a physical Mac.** A successful build or
pasteboard probe does not establish that Chrome's gesture handoff works in
Photoshop, After Effects or Nuke. This test implementation uses public AppKit
APIs without injecting global mouse events and does not request Accessibility.
If physical testing establishes that another mechanism or Accessibility is
required, that work must be completed before calling direct dragging supported.

Before a team rollout:

- Open **Settings → Open drag test target**. Drop PNG and MOV files and verify
  the displayed path is the actual project path. Results are recorded in
  `Browser/drag-probe.jsonl`.
- Repeat direct single-gesture drops into Photoshop, After Effects and Nuke;
  confirm the application imports the original file. Test local files and
  mounted network projects with spaces and non-English names.
- Check quick release, Escape/cancel, rejected drops, multiple displays, Chrome
  restart and helper shutdown. Check the move handle and Shift+drag separately.
- Verify first project setup, naming/subversions, image and video previews,
  conversion/cancellation, themes, panel resizing and an update retaining data.

## Updates and removal

The existing periodic updater selects only `*-macos-arm64.zip` on this platform,
validates the SHA-256, versions and macOS package marker, and runs the per-user
installer. It cannot install a Windows ZIP. `Update-Browser.command` checks
manually. The first macOS package has not been published; updates require a
subsequent version with its platform-specific asset in the release.

`Uninstall-Browser.command` unregisters the helper. Remove the extension in
Chrome afterward. Saved projects/settings and old host files are preserved so
running Chrome processes are not disrupted and reinstall can retain data.

## Building and verification

For a source ZIP transferred to another Mac, install Python 3.14 for macOS and
Apple Command Line Tools, then open **Build-Browser.command** in the extracted
source folder. It checks macOS 14+ and native Apple Silicon, creates a local
build environment, downloads the pinned Python dependencies and creates
`dist/AIRenamer-Browser-0.30.0-macos-arm64.zip`. Finder selects the resulting ZIP;
extract it into a separate folder and open its `Setup-Browser.command`.
No source is uploaded. Build output is recorded in `build-macos.log`.
The launcher itself has not been executed on macOS in this Windows workspace.

Build on a Mac with an arm64 Python 3.14.5 and Xcode Command Line Tools:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r browser-requirements.txt
.venv/bin/python browser_release.py
.venv/bin/python tests/macos_protocol_smoke.py
```

The workflow in `.github/workflows/browser-macos.yml` builds on macOS 14/15
arm64 and uploads a test ZIP, without publishing a release. It checks the real
frozen protocol, isolated user data, installer, AppKit file-URL serialization,
FFmpeg download and PNG conversion. It does not automate Adobe/Nuke acceptance.
The macOS 14 hosted runner is scheduled to retire in November 2026; retaining
that system as a tested minimum afterward requires a macOS 14 runner.

The user has requested local-only work for this port. The workflow has not
been run and the source has not been pushed to GitHub. Use the local build
commands above when a Mac becomes available.

Local Windows validation passed 209 Python tests (2 skipped) and 36 browser
JavaScript tests. The Windows host was frozen successfully as a regression
check. The actual pinned FFmpeg archive passed checksum, arm64 architecture
and deployment-minimum validation. These checks do not verify Mac compilation,
installation or interactive dragging.

The helper and host use ad-hoc signing for testing. A public release requires
separate Developer ID signing, notarization and physical acceptance. Source,
build scripts and license notices are included in each Browser package.

`python browser_release.py --source-only` creates a complete source ZIP on
Windows or macOS. This source handoff is buildable on a Mac; it is not an
installable macOS binary package.
