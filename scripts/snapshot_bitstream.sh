#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Keep a board build where later builds do not overwrite it: copies a
# targets/qmtech_wukong.py output directory's bitstream, CSR maps and built
# software (headers and libraries, what litexboot and other bare-metal or
# kernel code build against) to ~/LITEX-68K/bitstreams/NAME, and points its
# variables.mak at the copy. scripts/board.py uses these (doc/board.md).
#
# Usage: scripts/snapshot_bitstream.sh NAME BUILD_DIR
#   e.g. scripts/snapshot_bitstream.sh wukong-fpu-eth-sd build/qmtech_wukong_30m_fpu_1m_io
set -eu

NAME=$1
SRC=$(cd "$2" && pwd)
DST=${BITSTREAMS:-$HOME/LITEX-68K/bitstreams}/$NAME
HERE=$(cd "$(dirname "$0")/.." && pwd)

[ -f "$SRC/gateware/qmtech_wukong.bit" ] || { echo "$SRC: no gateware/qmtech_wukong.bit" >&2; exit 1; }
rm -rf "$DST"
mkdir -p "$DST/gateware"
cp "$SRC/gateware/qmtech_wukong.bit" "$SRC/gateware/qmtech_wukong_timing.rpt" "$DST/gateware/"
cp "$SRC/csr.csv" "$SRC/csr.json" "$DST/"
cp -r "$SRC/software" "$DST/software"
sed -i "s|^BUILDINC_DIRECTORY=.*|BUILDINC_DIRECTORY=$DST/software/include|" \
    "$DST/software/include/generated/variables.mak"

CPU=$(sed -n 's/^constant,config_cpu_name,\(.*\),,$/\1/p' "$SRC/csr.csv")
{
	echo "source:              $SRC"
	echo "copied:              $(date -Iseconds)"
	echo "litex-68k (at copy): $(git -C "$HERE" describe --always --dirty)"
	echo "litex (copy):        $(git -C "$HOME/LITEX-68K/litex" describe --always --dirty 2>/dev/null || echo '~/LITEX/litex')"
	echo "cpu:                 $CPU"
	echo "pythondata:          $(git -C "$HERE/../pythondata-cpu-$CPU" describe --always --dirty)"
	echo "identifier:          $(sed -n 's/^constant,config_identifier,\(.*\),,$/\1/p' "$SRC/csr.csv")"
	echo "timing:              WNS $(grep -A6 'Design Timing Summary' "$SRC/gateware/qmtech_wukong_timing.rpt" | tail -1 | awk '{print $1}') ns"
} > "$DST/SNAPSHOT"
cat "$DST/SNAPSHOT"
echo "$DST: $(du -sh "$DST" | cut -f1)"
