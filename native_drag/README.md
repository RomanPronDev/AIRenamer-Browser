# Direct drag handoff · 0.27.8

The interface stays in Chrome's actual side panel. No desktop panel, overlay, global mouse hook, administrator rights, or browser injection is added. These are experimental paths, not a compatibility guarantee for Adobe or Nuke.

## Chrome-backed files

Settings → a project → **Connect for drag**, or the connection strip in Files, accepts one project root folder dragged from Explorer. The browser's `webkitGetAsEntry()` grant lets the panel look up the current file with `DirectoryEntry.getFile()` and `FileEntry.file()`. The snapshot `File` is added unchanged to `DataTransfer.items`. It is never wrapped in `new File(...)`, never fetched into a Blob, and never combined with `DownloadURL` in this drag. Only the hovered file's metadata is requested; the project is not recursively enumerated. The cache holds at most 64 snapshots. Converted sequence directories are not handled by this path.

This grant belongs to the open panel session. Neither a saved path nor the existing native folder picker creates it. Browser security prevents granting it silently at startup. Name, size and modification-time checks catch common wrong-folder selections, but JavaScript cannot verify that an identically named copy belongs to the same absolute root. Users must drop the indicated root.

Chromium evidence: [DataObject drag serialization](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/clipboard/data_object.cc), [snapshot creation](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/modules/filesystem/dom_file_system_base.cc), [File constructors](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/fileapi/file.cc). Modern File System Access `getFile()` uses the name/blob constructor without the same backing-file flag, so it is not an equivalent substitute for this experiment.

## Automatic native handoff

Native drag is enabled by default. The extension warms the helper at Chrome/extension startup and when the panel reconnects. Grab any part of the file row, including the name or media icon, and drag directly to the destination. Use the small move handle (or Shift while starting a row drag) for shot-to-shot moves. The older drag window remains in the file menu. A one-time Chrome preference revision migrates the old opt-in/browser default to native; a later explicit choice of Chrome folder connection is respected.

At row `dragstart`, the panel cancels Chromium's drag and sends the file capability plus the gesture timestamp through Native Messaging. The companion uses a capability minted during Files listing to avoid another network tree traversal. A hidden Windows Forms control on an STA thread offers the original path as Windows `CF_HDROP` through `DoDragDrop`. It never shows a drag-source window. Requests expire after 750 ms and are rejected if the physical left button is already released. Missing files, stale tokens, and overlapping drags are rejected. Only Copy is offered to outside applications; the source is not moved or deleted.

The helper is compiled from `DragBridge.cs` using the Windows .NET Framework compiler, included inside the native companion by PyInstaller, and started once with redirected pipes and no console. It uses the machine's .NET Framework 4.x and Windows Forms runtime; no Qt/Electron/WebView runtime is added. At idle it waits for messages with no polling timer. Closing the native host closes its pipe and terminates the helper; changing back to Chrome mode stops it explicitly. No separate install action is needed after the existing Browser package update. FFmpeg remains outside the package.

## Verified and still unverified

Automated tests decode actual COM `CF_HDROP` memory through `DragQueryFileW` and verify original Unicode paths, STA operation, expired gesture rejection, missing files, helper reuse and shutdown, automatic browser startup and default migration, gesture cancellation, intact snapshot identity, and preserved shot-to-shot behavior.

On the development machine: cold readiness 81.24 ms; 50 warmed ping requests averaged 0.07 ms; working set 29.15 MiB; CPU-time increase was 0.0 seconds over the sampled idle interval. These figures describe the helper, not total Chrome/Python usage, and are not guarantees on other workstations.

**User-confirmed:** the native drop probe receives the correct original server file path. This confirms original-path delivery for that test, not acceptance by Adobe/Nuke. Version 0.27.8 addresses the panel-side trigger mismatch: previously only the left icon used native handoff and the feature required opt-in, while the usual filename drag still used Chromium's virtual DownloadURL.

**Still not independently verified:** actual Photoshop/After Effects/Nuke acceptance, multi-monitor/DPI behavior, and slow network drives. The Windows UI verification runtime failed before initialization, including after a reset, with `trusted Node process exited unexpectedly; kernel reset, rerun your request`. Automated checks are not an actual physical app drop. A delayed handoff or incompatible/elevated drop target can still reject a gesture. Finished/cancelled/error results now return to the panel instead of being discarded; the panel checks them only while a drag is active, for at most two minutes.

## Manual acceptance test

1. Install the local 0.27.8 Browser package; reopen Chrome so the new extension and companion are loaded.
2. Wait for **Drag a file into an app · Move handle for shots** in Files; no manual enable step is needed. No source window should appear.
3. In Settings, click **Open drag test target**. Drag a file's name, row, or left media icon from the panel into it in one continuous gesture. The displayed path must be the original project file, not a `chrome_drag` temporary copy. The probe logs accepted paths/formats to `%LOCALAPPDATA%\MediaRenamer\Browser\drag-probe.jsonl`.
4. Repeat with PNG and video, Unicode/spaces, a network project, quick release, Escape/cancel, several Chrome windows, different displays/scaling, and restart. Confirm no source rename/move/copy and no orphan helper after Chrome exits.
5. Repeat directly into ordinary, non-elevated Photoshop, After Effects and Nuke. A probe success establishes native path delivery but does not establish acceptance by these applications. Elevated targets may reject a drag from ordinary Chrome.
6. Verify dragging the move handle to another shot still moves and renames it; also test Shift + row drag.
7. Switch back to Chrome mode, connect the correct root folder from Explorer, hover a file, then drag its name into the native test target and a Chrome upload field. Verify the original path and reconnection after closing the panel.

If native handoff fails, record the displayed error and test the fallback in **⋯ → Drag into desktop app**. If both experiments fail, a native source surface or a browser embedding architecture remains necessary; a background script cannot bypass browser file grants.

## Build and checks

Run `native_drag/Build-DragBridge.ps1` before Windows integration tests. `browser_release.py` performs that build automatically before packaging the companion. `tests/test_browser_drag.py` checks COM/protocol behavior; `browser/local-files.test.cjs`, `browser/live.test.cjs`, and `browser/background.test.cjs` check the panel and worker contracts. The current build remains local and is not published to GitHub.
