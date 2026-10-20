#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""litex_sim with the m68k CPUs registered, and RD68031 the default CPU.

Every litex_sim option works as there, e.g.:

    targets/sim.py --integrated-main-ram-size=0x10000
    targets/sim.py --rom-init=prog.bin --no-compile-software --sim-debug --non-interactive

and one more:

    --uart-tx-clocks N   the UART sends at most one byte every N clocks, as a
                         baud rate would (115200 baud at 25 MHz: about 2170).
                         litex_sim's UART otherwise takes a byte every clock, so
                         its transmit FIFO never fills and software never waits
                         for it (doc/l5-wukong.md).
"""

import os
import sys

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream

from litex_68k.cpu import register

def paced_uart_phy(clocks):
    """litex_sim's UART PHY model, with its transmit side paced: one byte, then
    `clocks` - 1 clocks during which it takes nothing."""
    class PacedRS232PHYModel(LiteXModule):
        def __init__(self, pads):
            self.sink   = stream.Endpoint([("data", 8)])
            self.source = stream.Endpoint([("data", 8)])

            # # #

            wait = Signal(max=max(2, clocks))
            go   = Signal()
            self.comb += [
                go.eq(wait == 0),
                pads.source_valid.eq(self.sink.valid & go),
                pads.source_data.eq(self.sink.data),
                self.sink.ready.eq(pads.source_ready & go),

                self.source.valid.eq(pads.sink_valid),
                self.source.data.eq(pads.sink_data),
                pads.sink_ready.eq(self.source.ready),
            ]
            self.sync += [
                If(self.sink.valid & self.sink.ready,
                    wait.eq(clocks - 1)
                ).Elif(~go,
                    wait.eq(wait - 1)
                )
            ]
    return PacedRS232PHYModel

def main():
    register()
    # The build runs tools from this Python's venv (meson, ninja), activated or not.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    argv = sys.argv[1:]
    if "--uart-tx-clocks" in argv:
        i = argv.index("--uart-tx-clocks")
        clocks = int(argv[i + 1], 0)
        del argv[i:i + 2]
        from litex.soc.cores import uart
        uart.RS232PHYModel = paced_uart_phy(clocks)
    if not any(a.startswith("--cpu-type") for a in argv):
        argv = ["--cpu-type=rd68031"] + argv
    sys.argv[1:] = argv
    from litex.tools import litex_sim
    litex_sim.main()

if __name__ == "__main__":
    main()
