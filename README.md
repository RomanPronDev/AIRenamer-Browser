# AIRenamer Browser — 0.27.0

AIRenamer is a Chrome side panel connected to the same local projects, shot files, naming rules, and move operations as the desktop application. All panel labels are in English. Chrome starts the local Native Messaging companion when needed; the desktop application does not have to stay open.

## Install and update on Windows

1. Extract the archive and run `Setup-Browser.cmd`.
2. On first install, enable **Developer mode** in `chrome://extensions`, select **Load unpacked**, and choose `%LOCALAPPDATA%\MediaRenamer\Browser\Extension`.
3. Pin AIRenamer in Chrome. Click its icon or press `Ctrl+Shift+Y` to open the side panel.
4. After this one-time installation, Chrome checks for a newer AIRenamer Browser GitHub release about every six hours while it is running. The local companion downloads a newer package, checks its SHA-256 and version, and installs it to the same folder. The extension reloads when Chrome is idle. Opening Chrome also checks for an already installed update. If needed, `Update-Browser.cmd` remains a manual fallback.

Chrome needs **Load unpacked** only for the first installation on each machine. An unpacked extension cannot install itself into Chrome silently; afterward, AIRenamer updates its already loaded folder automatically. Keep that folder in place. The updater reads only stable, published releases from `RomanPronDev/AIRenamer-Browser`.

Setup reuses a working `%LOCALAPPDATA%\MediaRenamer\Tools\ffmpeg.exe`. If it is missing, setup starts a background download of the GPL Windows build from BtbN, verifies its published SHA-256, and saves it in that folder for later conversions. Conversion also waits for or retries that setup if needed. FFmpeg is not included in the Browser archive.

## In the panel

Select a project from the picker at the top. Projects with sequences show a second picker; projects whose first-level folders are shots show those shots directly. Project, sequence, and shot selection persist when switching Chrome tabs. Click a shot to list its files. Drop downloaded media onto a shot or use **To shot** in Recent Downloads to import and rename it.

**Project folders** are managed in Settings. Add a root folder, change an existing project's folder, or remove its shortcut without deleting files. The Browser and desktop app use the same per-machine project list and detected layouts. If that list is empty when the Browser first opens, it shows a folder-selection welcome dialog. Existing project paths remain untouched during installation and updates, including paths to temporarily unavailable drives.

**Recent Downloads** lists the complete Chrome download history, including non-media files. Previews never load automatically: click the thumbnail icon to load a small preview of one file, or click its name to load and open its preview. Only that selected file is requested; its preview is reused if you click it again. Unsupported formats stay as icons.

Drag a saved file from **Files** to another shot to move and rename it. The panel also provides file drag for Chrome upload areas when a file has been prepared, and a Chrome `DownloadURL` drag for operating-system drop targets. Hover over a row briefly before dragging into a website. Files larger than 128 MB are not prepared in browser memory. Desktop applications may not accept Chrome's virtual-file drag; use the folder button beside a file to show the real file in Explorer.

For a video already saved in a shot, open its **⋯** menu and choose **Convert to PNG**. The local FFmpeg decodes every video frame into a separately numbered, losslessly encoded PNG in a folder next to the video. Conversion runs in the background and can be cancelled from the same menu. The output folder follows the existing desktop AIRenamer naming convention. PNG encoding does not restore detail lost in a compressed source video.

Drag the separators below **Shots** and **Files** to resize the panes. **Settings** controls light/dark/system theme and ignored folder names. `_shotcode` is always ignored. Downloads continue through Chrome normally; site-specific interception is not used.

## Verification

- `node --test browser/background.test.cjs browser/live.test.cjs`
- `env\Scripts\python.exe -m pytest tests/test_browser_update.py tests/test_browser_native.py tests/test_config.py -q -p no:cacheprovider --basetemp .test_runs/pytest-browser`
