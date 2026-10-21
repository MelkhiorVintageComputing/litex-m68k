#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
#
# Buildroot's post-image script (doc/l8-buildroot.md): name the initramfs as
# linux/build.sh does, so that the images directory is a --linux-dir for
# sim/litex/l7_test.py (vmlinux, initramfs.cpio.gz). images.json depends on
# the SoC (where its main RAM is), so l7_test.py writes it, with images.py.
set -e
BINARIES_DIR=$1
ln -sf rootfs.cpio.gz "$BINARIES_DIR/initramfs.cpio.gz"
