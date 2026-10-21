#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Host tests of patches/gcc 0003, the m68k extended precision routines
# (patches/gcc/README.md): the libgcc files of gcc 15.3.0's tarball with the
# series applied, built for the host with a 12-byte struct as XFtype.
#   x87.c     against x87 long double, where the two formats agree
#   dump.c + check.py   against an exact rational reference, m68k's own
#             region (exponent 0, denormals, unnormals) weighted
#
#   patches/gcc/test/run.sh [x87 cases (20000000)] [exact cases (200000)]
#
# The tarball is buildroot's download (DL, default ~/LITEX-68K/dl/buildroot/gcc).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
TAR=${DL:-$HOME/LITEX-68K/dl/buildroot/gcc}/gcc-15.3.0.tar.xz
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
tar -C "$W" -xJf "$TAR" --strip-components=1 gcc-15.3.0/libgcc gcc-15.3.0/include \
	gcc-15.3.0/gcc/config.gcc gcc-15.3.0/gcc/config/m68k
for p in "$HERE"/../*.patch; do patch -s -d "$W" -p1 < "$p"; done
G=$W/libgcc
CFLAGS="-O2 -w -include $HERE/inc/prelude.h -I $HERE/inc -I $G -I $G/config/m68k -I $W/include"
gcc $CFLAGS -o "$W/x87" "$HERE/x87.c" "$G"/config/m68k/*xf*.c -lm
gcc $CFLAGS -o "$W/dump" "$HERE/dump.c" "$G"/config/m68k/*xf*.c
"$W/x87" "${1:-20000000}"
"$W/dump" "${2:-200000}" | python3 "$HERE/check.py"
