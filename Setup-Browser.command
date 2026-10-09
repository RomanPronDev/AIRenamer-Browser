#!/bin/bash
set -eu
package_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
host_dir="$package_dir/dist/MediaRenamerBrowserNative"
printf 'Checking AIRenamer runtime signatures…\n'
/usr/bin/codesign --verify --deep --strict --verbose=2 "$host_dir/_internal/Python.framework"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$host_dir/BrowserDragBridge.app"
/usr/bin/codesign --verify --strict --verbose=2 "$host_dir/MediaRenamerBrowserNative"
"$package_dir/dist/MediaRenamerBrowserNative/MediaRenamerBrowserNative" --install-browser --package-folder "$package_dir"
printf '\nPress Return to close this setup window.\n'
read -r _
