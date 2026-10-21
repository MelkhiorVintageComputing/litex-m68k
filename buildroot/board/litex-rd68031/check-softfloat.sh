#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# The FPU-less defconfig's post-build script (doc/l9-nofpu.md): fail if any
# ELF file in the target has an FPU instruction, or asks for a dynamic linker
# the target does not have. The kernel's FPU emulator (linux-nofpu.config)
# would run a hard-float program silently, so the toolchain's soft-float
# default is checked here, on what it built; and musl's soft-float dynamic
# linker has a name of its own (ld-musl-m68k-sf.so.1), which gcc must use.
#
#   check-softfloat.sh TARGET_DIR      (HOST_DIR from buildroot's environment)
set -e
TARGET=$1
OBJDUMP=$(ls "$HOST_DIR"/bin/*-linux-*-objdump | head -1)
READELF=$(ls "$HOST_DIR"/bin/*-linux-*-readelf | head -1)
bad=0
n=0
for f in $(find "$TARGET" -type f); do
	head -c 4 "$f" | grep -q 'ELF' || continue
	n=$((n + 1))
	# objdump's lines are address, words, instruction, tab-separated; every
	# m68k FPU mnemonic starts with f (fmove, fadd, fsave, fbeq, ...).
	c=$("$OBJDUMP" -d "$f" | awk -F'\t' '$3 ~ /^f[a-z]/' | wc -l)
	if [ "$c" -ne 0 ]; then
		echo "check-softfloat: ${f#$TARGET}: $c FPU instructions" >&2
		bad=1
	fi
	interp=$("$READELF" -l "$f" | sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')
	if [ -n "$interp" ] && [ ! -e "$TARGET$interp" ]; then
		echo "check-softfloat: ${f#$TARGET}: no $interp in the target" >&2
		bad=1
	fi
done
[ "$bad" -eq 0 ] || exit 1
echo "check-softfloat: $n ELF files, no FPU instruction, their dynamic linkers there"
