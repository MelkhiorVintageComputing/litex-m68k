#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""litex_sim with the m68k CPUs registered, and RD68031 the default CPU
(--cpu-type rd68041 for RD68041).

Every litex_sim option works as there, e.g.:

    targets/sim.py --integrated-main-ram-size=0x10000
    targets/sim.py --rom-init=prog.bin --no-compile-software --sim-debug --non-interactive

and one more:

    --uart-tx-clocks N   the UART sends at most one byte every N clocks, as a
                         baud rate would (115200 baud at 25 MHz: about 2170).
                         litex_sim's UART otherwise takes a byte every clock, so
                         its transmit FIFO never fills and software never waits
                         for it (doc/l5-wukong.md).
    --cpu-clk-freq F     the CPU on a clock of its own, F Hz, and the rest of the
                         SoC at litex_sim's sys, 1 MHz (e.g. 0.3125e6, as 31.25
                         MHz against 100 MHz). The FIFO bridges need no
                         relation between the two; litex_sim needs half its
                         period to be a whole number of picoseconds.
"""

import os
import sys

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream

from litex.build.generic_platform import Pins

from litex_68k.cpu import register
from litex_68k.cpu.rd68031.core import RD68031
from litex_68k.cpu.rd68041.core import RD68041

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

def cpu_clock_domain(litex_sim, freq):
    """A "cpu" clock domain, from a second clocker at `freq`, for the CPU."""
    litex_sim._io.append(("cpu_clk", 0, Pins(1)))

    class SimConfig(litex_sim.SimConfig):
        def add_clocker(self, clk, freq_hz, phase_deg=0):
            super().add_clocker(clk, freq_hz, phase_deg)
            if clk == "sys_clk":
                super().add_clocker("cpu_clk", int(freq))
    litex_sim.SimConfig = SimConfig

    class SimSoC(litex_sim.SimSoC):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Reset with sys: both come out of reset together, as from one MMCM.
            self.cd_cpu = ClockDomain()
            self.comb += [
                self.cd_cpu.clk.eq(self.platform.request("cpu_clk")),
                self.cd_cpu.rst.eq(ResetSignal("sys")),
            ]
    litex_sim.SimSoC = SimSoC
    RD68031.clock_domain = "cpu"
    RD68041.clock_domain = "cpu"

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
    cpu_clk_freq = None
    if "--cpu-clk-freq" in argv:
        i = argv.index("--cpu-clk-freq")
        cpu_clk_freq = float(argv[i + 1])
        del argv[i:i + 2]
    if not any(a.startswith("--cpu-type") for a in argv):
        argv = ["--cpu-type=rd68031"] + argv
    sys.argv[1:] = argv
    from litex.tools import litex_sim
    if cpu_clk_freq is not None:
        cpu_clock_domain(litex_sim, cpu_clk_freq)
    litex_sim.main()

if __name__ == "__main__":
    main()
