# AIRenamer Browser source

The exact AIRenamer Browser source for each published package is available
from the matching `v<version>` tag of
`https://github.com/RomanPronDev/AIRenamer-Browser`. The release archive also
includes the application source, build specification, installer scripts, tests,
dependency versions and license texts. The native executable is built with
PyInstaller from `MediaRenamerBrowserNative.spec`.

The experimental direct-drag helper is built from `native_drag/DragBridge.cs`
by `native_drag/Build-DragBridge.ps1` and included inside the native companion.
It uses the existing Windows .NET Framework runtime. Its source and verification
notes are included in the package; it does not add a separate desktop panel.

CPython, Pillow and PyInstaller source releases and their license texts are
available from their upstream projects. Their exact versions are recorded in
`BROWSER_THIRD_PARTY_NOTICES.md` and `browser-requirements.txt`. FFmpeg is not
distributed in the AIRenamer Browser package; it is downloaded directly from
its publisher on first setup when needed.
