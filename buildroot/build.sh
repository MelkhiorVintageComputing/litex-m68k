#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Build one of this br2-external tree's defconfigs with buildroot:
#   fpu     litex_rd68031_fpu_defconfig, RD68031 + RD68884, glibc, hard float
#           (L8, doc/l8-buildroot.md)
#   nofpu   litex_rd68031_nofpu_defconfig, RD68031 alone, musl, soft float
#           (L9, doc/l9-nofpu.md)
#
#   buildroot/build.sh [fpu|nofpu] [make target]...   e.g. nofpu linux-rebuild
#
# Buildroot 2026.08 comes from linux/fetch.sh's download directory (DL,
# checked against linux/MANIFEST) and is unpacked to SRC/buildroot-2026.08
# (default ~/LITEX-68K/src), a disposable tree that is never changed. Buildroot's
# own downloads go to DL/buildroot. The output is OUT (default
# ~/LITEX-68K/build/buildroot-<variant>); its images directory is a
# --linux-dir for sim/litex/l7_test.py. A heavy job (CLAUDE.md): run it alone;
# each package builds with JOBS (4) at most.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/.." && pwd)
DL=${DL:-$HOME/LITEX-68K/dl}
SRC=${SRC:-$HOME/LITEX-68K/src}
JOBS=${JOBS:-4}
VARIANT=${1:-fpu}
[ $# -gt 0 ] && shift
case $VARIANT in
fpu|nofpu) ;;
*) echo "usage: $0 [fpu|nofpu] [make target]..." >&2; exit 1 ;;
esac
DEFCONFIG=litex_rd68031_${VARIANT}_defconfig
OUT=${OUT:-$HOME/LITEX-68K/build/buildroot-$VARIANT}
BR=$SRC/buildroot-2026.08

"$TOP/linux/fetch.sh" "$DL"
[ -d "$BR" ] || { mkdir -p "$SRC"; tar -C "$SRC" -xf "$DL/buildroot-2026.08.tar.xz"; }
# The kernel tarball fetch.sh checked, where buildroot looks for it first.
mkdir -p "$DL/buildroot/linux"
[ -f "$DL/buildroot/linux/linux-7.2.9.tar.xz" ] || cp "$DL/linux-7.2.9.tar.xz" "$DL/buildroot/linux/"

mkdir -p "$OUT"
M="make -C $BR O=$OUT BR2_EXTERNAL=$HERE BR2_DL_DIR=$DL/buildroot BR2_JLEVEL=$JOBS"
if [ ! -f "$OUT/.config" ] || [ "$HERE/configs/$DEFCONFIG" -nt "$OUT/.config" ]; then
	$M $DEFCONFIG
fi
$M "$@"
