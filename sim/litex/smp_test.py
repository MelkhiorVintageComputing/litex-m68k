#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L20: smptest (smptest/) on two RD68041s ("+smp" variants, core.py), loaded
by the LiteX BIOS over the UART, in litex_sim or on a board (--serial), as
l4_test.py loads cputest.

    1. The SoC's software only (targets/sim.py --no-compile-gateware), then
       smptest against it.
    2. The simulation (or the board, its BIOS at its prompt: board.py
       prompt), litex_term on its UART loading smptest at 0x40000000.
    3. smptest: CPU 0 starts CPU 1, both run the tests, CPU 0 reports; its
       PASS line, and the report's lines checked: both CPUs' interrupts,
       the routine rewritten under CPU 1, the shootdown, timer0 and timer1
       each to its own CPU (when the SoC has timer1), the bus counters.

Prints the exchanges, then "SMP PASS" or "SMP FAIL: <why>"; exit status 0 on
pass. Needs --sdram in litex_sim: smptest uses main RAM up to $4013_FFFF."""

import argparse
import os
import pty
import re
import subprocess
import sys

from simtest import add_sim_options, sim_build_name, sim_options, Fail, TOP, check, fd_console, sim_command, start, stop, venv_env, wait_listening
from l4_test import LOAD, run, serial_boot

HERE = os.path.dirname(os.path.abspath(__file__))

def build_smptest(soc, out, iters, cflags=""):
    st = os.path.join(out, "smptest")
    os.makedirs(st, exist_ok=True)
    for f in os.listdir(st):
        os.remove(os.path.join(st, f))
    run(["make", "-f", os.path.join(HERE, "smptest", "Makefile"), "BUILD_DIR=" + soc,
         "ITERS={}".format(iters), "SMPTEST_CFLAGS=" + cflags], st, os.path.join(out, "smptest.log"), "smptest")
    return os.path.join(st, "smptest.bin")

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant",   default="standard+fpu+smp", help="CPU variant (a +smp one).")
    parser.add_argument("--port",      default=2432, type=int, help="TCP port for the UART.")
    parser.add_argument("--build-dir", default=None,
        help="Output directory (default: build/<variant>/smp, with the simulation options in its name).")
    parser.add_argument("--serial",    default=None,
        help="A board's serial port (e.g. /dev/ttyUSB0) instead of the simulation: the board runs "
             "--build-dir's SoC, its BIOS at its prompt (board.py prompt).")
    parser.add_argument("--speed",     default=115200, type=int, help="Board: the UART's baud rate.")
    parser.add_argument("--iters",     default=16, type=int, help="smptest's ITERS.")
    parser.add_argument("--negative",  action="store_true",
        help="smptest with the rewritten routine's page not snooped (U 00): it must fail, check $400.")
    add_sim_options(parser)
    args = parser.parse_args()
    args.cpu_type = "rd68041"
    check("+smp" in args.variant, "--variant must be a +smp one")

    build = os.path.abspath(args.build_dir or os.path.join(HERE, "build", args.variant, sim_build_name(args, "smp")))
    out = os.path.join(build, "smp") if args.serial else build
    os.makedirs(out, exist_ok=True)
    sim = term = None
    try:
        if not args.serial:
            run(sim_command(args.variant, build, 0, ["--no-compile-gateware"] + sim_options(args)), build,
                os.path.join(build, "software.log"), "the SoC's software")
        smptest = build_smptest(build, out, args.iters, "-DSMPTEST_NO_SNOOP_CODE" if args.negative else "")
        boot = os.path.join(out, "boot.bin")
        with open(smptest, "rb") as f, open(boot, "wb") as g:
            g.write(f.read())

        if args.serial:
            port, options = args.serial, ["--speed", str(args.speed)]
        else:
            sim = start(sim_command(args.variant, build, args.port, sim_options(args)), open(os.path.join(build, "sim.log"), "w"))
            wait_listening(args.port, sim)
            port = "socket://127.0.0.1:{}".format(args.port)
            options = ["--safe"]     # l4_test.py: 64-byte frames for the simulated UART

        master, slave = pty.openpty()
        term = subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "litex_term"), port,
            "--kernel", boot, "--kernel-adr", "0x{:08x}".format(LOAD), *options],
            stdin=slave, stdout=slave, stderr=slave, env=venv_env(), start_new_session=True)
        os.close(slave)
        con = fd_console(master, sys.stdout,
            alive=lambda: (sim is None or sim.poll() is None) and term.poll() is None)

        serial_boot(con, "smptest", at_prompt=bool(args.serial))
        out = con.expect(r"smptest: (PASS|FAIL).*\n", 3600)
        check("smptest: CPU 1 started, WHOAMI 1" in out, "CPU 1 did not start")
        if args.negative:
            check(re.search(r"smptest: FAIL \(CPU 0: 0 of \d+, first 000; CPU 1: 1 of \d+, first 400\)", out),
                  "the negative test: not the failure of check $400 alone")
            check("routine 1 then 1" in out, "the negative test: CPU 1 did not run the old routine")
            print("\nSMP PASS (the negative test failed as it must)", flush=True)
            return 0
        check("smptest: PASS" in out, "smptest failed")
        m = re.search(r"ipis (\d+) (\d+), ticks (\d+) (\d+), routine (\d+) then (\d+), remap (\w+) then (\w+)", out)
        check(m, "smptest: no report")
        ipis0, ipis1, ticks0, ticks1, r0, r1 = (int(m.group(k)) for k in range(1, 7))
        check(ipis0 == args.iters and ipis1 == args.iters + 1,
              "interprocessor interrupts: {} and {}".format(ipis0, ipis1))
        check(ticks0 >= 4 and ticks1 >= 4, "timer interrupts per CPU: {} and {}".format(ticks0, ticks1))
        check((r0, r1) == (1, 2), "the routine rewritten under CPU 1: {} then {}".format(r0, r1))
        check((m.group(7), m.group(8)) == ("aaaa0000", "bbbb0000"), "the shootdown")
        t = re.search(r"smptest: timer0 ticks (\d+) (\d+), timer1 ticks (\d+) (\d+)", out)
        if t:                           # timer1 (core.py's "+smp" SoCs since L23)
            t00, t10, t01, t11 = (int(t.group(k)) for k in range(1, 5))
            check(t00 >= 8 and t11 >= 8 and t10 == 0 and t01 == 0,
                  "a timer per CPU: timer0 {} {}, timer1 {} {}".format(t00, t10, t01, t11))
        bus = re.search(r"smptest: bus: (.*)\n", out)
        check(bus, "smptest: no bus counters")
        stats = dict((k, int(v)) for k, v in re.findall(r"(\w+) (\d+)", bus.group(1)))
        for k in ("contention", "turnaround", "owner", "atomicity", "intervene"):
            check(stats.get(k) == 0, "bus counter {}: {}".format(k, stats.get(k)))

        print("\nSMP PASS", flush=True)
        return 0
    except Fail as e:
        print("\nSMP FAIL: {}".format(e))
        return 1
    finally:
        for p in (term, sim):
            if p is not None:
                stop(p)

if __name__ == "__main__":
    sys.exit(main())
