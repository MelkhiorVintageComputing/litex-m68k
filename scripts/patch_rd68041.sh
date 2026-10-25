#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Make the patched RD68041 that sync_pythondata_rd68041.sh takes the RTL from:
# ~/LITEX-68K/RD68041 (disposable), a clone of the commit checked out under
# Inputs/RD68041 with patches/RD68041/*.patch applied (git am). Its generated
# files (rtl/gen/) come from tools/ucode/frames.py, which no patch changes; its
# own `make ucode-check` says so.
#
# Usage: scripts/patch_rd68041.sh [destination]   (default ~/LITEX-68K/RD68041)
# An existing destination is checked, not remade: it must be that commit plus
# exactly these patches, with no local changes. Remove it to start again.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
SRC=$HERE/Inputs/RD68041
DST=${1:-$HOME/LITEX-68K/RD68041}
BASE=$(git -C "$SRC" rev-parse HEAD)
PATCHES=$(ls "$HERE"/patches/RD68041/*.patch)
N=$(echo "$PATCHES" | wc -l)

if [ ! -d "$DST" ]; then
    git clone -q "$SRC" "$DST"
    git -C "$DST" checkout -q -B litex-68k "$BASE"
    git -C "$DST" -c user.name=litex-68k -c user.email=litex-68k@localhost \
        am -q $PATCHES
fi

# Checked: the base, then the patches by subject; clean.
[ "$(git -C "$DST" rev-parse "HEAD~$N")" = "$BASE" ] \
    || { echo "$DST is not Inputs/RD68041 ($BASE) plus the patches" >&2; exit 1; }
WANT=$(for p in $PATCHES; do sed -n 's/^Subject: \[PATCH[^]]*\] //p' "$p"; done)
HAVE=$(git -C "$DST" log --reverse --format=%s "HEAD~$N..HEAD")
[ "$(echo "$WANT" | cut -c1-40)" = "$(echo "$HAVE" | cut -c1-40)" ] \
    || { echo "$DST has other patches than patches/RD68041" >&2; exit 1; }
[ -z "$(git -C "$DST" status --porcelain --untracked-files=no)" ] \
    || { echo "$DST has local changes" >&2; exit 1; }
make -C "$DST" -s ucode-check
echo "$DST: RD68041 $(git -C "$SRC" describe --always --tags) + $N patch(es)"
