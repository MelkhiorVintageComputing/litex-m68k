#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""fpswitch (fpswitch/): two FPU tasks switched by timer0, the FPU saved and
restored as Linux does, in litex_sim (doc/l7-linux.md).

    fpswitch_test.py [--variant standard+fpu] [-- <targets/sim.py options>]

The SoC is built with fpswitch preloaded in main RAM (--ram-init), and the
BIOS jumps to it. Prints the exchange, then "FPSWITCH PASS" or
"FPSWITCH FAIL: <why>"; exit status 0 on pass. Options after "--" go to
targets/sim.py (--trace, --trace-start, ... to look at a failure).
"""

import argparse
import os
import signal
import subprocess
import sys

from simtest import Fail, check, sim_command, socket_console, start, stop, venv_env, wait_listening

HERE = os.path.dirname(os.path.abspath(__file__))

def run(cmd, cwd, log, what):
    with open(log, "w") as f:
        if subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=venv_env()):
            raise Fail("{} failed (see {})".format(what, log))

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    parser = argparse.ArgumentParser(description="fpswitch: FPU context switches in litex_sim.")
    parser.add_argument("--variant", default="standard+fpu", help="CPU variant (with an FPU).")
    parser.add_argument("--cpu-type", default="rd68031", choices=["rd68031", "rd68041"], help="The CPU.")
    parser.add_argument("--port",    default=2433, type=int, help="TCP port for the UART.")
    parser.add_argument("--timeout", default=3600, type=float, help="Seconds for the whole run.")
    parser.add_argument("sim_args", nargs="*", help="After --: more targets/sim.py options.")
    args = parser.parse_args()

    build = os.path.join(HERE, "build", args.variant,
        "fpswitch" + ("-" + args.cpu_type if args.cpu_type != "rd68031" else ""))
    cpu = ["--cpu-type", args.cpu_type]
    out = os.path.join(build, "fpswitch")
    os.makedirs(out, exist_ok=True)
    sim = None
    try:
        run(sim_command(args.variant, build, 0, ["--no-compile-gateware", *cpu], main_ram=0x100000),
            build, os.path.join(build, "software.log"), "the SoC's software")
        run(["make", "-f", os.path.join(HERE, "fpswitch", "Makefile"), "BUILD_DIR=" + build],
            out, os.path.join(build, "fpswitch.log"), "fpswitch")
        sim = start(sim_command(args.variant, build, args.port,
            ["--ram-init", os.path.join(out, "fpswitch.bin"), *cpu, *args.sim_args], main_ram=0x100000),
            open(os.path.join(build, "sim.log"), "w"))
        wait_listening(args.port, sim)
        con = socket_console(args.port, sys.stdout, sim)
        con.expect(r"fpswitch: two FPU tasks", 600)
        got = con.expect(r"fpswitch: (PASS|FAIL)\n", args.timeout)
        check("fpswitch: PASS" in got, "fpswitch failed")
        print("\nFPSWITCH PASS", flush=True)
        return 0
    except Fail as e:
        print("\nFPSWITCH FAIL: {}".format(e))
        return 1
    finally:
        if sim is not None:
            stop(sim)

if __name__ == "__main__":
    sys.exit(main())
