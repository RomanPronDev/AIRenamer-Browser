#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$source_dir"
finish() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        printf '\nBuild stopped. Review the message above and build-macos.log, if created.\n'
    fi
    printf '\nPress Return to close this window.\n'
    read -r _ || true
    exit "$result"
}
trap finish EXIT

if [ "$(uname -s)" != Darwin ]; then
    printf 'This build must run on a Mac. Copy the extracted source folder to that Mac.\n'
    exit 1
fi
if [ "$(uname -m)" != arm64 ]; then
    printf 'Apple Silicon running natively is required. Open Terminal without Rosetta.\n'
    exit 1
fi
macos_version="$(/usr/bin/sw_vers -productVersion)"
if [ "${macos_version%%.*}" -lt 14 ]; then
    printf 'macOS 14 or newer is required.\n'
    exit 1
fi
if ! /usr/bin/xcrun --find swiftc >/dev/null 2>&1; then
    printf 'Install Apple Command Line Tools first: open Terminal, run xcode-select --install,\n'
    printf 'complete the Apple installer, then reopen Build-Browser.command.\n'
    exit 1
fi

build_python=""
for candidate in "$(command -v python3.14 || true)" /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c \
        'import platform, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and platform.machine() == "arm64" else 1)' >/dev/null 2>&1; then
        build_python="$candidate"
        break
    fi
done
if [ -z "$build_python" ]; then
    printf 'Install Python 3.14 for macOS from https://www.python.org/downloads/macos/\n'
    printf 'Then reopen Build-Browser.command. The port was checked with Python 3.14.5.\n'
    exit 1
fi

exec > >(/usr/bin/tee "$source_dir/build-macos.log") 2>&1
printf 'Building AIRenamer Browser locally. No source is uploaded.\n'
printf 'Dependencies are downloaded from the Python package index.\n'
build_environment="$source_dir/.venv-browser-macos"
"$build_python" -m venv "$build_environment"
"$build_environment/bin/python" -c \
    'import platform, sys; assert sys.version_info[:2] == (3, 14) and platform.machine() == "arm64", "Build environment must use native arm64 Python 3.14"'
"$build_environment/bin/python" -m pip install -r "$source_dir/browser-requirements.txt"
export MACOSX_DEPLOYMENT_TARGET=14.0
"$build_environment/bin/python" "$source_dir/browser_release.py"
version="$("$build_environment/bin/python" -c 'import json; print(json.load(open("version.json"))["version"])')"
package="$source_dir/dist/AIRenamer-Browser-$version-macos-arm64.zip"
printf '\nPackage created: %s\n' "$package"
printf 'Extract this ZIP into a separate folder and open Setup-Browser.command there.\n'
printf 'Application dragging still needs your physical Mac test.\n'
/usr/bin/open -R "$package" || true
