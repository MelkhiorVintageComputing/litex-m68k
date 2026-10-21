#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# L8 (doc/l8-buildroot.md): build buildroot's litex_rd68031_fpu_defconfig with
# this br2-external tree.
#
#   buildroot/build.sh [OUT]       (default ~/LITEX-68K/build/buildroot-fpu)
#   buildroot/build.sh OUT <make target>...    e.g. linux-rebuild, menuconfig
#
# Buildroot 2026.08 comes from linux/fetch.sh's download directory (DL,
# checked against linux/MANIFEST) and is unpacked to SRC/buildroot-2026.08
# (default ~/LITEX-68K/src), a disposable tree that is never changed. Buildroot's
# own downloads go to DL/buildroot. The images, in OUT/images, are a --linux-dir
# for sim/litex/l7_test.py. A heavy job (CLAUDE.md): run it alone; each package
# builds with JOBS (4) at most.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/.." && pwd)
DL=${DL:-$HOME/LITEX-68K/dl}
SRC=${SRC:-$HOME/LITEX-68K/src}
JOBS=${JOBS:-4}
OUT=${1:-$HOME/LITEX-68K/build/buildroot-fpu}
[ $# -gt 0 ] && shift
BR=$SRC/buildroot-2026.08

"$TOP/linux/fetch.sh" "$DL"
[ -d "$BR" ] || { mkdir -p "$SRC"; tar -C "$SRC" -xf "$DL/buildroot-2026.08.tar.xz"; }
# The kernel tarball fetch.sh checked, where buildroot looks for it first.
mkdir -p "$DL/buildroot/linux"
[ -f "$DL/buildroot/linux/linux-7.2.9.tar.xz" ] || cp "$DL/linux-7.2.9.tar.xz" "$DL/buildroot/linux/"

mkdir -p "$OUT"
M="make -C $BR O=$OUT BR2_EXTERNAL=$HERE BR2_DL_DIR=$DL/buildroot BR2_JLEVEL=$JOBS"
if [ ! -f "$OUT/.config" ] || [ "$HERE/configs/litex_rd68031_fpu_defconfig" -nt "$OUT/.config" ]; then
	$M litex_rd68031_fpu_defconfig
fi
$M "$@"
