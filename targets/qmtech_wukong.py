#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""RD68031 on the QMTech Wukong (xc7a100t), from litex-boards' target.

    targets/qmtech_wukong.py --build             # Vivado: a heavy job, run it alone
    targets/qmtech_wukong.py --load              # into the FPGA over JTAG (not the flash)
    litex_term /dev/ttyUSB0

Two configurations (doc/l5-wukong.md):

- DDR3 main RAM (the default): the SoC at --sys-clk-freq (100 MHz), which
  LiteDRAM's Artix-7 PHY needs, and the CPU at --cpu-clk-freq (30 MHz, 25
  with --bus-bridge sync; RD68031 makes about 29.6 MHz on an Artix-7) in a
  clock domain of its own, from the
  50 MHz input through an MMCM of its own. The bus crosses to sys through
  FIFOs (litex_68k/m68030_fifo.py), so any frequency that MMCM makes will do;
  with --bus-bridge sync (core.py), the CPU's clock comes from the main MMCM
  and must be a whole fraction of sys. Main RAM is on a LiteDRAM
  native port of the CPU's own (128 bits: the DDR3 is 16 bits, 1:4). Output:
  build/qmtech_wukong (build/qmtech_wukong_sync with --bus-bridge sync).
- --integrated-main-ram-size N: block RAM instead, and one clock for all, the
  CPU's. Output: build/qmtech_wukong_bram.

--cpu-variant standard+fpu adds the RD68884 FPU (core.py).

Defaults that differ from litex-boards': --cpu-type rd68031, --revision 3,
--timer-uptime, the clocks above, and the output directory, in this repository.
--bus-bridge (fifo or sync, RD68031.bus_bridge) is ours.
--load uses Vivado's hardware manager (the board's JTAG here is a Xilinx
Platform Cable, which litex-boards' OpenOCD configuration does not drive).
"""

import os
import subprocess
import sys

from migen import ClockDomain, If, Signal
from migen.genlib.cdc import MultiReg

from litex.soc.cores.clock import S7MMCM

from litex_68k.cpu import register
from litex_68k.cpu.rd68031.core import RD68031

TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VIVADO_SETTINGS = os.environ.get("LITEX_ENV_VIVADO", "/opt/Xilinx/2025.2/Vivado")

def vivado_load(bitstream):
    """Load a bitstream into the FPGA (its configuration memory, not the flash)."""
    bitstream = os.path.abspath(bitstream)
    tcl = os.path.splitext(bitstream)[0] + "_load.tcl"
    with open(tcl, "w") as f:
        f.write("open_hw_manager\n"
                "connect_hw_server\n"
                "open_hw_target\n"
                "set dev [lindex [get_hw_devices xc7a100t*] 0]\n"
                "set_property PROGRAM.FILE {{{}}} $dev\n"
                "program_hw_devices $dev\n"
                "close_hw_target\n"
                "disconnect_hw_server\n".format(bitstream))
    cmd = "source {}/settings64.sh && vivado -mode batch -nojournal -nolog -source {}".format(
        VIVADO_SETTINGS, tcl)
    subprocess.check_call(["bash", "-c", cmd], cwd=os.path.dirname(tcl))

def get_option(argv, opt, default=None, remove=False):
    """The value of `opt value` or `opt=value` in argv, taken out if `remove`."""
    for i, a in enumerate(argv):
        if a == opt:
            value = argv[i + 1]
            if remove:
                del argv[i:i + 2]
            return value
        if a.startswith(opt + "="):
            if remove:
                del argv[i]
            return a.split("=", 1)[1]
    return default

def crg(qmtech_wukong, cpu_clk_freq=None):
    """litex-boards' CRG, with a reset request that its MMCM sees, and, given
    cpu_clk_freq, a "cpu" clock domain from one more output of its MMCM.

    The reset: LiteX's MMCM takes its reset through flip-flops clocked by its
    input clock, 50 MHz here (xilinx_common.py), and the SoC's reset request
    (ctrl's soc_rst, the BIOS's reboot) is one sys clock long: 10 ns at 100
    MHz, which a 20 ns clock samples or not, depending on the phase between
    the two, which the build fixes. So the request is stretched, on the input
    clock: a sys clock stops while its MMCM is in reset, and a stretcher
    counting on it would never let the reset go. The request crosses as a
    toggle, which no reset touches."""
    RESET_CLOCKS = 16                           # of the input clock
    class _CRG(qmtech_wukong._CRG):
        def __init__(self, platform, speed_grade, sys_clk_freq, **kwargs):
            super().__init__(platform, speed_grade, sys_clk_freq, **kwargs)
            # The parent's MMCM reset is driven by self.rst: keep that signal,
            # and give the SoC a new self.rst to drive.
            mmcm_rst = self.rst
            self.rst = Signal()
            request  = Signal(reset_less=True)
            self.sync += If(self.rst, request.eq(~request))

            self.cd_crg_clkin = ClockDomain(reset_less=True)
            self.comb += self.cd_crg_clkin.clk.eq(self.pll.clkin)
            seen, seen_q = Signal(), Signal()
            count = Signal(max=RESET_CLOCKS + 1)
            self.specials += MultiReg(request, seen, "crg_clkin")
            self.sync.crg_clkin += [
                seen_q.eq(seen),
                If(seen != seen_q,
                    count.eq(RESET_CLOCKS)
                ).Elif(count != 0,
                    count.eq(count - 1)
                ),
            ]
            self.comb += mmcm_rst.eq(count != 0)

            if cpu_clk_freq is not None:
                self.cd_cpu = ClockDomain()
                if RD68031.bus_bridge == "fifo":
                    # An MMCM of its own, from the same 50 MHz and the same
                    # reset: the main MMCM's outputs stay litex-boards' (sys
                    # and the DDR3's 4x), and the CPU's clock is any its VCO
                    # makes. Every path between the CPU's clock and sys is a
                    # clock domain crossing designed as one (asynchronous
                    # FIFOs, MultiRegs; IPL the core synchronises itself), so
                    # the two clocks are asynchronous to Vivado too.
                    self.cpu_pll = cpu_pll = S7MMCM(speedgrade=speed_grade)
                    self.comb += cpu_pll.reset.eq(self.pll.reset)
                    cpu_pll.register_clkin(self.pll.clkin, 50e6)
                    cpu_pll.create_clkout(self.cd_cpu, cpu_clk_freq)
                    platform.add_false_path_constraints(self.cd_sys.clk, self.cd_cpu.clk)
                else:
                    # WishboneSyncCrossing (core.py) needs the CPU's clock and
                    # sys from one MMCM, aligned, and Vivado timing the paths
                    # between them.
                    self.pll.create_clkout(self.cd_cpu, cpu_clk_freq)
    qmtech_wukong._CRG = _CRG

def cpu_clock_domain(qmtech_wukong, cpu_clk_freq):
    """RD68031 in a "cpu" clock domain, one more output of litex-boards' main MMCM."""
    crg(qmtech_wukong, cpu_clk_freq)
    RD68031.clock_domain = "cpu"

def main():
    register()
    # The build runs tools from this Python's venv (meson, ninja), activated or not.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("LITEX_ENV_VIVADO", VIVADO_SETTINGS)

    argv = sys.argv[1:]
    # --load is ours: litex-boards' would run OpenOCD.
    load = "--load" in argv
    argv = [a for a in argv if a != "--load"]
    RD68031.bus_bridge = get_option(argv, "--bus-bridge", RD68031.bus_bridge, remove=True)
    # 30 MHz on a clock of its own with the FIFO bridge (doc/l6-fpu.md); 25 MHz,
    # a quarter of sys, with "sync".
    cpu_clk_freq = float(get_option(argv, "--cpu-clk-freq",
        "30e6" if RD68031.bus_bridge == "fifo" else "25e6", remove=True))
    ddr3 = get_option(argv, "--integrated-main-ram-size") is None
    name = "qmtech_wukong" if ddr3 else "qmtech_wukong_bram"
    if RD68031.bus_bridge != "fifo":
        name += "_" + RD68031.bus_bridge

    defaults = [
        ("--cpu-type",     "rd68031"),
        ("--revision",     "3"),
        ("--sys-clk-freq", "100e6" if ddr3 else str(cpu_clk_freq)),
        ("--output-dir",   os.path.join(TOP, "build", name)),
    ]
    for opt, value in defaults:
        if get_option(argv, opt) is None:
            argv = [opt, value] + argv
    if "--timer-uptime" not in argv:            # for the BIOS's uptime command
        argv = ["--timer-uptime"] + argv
    sys.argv[1:] = argv

    from litex_boards.targets import qmtech_wukong
    sys_clk_freq = float(get_option(argv, "--sys-clk-freq"))
    if sys_clk_freq != cpu_clk_freq:
        if RD68031.bus_bridge == "sync":
            # WishboneSyncCrossing (core.py) needs the CPU's clock to be sys's
            # divided by an integer.
            ratio = sys_clk_freq / cpu_clk_freq
            assert ratio == int(ratio), "--sys-clk-freq must be a multiple of --cpu-clk-freq"
        cpu_clock_domain(qmtech_wukong, cpu_clk_freq)
    else:
        crg(qmtech_wukong)
    qmtech_wukong.main()

    if load:
        vivado_load(os.path.join(get_option(argv, "--output-dir"), "gateware", "qmtech_wukong.bit"))

if __name__ == "__main__":
    main()
