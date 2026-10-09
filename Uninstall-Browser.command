#!/bin/bash
set -eu
package_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
"$package_dir/dist/MediaRenamerBrowserNative/MediaRenamerBrowserNative" --install-browser --uninstall
printf '\nPress Return to close this window.\n'
read -r _
