#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Make the patched copy of a LiteX repository: ~/LITEX-68K/<repo>
# (disposable), a clone of ~/LITEX/<repo> at the commit checked out there,
# with patches/<repo>/*.patch applied (git am). scripts/setup_venv.sh then
# puts the copy on the venv's path in place of ~/LITEX/<repo>; run it after.
#
# Usage: scripts/patch_litex.sh [repo]   (default litex)
# An existing copy is checked, not remade: it must be that commit plus
# exactly these patches, with no local change. Remove it to start again.
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=${1:-litex}
SRC=${LITEX:-$HOME/LITEX}/$REPO
DST=${LITEX68K:-$HOME/LITEX-68K}/$REPO
BASE=$(git -C "$SRC" rev-parse HEAD)
PATCHES=$(ls "$HERE"/patches/"$REPO"/*.patch)
N=$(echo "$PATCHES" | wc -l)

if [ ! -d "$DST" ]; then
    git clone -q "$SRC" "$DST"
    git -C "$DST" checkout -q -B litex-68k "$BASE"
    git -C "$DST" -c user.name=litex-68k -c user.email=litex-68k@localhost \
        am -q --committer-date-is-author-date $PATCHES
fi

# Checked: the base, the patches by subject, a clean tree.
[ "$(git -C "$DST" rev-parse "HEAD~$N")" = "$BASE" ] \
    || { echo "$DST is not $SRC ($BASE) plus the patches" >&2; exit 1; }
WANT=$(for p in $PATCHES; do sed -n 's/^Subject: \[PATCH[^]]*\] //p' "$p"; done)
HAVE=$(git -C "$DST" log --reverse --format=%s "HEAD~$N..HEAD")
[ "$(echo "$WANT" | cut -c1-40)" = "$(echo "$HAVE" | cut -c1-40)" ] \
    || { echo "$DST has other patches than patches/$REPO" >&2; exit 1; }
[ -z "$(git -C "$DST" status --porcelain --untracked-files=no)" ] \
    || { echo "$DST has local changes" >&2; exit 1; }
echo "$DST: $REPO $(git -C "$SRC" rev-parse --short HEAD) + $N patch(es)"
