#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Fetch the sources in linux/MANIFEST into a download directory (default
# ~/LITEX-68K/dl, outside this repository and ~/LITEX), and check each against
# its sha256. Files already there and right are not fetched again.
#
# Usage: linux/fetch.sh [download directory]
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DL=${1:-$HOME/LITEX-68K/dl}
mkdir -p "$DL"

grep -v '^#' "$HERE/MANIFEST" | while read -r sum file url; do
    [ -n "$sum" ] || continue
    if [ -f "$DL/$file" ] && echo "$sum  $DL/$file" | sha256sum -c --quiet - 2>/dev/null; then
        echo "$file: present"
        continue
    fi
    echo "$file: fetching $url"
    curl -fL --retry 3 -o "$DL/$file.part" "$url"
    echo "$sum  $DL/$file.part" | sha256sum -c --quiet - || {
        echo "$file: wrong sha256" >&2; rm -f "$DL/$file.part"; exit 1; }
    mv "$DL/$file.part" "$DL/$file"
done
