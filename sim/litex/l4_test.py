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
import subprocess
import sys
import time

from simtest import Fail, PROMPT, TOP, check, fd_console, sim_command, start, stop, venv_env, wait_listening

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD = 0x40000000
DEMO_PROMPT = "litex-demo-app>"

def run(cmd, cwd, log, what):
    with open(log, "w") as f:
        if subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=venv_env()):
            raise Fail("{} failed (see {})".format(what, log))

def build_programs(variant, build):
    """The SoC's software, then the demo and cputest against it."""
    run(sim_command(variant, build, 0, ["--no-compile-gateware"]), build,
        os.path.join(build, "software.log"), "the SoC's software")
    run([sys.executable, "-m", "litex.soc.software.demo.demo", "--build-path", build], build,
        os.path.join(build, "demo.log"), "the demo")
    out = os.path.join(build, "cputest")
    os.makedirs(out, exist_ok=True)
    run(["make", "-f", os.path.join(HERE, "cputest", "Makefile"), "BUILD_DIR=" + build], out,
        os.path.join(build, "cputest.log"), "cputest")
    return os.path.join(build, "demo.bin"), os.path.join(out, "cputest.bin")

def serial_boot(con, what):
    """The BIOS loads boot.bin, whether its boot sequence asks litex_term for it
    or, when that came before litex_term was there, the serialboot command."""
    out = con.expect(r"Executing booted program|" + re.escape(PROMPT), 900)
    if out.endswith(PROMPT):
        con.type("serialboot")
        out = con.expect(r"Executing booted program", 600)
    check(re.search(r"Upload complete", out), "{}: litex_term did not upload it".format(what))

def main():
    parser = argparse.ArgumentParser(description="L4: bare-metal programs on RD68031, under litex_sim.")
    parser.add_argument("--variant",   default="standard", help="CPU variant.")
    parser.add_argument("--port",      default=2431, type=int, help="TCP port for the UART.")
    parser.add_argument("--build-dir", default=None, help="Output directory (default: build/<variant>/l4).")
    args = parser.parse_args()

    build = os.path.abspath(args.build_dir or os.path.join(HERE, "build", args.variant, "l4"))
    os.makedirs(build, exist_ok=True)
    sim = term = None
    try:
        demo, cputest = build_programs(args.variant, build)
        boot = os.path.join(build, "boot.bin")
        shutil.copy(demo, boot)

        sim = start(sim_command(args.variant, build, args.port), open(os.path.join(build, "sim.log"), "w"))
        wait_listening(args.port, sim)

        master, slave = pty.openpty()
        term = subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "litex_term"),
            "socket://127.0.0.1:{}".format(args.port),
            "--kernel", boot, "--kernel-adr", "0x{:08x}".format(LOAD),
            # 64-byte frames: the simulated UART has no baud rate, a longer
            # frame overflows the BIOS's 128-byte receive ring (doc/l4-bare-metal.md).
            "--safe"],
            stdin=slave, stdout=slave, stderr=slave, env=venv_env(), start_new_session=True)
        os.close(slave)
        con = fd_console(master, sys.stdout, alive=lambda: sim.poll() is None and term.poll() is None)

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
