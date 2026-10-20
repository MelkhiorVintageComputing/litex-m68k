#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""RD68031 on the QMTech Wukong (xc7a100t), from litex-boards' target.

L5 stage 1: the whole SoC on one 25 MHz clock (RD68031 makes about 29.6 MHz
on an Artix-7, doc/plan.md), with integrated main RAM: no DDR3 yet.

    targets/qmtech_wukong.py --build             # Vivado: a heavy job, run it alone
    targets/qmtech_wukong.py --load              # into the FPGA over JTAG (not the flash)
    litex_term /dev/ttyUSB0

Defaults that differ from litex-boards': --cpu-type rd68031, --revision 3,
--sys-clk-freq 25e6, --integrated-main-ram-size 0x10000, --timer-uptime, and --output-dir
build/qmtech_wukong in this repository. --load uses Vivado's hardware manager
(the board's JTAG here is a Xilinx Platform Cable, which litex-boards'
OpenOCD configuration does not drive).
"""

import os
import subprocess
import sys

from litex_68k.cpu import register

TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VIVADO_SETTINGS = os.environ.get("LITEX_ENV_VIVADO", "/opt/Xilinx/2025.2/Vivado")

DEFAULTS = [
    ("--cpu-type",                 "rd68031"),
    ("--revision",                 "3"),
    ("--sys-clk-freq",             "25e6"),
    ("--integrated-main-ram-size", "0x10000"),
    ("--output-dir",               os.path.join(TOP, "build", "qmtech_wukong")),
]
FLAGS = ["--timer-uptime"]                      # for the BIOS's uptime command

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

def main():
    register()
    # The build runs tools from this Python's venv (meson, ninja), activated or not.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("LITEX_ENV_VIVADO", VIVADO_SETTINGS)
    argv = sys.argv[1:]
    for opt, value in DEFAULTS:
        if not any(a == opt or a.startswith(opt + "=") for a in argv):
            argv = [opt, value] + argv
    argv = [f for f in FLAGS if f not in argv] + argv
    # --load is ours: litex-boards' would run OpenOCD.
    load = "--load" in argv
    argv = [a for a in argv if a != "--load"]
    sys.argv[1:] = argv

    from litex_boards.targets import qmtech_wukong
    qmtech_wukong.main()

    if load:
        out = argv[argv.index("--output-dir") + 1]
        vivado_load(os.path.join(out, "gateware", "qmtech_wukong.bit"))

if __name__ == "__main__":
    main()
