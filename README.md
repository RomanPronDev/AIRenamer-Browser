# AIRenamer Browser — 0.30.0

AIRenamer is a standalone Chrome side panel with its own project list, folder structure, naming rules, exclusions, and move operations. All panel labels are in English. Chrome starts the local Native Messaging companion when needed; the desktop application does not have to stay open.

**Windows 0.30.0:** `AIRenamer-Browser-0.30.0.zip` is the installable Windows package;
it contains `Setup-Browser.cmd`, `Update-Browser.cmd` and the native `.exe`.
**Mac build source:** `AIRenamer-Browser-0.30.0-source.zip` contains
`Build-Browser.command` and the corrected framework packaging, for rebuilding
on an Apple Silicon Mac. The source ZIP is not a Windows or Mac binary installer.

The release also includes Apple Silicon / macOS 14+ source and build instructions.
See [macOS installation and acceptance checks](browser/MACOS.md). The published stable
Windows version is 0.30.0. macOS
single-gesture dragging requires physical Chrome/Adobe/Nuke acceptance.

## Install and update on Windows

1. Extract the archive and run `Setup-Browser.cmd`.
2. On first install, enable **Developer mode** in `chrome://extensions`, select **Load unpacked**, and choose `%LOCALAPPDATA%\MediaRenamer\Browser\Extension`.
3. Pin AIRenamer in Chrome. Click its icon or press `Ctrl+Shift+Y` to open the side panel.
4. After this one-time installation, Chrome checks for a newer AIRenamer Browser GitHub release about every six hours while it is running. The local companion downloads a newer package, checks its SHA-256 and version, and installs it to the same folder. The extension reloads when Chrome is idle. Opening Chrome also checks for an already installed update. If needed, `Update-Browser.cmd` remains a manual fallback.

Chrome needs **Load unpacked** only for the first installation on each machine. An unpacked extension cannot install itself into Chrome silently; afterward, AIRenamer updates its already loaded folder automatically. Keep that folder in place. The updater reads only stable, published releases from `RomanPronDev/AIRenamer-Browser`.

Setup reuses a working `%LOCALAPPDATA%\MediaRenamer\Tools\ffmpeg.exe`. If it is missing, setup starts a background download of the GPL Windows build from BtbN, verifies its published SHA-256, and saves it in that folder for later conversions. Conversion also waits for or retries that setup if needed. FFmpeg is not included in the Browser archive.

If Windows declines launching optional FFmpeg preparation during installation, setup now finishes and records `Browser\setup-ffmpeg-launch.log`. Conversion retries preparation on first use. If the panel also cannot connect, the native executable itself may be blocked; its launch policy must be checked on that workstation. AIRenamer does not change Windows security settings. Failure to open the Chrome setup page automatically also leaves installation complete; open `chrome://extensions` manually.

## In the panel

**0.30.0:** Windows FFmpeg setup reuses a working tool in the local Tools folder or PATH. If neither is available, it downloads the official BtbN archive, verifies the published SHA-256, validates the executable and installs it atomically. GitHub API failures have an official checksum-download fallback. Transient network/rate-limit errors and a release changing during download receive up to three bounded attempts; a failed attempt no longer disables retry for the whole Browser session. Chrome startup prepares missing tools in the background. **Settings → Video tools** now shows progress, the effective local tool path or a useful error and **Prepare / retry video tools** on Windows as well as macOS. FFmpeg stays outside the release ZIP. Corporate network or executable policy restrictions still need resolution on that workstation; the program never disables certificate/security checks.

Successfully saved downloads are highlighted **yellow** in Recent Downloads, with the destination in the tooltip. The record survives closing the panel, restarting Chrome and updating the extension. To shot records the exact Chrome download ID, source path and timestamp after successful import; failed imports are not highlighted, and a reused ID/overwritten path is not mistaken for an earlier file. Historical imports made before this version have no reliable source/destination record and cannot be retroactively highlighted. Saving is allowed again to another shot; the latest destination is shown. Source Downloads files retain the existing copy/import behavior.

**0.29.0:** Reopening restores the last project, shot, file list and clicked thumbnails from the running Chrome session. The companion stays available in the background. Metadata reads are reused for 30 seconds; mutations invalidate them, and Refresh always reads again. Chrome restart, companion disconnect and extension update reset this lightweight cache. Original media files are never retained in memory for this feature.

The **Recent** row above Shots returns to the last 12 used destinations, including their project and optional sequence; scroll the single row horizontally if needed. **Files** has an inline **Copy file path** icon on every row and a copy icon in its heading for one selected or newly moved file. Click a file's thumbnail to load its small preview; clicking the filename opens that same preview. Video rows use a blue background and image rows a warm background, in both themes. Moving a file opens its destination shot and centers/highlights the renamed file there. Pane dividers share the available height and reserve room for Recent Downloads.

Recent Downloads still includes the entire Chrome history. It renders 100 rows initially and adds more as you scroll (or use **Show more downloads**), so large histories do not create thousands of elements on reopening. Clicked thumbnails are bounded to 24 / about 1 MiB in panel state; shared metadata/preview reads are bounded to 32 / about 4 MiB. Videos are streamed only after a click.

Select a project from the picker at the top. Projects with sequences show a second picker; projects whose first-level folders are shots show those shots directly. AIRenamer checks visible folders for shot work such as `genai`, even when the sequence is named with a number; ignored folder names and paths are skipped. Project, sequence, and shot selection persist when switching Chrome tabs. Click a shot to list its files. Drop downloaded media onto a shot or use **To shot** in Recent Downloads to import and rename it.

**Project folders** are managed in Settings. Use **Browse…** to choose a root folder in Windows, or paste its path from Explorer, then confirm it in the panel. You can change a project's folder or remove its shortcut without deleting files. The Browser has its own per-machine project list and detected layouts, stored under `%LOCALAPPDATA%\MediaRenamer\Browser\State`. Its naming and extension settings are under `Browser\Config`, and its default/per-project folder structures remain in `Browser\structure.json`. Changes in either product do not update the other. Existing older Browser installations receive one snapshot of formerly shared shortcuts and exclusions so their paths are preserved; desktop folder and naming settings are never imported. Fresh installations start with an empty project list. If that list is empty when the Browser first opens, it shows a welcome dialog with the same Browse button and path field. Existing project paths remain untouched during installation and updates, including paths to temporarily unavailable drives.

The independent Browser media defaults are `Project/vfx/shots/[Sequence]/Shot/genai/KEYFRAMES` for images and `.../genai/VIDEO` for videos. Importing or moving a file creates both media folders. Custom Browser structure overrides remain in place. To correct a previously saved media layout, open **Settings → Structure** (or **Edit default folders**), select **Use genai / KEYFRAMES and VIDEO**, then **Save structure**. Existing files are not automatically relocated.

In **0.28.0**, use **+ next to Find shot → New shot** to enter a shot name and choose **Create shot** (or press Enter). AIRenamer creates the shot inside the selected project's shots tree and, if applicable, its selected sequence, creates the configured image/video folders, then opens the new shot. The dialog shows the destination root and actual folder names. Choose a sequence first in projects with sequences. Projects with direct shots need no sequence. Missing intermediate shots/media directories are created within the registered project; a missing project root is never recreated.

To prepare an existing shot, select it and click the **folder icon in the Files header → Create media folders**. This creates `genai/KEYFRAMES` and `genai/VIDEO` by default, or the media paths/names configured in **Settings → Edit default folders** and the project's **Structure** override. Repeating this action preserves existing files. Duplicate shot names, unsafe names, unavailable folders and excluded branches produce a visible error. These actions only create directories; they do not rename or relocate existing files. Additional media categories still create their folders through the existing import workflow.

**Settings → Names & versions** provides the Browser's own filename template, image/video type suffixes, a global **Enable subversions** option and live filename examples. Tokens are `{sequence}` (or its `{scene}` alias), `{shot}`, `{type}`, `{version}`, `{subversion}`, and `{format}`. Version numbers use at least three digits, subversions at least two; the real file extension is appended automatically. Keep `{version}` in the template and include `{subversion}` when subversions are enabled. Separate these two numeric tokens with text/punctuation. Empty sequence and format tokens are cleaned without leftover separators. Custom template names and legacy `_v001_00` names can both be recognized. Editing the template does not rename existing files.

For the next main version, import normally. In Files, select **Create subversion**, or hold Shift while dropping a new file into a shot, to extend the highest version in its category. To extend a specific older version, drop the new file onto that saved file's row; both files must have the same media type/category. **Add aspect ratio** fills `{format}` with a value such as `_16x09`. If subversions are disabled globally, every import creates a main version. These options apply to Chrome's **To shot**, files selected with **+**, and file drops/transfers.

**Settings → File types & categories** edits supported image/video extensions and additional category IDs, folders, media types and type suffixes. Each category has a **+** action and accepts drops in its section, including when empty. Category folders must be distinct, including per-project folder overrides. Removing a category removes its configuration and view; its files are preserved. Settings saves naming rules and ignored names atomically and preserves project shortcuts and folder structures. Invalid templates/settings stay in the dialog with an explanation. None of these preferences are loaded from or written to desktop settings.

**Recent Downloads** lists the complete Chrome download history, including non-media files. Previews never load automatically: click the thumbnail icon to load a small preview of one file, or click its name to load and open its preview. Only that selected file is requested; its preview is reused if you click it again. Unsupported formats stay as icons.

Drag a saved file's row, name, or media icon from **Files** directly into another application. **Native drag** is the default and is prepared automatically at Chrome/extension startup and when opening the panel. No Settings opt-in or Chrome folder connection is required. Old opt-in preferences migrate once to this default; choosing **Chrome folder connection** afterward is remembered. Only the original local/network path is handed to Windows; the media is not fetched into memory or copied for dragging. The receiving application receives Copy, so the source file remains in place.

Use the small **move handle** beside the file, or hold Shift when starting a row drag, to move and rename the file into another shot. The **⋯ → Drag into desktop app** fallback still opens a small native drag handle. **Open drag test target** in Settings reports the actual received path. The user has confirmed receiving the correct server path in this probe; actual Photoshop/After Effects/Nuke acceptance still needs verification in those programs. If a drop is cancelled or rejected, the panel now displays that result. Completion polling runs only during a drag and stops after it completes.

**Chrome folder connection** remains available in Settings as an alternative. Drop the indicated project root from Explorer to grant browser access, hover the file briefly, then drag it. This retains the browser-granted snapshot without fetching the media into memory. Reconnect after closing the panel. A pasted path or native Browse selection cannot silently create this browser grant. See `native_drag/README.md` for the implementation and acceptance checks.

Opening the panel displays **Loading** with three animated dots while the companion responds. Once the projects arrive, the indicator disappears. A connection failure ends Loading and displays a reconnect action rather than leaving an endless animation.

For a video already saved in a shot, open its **⋯** menu and choose **Convert to PNG**. The local FFmpeg decodes every video frame into a separately numbered, losslessly encoded PNG in a folder next to the video. Conversion runs in the background and can be cancelled from the same menu. The output folder follows the existing desktop AIRenamer naming convention. PNG encoding does not restore detail lost in a compressed source video.

Drag the separators below **Shots** and **Files** to resize the panes. **Settings** controls light/dark/system theme and ignored folder names. `_shotcode` is always ignored. Downloads continue through Chrome normally; site-specific interception is not used.

## Verification

- `node --test browser/background.test.cjs browser/live.test.cjs browser/local-files.test.cjs browser/panel-model.test.cjs`
- `env\Scripts\python.exe -m pytest tests/test_browser_update.py tests/test_browser_native.py tests/test_browser_drag.py tests/test_browser_settings.py tests/test_config.py -q -p no:cacheprovider --basetemp .test_runs/pytest-browser`
