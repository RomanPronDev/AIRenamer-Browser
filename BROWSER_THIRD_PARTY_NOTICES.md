# AIRenamer Browser third-party notices

The AIRenamer Browser native host is built with CPython 3.14.5, Pillow 12.2.0,
and PyInstaller 6.20.0. Their license texts are included in
`third_party_licenses/` and in the native executable. The Browser build excludes
the desktop application's Qt/PyQt dependencies.

The experimental Windows drag helper uses the operating system's installed
.NET Framework / Windows Forms runtime, which is not redistributed here.

The macOS helper is compiled from Swift source and uses the installed macOS
AppKit and Foundation frameworks. These system frameworks are not redistributed
inside the Browser package. The Swift toolchain is used only for building.

FFmpeg is not included in the AIRenamer Browser package. On setup, the program
uses an existing local FFmpeg or downloads the GPL Windows build directly from
the [BtbN FFmpeg Builds](https://github.com/BtbN/FFmpeg-Builds) release page,
verifies its publisher-provided SHA-256 digest, and caches it locally. See the
BtbN release for the exact build's license, source and provenance.

On macOS, the program downloads FFmpeg 9.0.2 for arm64 directly from
[Martin Riedl's build server](https://ffmpeg.martin-riedl.de/), using the pinned
archive URL and SHA-256 in `browser_ffmpeg.py`. Source/build information is
provided by [the publisher's build scripts](https://git.martin-riedl.de/ffmpeg/build-script)
and [FFmpeg](https://ffmpeg.org/). The installation receipt records the selected
archive and binary hashes. FFmpeg is downloaded separately, not redistributed
in the AIRenamer Browser ZIP.

The AIRenamer source code is licensed under GPL-3.0-only. See `LICENSE` and
`BROWSER_SOURCE.md`.
