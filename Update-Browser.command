#!/bin/bash
set -eu
package_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
"$package_dir/dist/MediaRenamerBrowserNative/MediaRenamerBrowserNative" --auto-update
printf '\nUpdate check finished. Reopen the Chrome panel. Press Return to close.\n'
read -r _
