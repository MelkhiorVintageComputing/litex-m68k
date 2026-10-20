#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""litex_sim with the m68k CPUs registered, and RD68031 the default CPU.

Every litex_sim option works as there, e.g.:

    targets/sim.py --integrated-main-ram-size=0x10000
    targets/sim.py --rom-init=prog.bin --no-compile-software --sim-debug --non-interactive
"""

import sys

from litex_68k.cpu import register

def main():
    register()
    if not any(a.startswith("--cpu-type") for a in sys.argv[1:]):
        sys.argv[1:1] = ["--cpu-type=rd68031"]
    from litex.tools import litex_sim
    litex_sim.main()

if __name__ == "__main__":
    main()
