#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L19: the RAM image for RD68041's two-processor program (Inputs/RD68041's
sim/programs/m14_smp.S, built as a flat binary at 0): padded to 64 KiB, with
the program's parameters poked where it reads them (its CI page, $8000):
ITERS at $8000, UMASK at $8004 (ANDed into every page descriptor: 0 clears the
U bits, so nothing is snooped) and WTSHARED at $8008 (non-zero: every shared
page written through, U 01, setup A's policy, doc/l18-smp.md). Written as
tb.sv's +ram= wants it: one 32-bit big-endian word per line."""

import argparse
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bin")
parser.add_argument("hex")
parser.add_argument("--iters",    type=int, default=6)
parser.add_argument("--umask",    type=lambda x: int(x, 0), default=0xFFFFFFFF)
parser.add_argument("--wtshared", type=int, default=1)
args = parser.parse_args()

image = bytearray(open(args.bin, "rb").read())
assert len(image) <= 0x8000, "the program reaches its CI page"
image += bytes(0x10000 - len(image))
struct.pack_into(">III", image, 0x8000, args.iters, args.umask, args.wtshared)
with open(args.hex, "w") as f:
    for k in range(0, len(image), 4):
        f.write("{:08x}\n".format(struct.unpack_from(">I", image, k)[0]))
