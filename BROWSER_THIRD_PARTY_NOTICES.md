# AIRenamer Browser third-party notices

The AIRenamer Browser native host is built with CPython 3.14.5, Pillow 12.2.0,
and PyInstaller 6.20.0. Their license texts are included in
`third_party_licenses/` and in the native executable. The Browser build excludes
the desktop application's Qt/PyQt dependencies.

The experimental Windows drag helper uses the operating system's installed
.NET Framework / Windows Forms runtime, which is not redistributed here.

FFmpeg is not included in the AIRenamer Browser package. On setup, the program
uses an existing local FFmpeg or downloads the GPL Windows build directly from
the [BtbN FFmpeg Builds](https://github.com/BtbN/FFmpeg-Builds) release page,
verifies its publisher-provided SHA-256 digest, and caches it locally. See the
BtbN release for the exact build's license, source and provenance.

The AIRenamer source code is licensed under GPL-3.0-only. See `LICENSE` and
`BROWSER_SOURCE.md`.
