#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L4: bare-metal programs on RD68031, loaded by the LiteX BIOS over the UART.

The way a board is used: litex_term on the UART (here the simulation's TCP
port, as socket://), answering the BIOS's serial boot request with an image.

    1. The SoC's software only (targets/sim.py --no-compile-gateware), then
       against it LiteX's own bare-metal demo, unchanged (litex_bare_metal_demo's
       demo.py), and cputest (cputest/).
    2. The simulation, litex_term on its UART through a pseudo-terminal (it
       wants one), loading <build>/boot.bin at 0x40000000. boot.bin is the
       demo first: litex_term reads the file when the BIOS asks for it.
    3. The demo: its banner, help, helloc. Then boot.bin becomes cputest and
       the demo's reboot resets the SoC: the BIOS boots again, loads cputest.
    4. cputest: its PASS line, what the caches did (stale with the standard
       variant's caches, coherent without them; the data cache's aliases only
       without write allocation), and crt0's report of the
       last exception, an illegal instruction at the address cputest gave.

Prints the exchanges, then "L4 PASS" or "L4 FAIL: <why>"; exit status 0 on pass.
"""

import argparse
import os
import pty
import re
import shutil
import signal
import subprocess
import sys
import time

from simtest import pacing, Fail, PROMPT, TOP, check, fd_console, sim_command, start, stop, venv_env, wait_listening

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD = 0x40000000
DEMO_PROMPT = "litex-demo-app>"

def run(cmd, cwd, log, what):
    with open(log, "w") as f:
        if subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=venv_env()):
            raise Fail("{} failed (see {})".format(what, log))

def build_programs(soc, out):
    """The demo and cputest, against the software of the SoC built in `soc`, into `out`."""
    run([sys.executable, "-m", "litex.soc.software.demo.demo", "--build-path", soc], out,
        os.path.join(out, "demo.log"), "the demo")
    ct = os.path.join(out, "cputest")
    os.makedirs(ct, exist_ok=True)
    run(["make", "-f", os.path.join(HERE, "cputest", "Makefile"), "BUILD_DIR=" + soc], ct,
        os.path.join(out, "cputest.log"), "cputest")
    return os.path.join(out, "demo.bin"), os.path.join(ct, "cputest.bin")

def serial_boot(con, what):
    """The BIOS loads boot.bin, whether its boot sequence asks litex_term for it
    or, when that came before litex_term was there, the serialboot command."""
    out = con.expect(r"Executing booted program|" + re.escape(PROMPT), 900)
    if out.endswith(PROMPT):
        con.type("serialboot")
        out = con.expect(r"Executing booted program", 600)
    check(re.search(r"Upload complete", out), "{}: litex_term did not upload it".format(what))

def main():
    # A SIGTERM (timeout(1), say) ends the test through its finally: litex_term,
    # in a session of its own, would otherwise keep the serial port.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    parser = argparse.ArgumentParser(description="L4: bare-metal programs on RD68031, under litex_sim.")
    parser.add_argument("--variant",   default="standard", help="CPU variant.")
    parser.add_argument("--port",      default=2431, type=int, help="TCP port for the UART.")
    parser.add_argument("--build-dir", default=None, help="Output directory (default: build/<variant>/l4).")
    parser.add_argument("--uart-tx-clocks", default=None,
        help="Simulation: pace the UART's transmit side, a byte every N clocks (targets/sim.py).")
    parser.add_argument("--serial",    default=None,
        help="A board's serial port (e.g. /dev/ttyUSB0) instead of the simulation: the board runs "
             "the SoC built in --build-dir, its BIOS at the prompt; the programs go to <build-dir>/l4.")
    args = parser.parse_args()

    build = os.path.abspath(args.build_dir or os.path.join(HERE, "build", args.variant, "l4"))
    out = os.path.join(build, "l4") if args.serial else build
    os.makedirs(out, exist_ok=True)
    sim = term = None
    try:
        if not args.serial:
            # The SoC's software, for the programs to build against.
            run(sim_command(args.variant, build, 0, ["--no-compile-gateware"]), build,
                os.path.join(build, "software.log"), "the SoC's software")
        demo, cputest = build_programs(build, out)
        boot = os.path.join(out, "boot.bin")
        shutil.copy(demo, boot)

        if args.serial:
            port, options = args.serial, []
        else:
            sim = start(sim_command(args.variant, build, args.port, pacing(args)), open(os.path.join(build, "sim.log"), "w"))
            wait_listening(args.port, sim)
            port = "socket://127.0.0.1:{}".format(args.port)
            # 64-byte frames: the simulated UART has no baud rate, a longer
            # frame overflows the BIOS's 128-byte receive ring (doc/l4-bare-metal.md).
            options = ["--safe"]

        master, slave = pty.openpty()
        term = subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "litex_term"), port,
            "--kernel", boot, "--kernel-adr", "0x{:08x}".format(LOAD), *options],
            stdin=slave, stdout=slave, stderr=slave, env=venv_env(), start_new_session=True)
        os.close(slave)
        con = fd_console(master, sys.stdout,
            alive=lambda: (sim is None or sim.poll() is None) and term.poll() is None)
        if args.serial:
            con.type("")                # the BIOS is idle at its prompt: show it

        # The demo.
        serial_boot(con, "the demo")
        out = con.expect(re.escape(DEMO_PROMPT), 120)
        check("LiteX minimal demo app built" in out, "the demo: no banner")
        out = con.command("help", until=DEMO_PROMPT)
        check("helloc" in out and "reboot" in out, "the demo: help does not list its commands")
        out = con.command("helloc", until=DEMO_PROMPT)
        check("C: Hello, world!" in out, "the demo: helloc did not say hello")

        # cputest, by the BIOS again after a reset.
        shutil.copy(cputest, boot)
        con.type("reboot")
        out = con.expect(r"BIOS built on", 300)
        serial_boot(con, "cputest")
        out = con.expect(r"cputest: (PASS|FAIL).*\n", 1200)
        check("cputest: PASS" in out, "cputest failed")
        icache = re.search(r"icache: (stale until CI|coherent)", out)
        dcache = re.search(r"dcache: write allocation coherent, without it (stale until CD|coherent)", out)
        check(icache and dcache, "cputest: no cache report")
        cached = args.variant != "minimal"
        check((icache.group(1) == "stale until CI") == cached,
            "icache: {} on the {} variant".format(icache.group(1), args.variant))
        check((dcache.group(1) == "stale until CD") == cached,
            "dcache without write allocation: {} on the {} variant".format(dcache.group(1), args.variant))
        out = con.expect(r"SR [0-9a-f]{4}\n", 60)
        m = re.search(r"illegal instruction at ([0-9a-f]{8})", out)
        check(m, "cputest: no address for the last exception")
        check(re.search(r"exception: format/vector 0010, PC {}".format(m.group(1)), out),
            "crt0: no report of the illegal instruction at {}".format(m.group(1)))

        print("\nL4 PASS", flush=True)
        return 0
    except Fail as e:
        print("\nL4 FAIL: {}".format(e))
        return 1
    finally:
        for p in (term, sim):
            if p is not None:
                stop(p)

if __name__ == "__main__":
    sys.exit(main())
