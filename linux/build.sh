#!/bin/sh
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Adapted from RD68031's sim/virt/linux/build.sh
# (https://github.com/MelkhiorVintageComputing/RD68031).
#
# Build a Linux for RD68031 in a LiteX SoC (L7, doc/l7-linux.md): Linux 7.2.9
# with patches/linux (the m68k LiteX machine), litex-rd68031.config over
# allnoconfig; a static BusyBox 1.38.0 for the MC68030; the initramfs that
# cpio.list describes, packed by the kernel's own gen_init_cpio with every
# timestamp at 0.
#
#   linux/build.sh [OUT]          (default build/linux)
#
# Sources: linux/fetch.sh's download directory (DL, default ~/LITEX-68K/dl).
# The patched kernel tree is SRC/linux-7.2.9 (default ~/LITEX-68K/src): made
# from the tarball and patches/linux if it is not there, used as it is if it
# is (a disposable tree, where the patches are developed: see
# patches/linux/README.md). Incremental: make does the rest. The kernel build is
# a heavy job (CLAUDE.md): run it alone, JOBS (4) at most.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/.." && pwd)
OUT=${1:-$TOP/build/linux}
DL=${DL:-$HOME/LITEX-68K/dl}
SRC=${SRC:-$HOME/LITEX-68K/src}
JOBS=${JOBS:-4}
CROSS=m68k-linux-gnu-
mkdir -p "$OUT" "$SRC"
OUT=$(cd "$OUT" && pwd)

# 1. sources
"$HERE/fetch.sh" "$DL"
if [ ! -d "$SRC/linux-7.2.9" ]; then
	tar -C "$SRC" -xf "$DL/linux-7.2.9.tar.xz"
	for p in "$TOP"/patches/linux/*.patch; do
		patch -d "$SRC/linux-7.2.9" -p1 --quiet < "$p"
	done
fi
[ -d "$SRC/busybox-1.38.0" ] || tar -C "$SRC" -xf "$DL/busybox-1.38.0.tar.bz2"

# 2. the kernel, with the build's identity fixed so that it is reproducible
export KBUILD_BUILD_TIMESTAMP="2026-01-01 00:00:00 UTC" KBUILD_BUILD_USER=litex68k \
	KBUILD_BUILD_HOST=litex68k KBUILD_BUILD_VERSION=1
# LOCALVERSION= (set, empty): no "+" for a tree that is not at a release tag.
K="make -s -C $SRC/linux-7.2.9 O=$OUT/linux-build ARCH=m68k CROSS_COMPILE=$CROSS LOCALVERSION="
mkdir -p "$OUT/linux-build"
if [ ! -f "$OUT/linux-build/.config" ] || [ "$HERE/litex-rd68031.config" -nt "$OUT/linux-build/.config" ]; then
	KCONFIG_ALLCONFIG="$HERE/litex-rd68031.config" $K allnoconfig > "$OUT/kernel-config.log" 2>&1
fi
$K -j"$JOBS" vmlinux > "$OUT/kernel-build.log" 2>&1 \
	|| { tail -30 "$OUT/kernel-build.log"; exit 1; }
cp "$OUT/linux-build/vmlinux" "$OUT/vmlinux"

# 3. BusyBox, defconfig, static, for the MC68030 (with its FPU: the
#    cross-compiler's default) -- without tc, which does not build against the
#    host's kernel headers (as in RD68031)
B="make -s -C $SRC/busybox-1.38.0 O=$OUT/busybox-build"
if [ ! -f "$OUT/busybox-build/busybox" ]; then
	mkdir -p "$OUT/busybox-build"
	$B defconfig > "$OUT/busybox-build.log" 2>&1
	sed -i -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' \
	    -e 's/^CONFIG_CROSS_COMPILER_PREFIX=.*/CONFIG_CROSS_COMPILER_PREFIX="'$CROSS'"/' \
	    -e 's/^CONFIG_EXTRA_CFLAGS=.*/CONFIG_EXTRA_CFLAGS="-mcpu=68030"/' \
	    -e 's/^CONFIG_TC=y/# CONFIG_TC is not set/' \
	    -e 's/^CONFIG_FEATURE_TC_INGRESS=y/# CONFIG_FEATURE_TC_INGRESS is not set/' \
	    "$OUT/busybox-build/.config"
	yes "" | $B oldconfig >> "$OUT/busybox-build.log" 2>&1
	$B -j"$JOBS" >> "$OUT/busybox-build.log" 2>&1 || { tail -20 "$OUT/busybox-build.log"; exit 1; }
fi

# 4. the initramfs
sed -e "s|@BUSYBOX@|$OUT/busybox-build/busybox|" -e "s|@INIT@|$HERE/init|" \
	"$HERE/cpio.list" > "$OUT/cpio.list"
"$OUT/linux-build/usr/gen_init_cpio" -t 0 "$OUT/cpio.list" | gzip -9n > "$OUT/initramfs.cpio.gz"
echo "built $OUT/vmlinux and $OUT/initramfs.cpio.gz"
