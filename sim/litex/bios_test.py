#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L3: the LiteX BIOS on RD68031, under litex_sim, driven over the UART.

Builds the SoC with its BIOS (targets/sim.py), runs it with the UART on a TCP
port, waits for the prompt and types commands, each answer checked against
something worked out independently of the CPU:

    mem_read          the identifier memory, a character per CSR word: CSR byte lanes
    mem_write/read    byte order: a long word written reads back MSB first
    mem_test          the BIOS's own memory test on main RAM
    crc               the BIOS's CRC32 of the start of the ROM, against Python's
                      zlib.crc32 of the same bytes of bios.bin
    uptime (twice)    the timer moves
    flush_cpu_dcache  CACR's CD, then memory still reads right
    mem_read          of an address nothing answers: the bridge's timeout, a bus
                      error, and crt0's exception report (format $B, vector 2).
                      Last, since the BIOS stops there.

Prints each exchange, then "L3 PASS" or "L3 FAIL: <why>"; exit status 0 on pass.
"""

import argparse
import os
import re
import sys
import zlib

from simtest import pacing, Fail, PROMPT, check, serial_console, sim_command, socket_console, start, stop

def csr_ident(build):
    for line in open(os.path.join(build, "csr.csv")):
        f = line.strip().split(",")
        if f[:2] == ["csr_base", "identifier_mem"]:
            return int(f[2], 0)
    raise Fail("no identifier_mem in csr.csv")

def main():
    parser = argparse.ArgumentParser(description="L3: the LiteX BIOS on RD68031, under litex_sim.")
    parser.add_argument("--variant",   default="standard", help="CPU variant.")
    parser.add_argument("--port",      default=2430, type=int, help="TCP port for the UART.")
    parser.add_argument("--build-dir", default=None, help="Output directory (default: build/<variant>/bios).")
    parser.add_argument("--uart-tx-clocks", default=None,
        help="Simulation: pace the UART's transmit side, a byte every N clocks (targets/sim.py).")
    parser.add_argument("--serial",    default=None,
        help="A board's serial port (e.g. /dev/ttyUSB0) instead of the simulation: the board runs "
             "the SoC built in --build-dir, its BIOS at the prompt.")
    args = parser.parse_args()

    build = args.build_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", args.variant, "bios")
    os.makedirs(build, exist_ok=True)
    sim = None
    try:
        if args.serial:
            con = serial_console(args.serial, sys.stdout)
            con.type("")
        else:
            sim = start(sim_command(args.variant, build, args.port, pacing(args)), open(os.path.join(build, "sim.log"), "w"))
            con = socket_console(args.port, sys.stdout, sim)
        con.expect(re.escape(PROMPT), 900)

        # The identifier, one character per 32-bit CSR word, in its low byte: at
        # +3 on a big-endian bus. (The BIOS's own ident command only reads it
        # when LiteX defines CSR_IDENTIFIER_MEM_BASE_VA, which it does only for
        # CPUs whose BIOS mapping differs from the physical one: prints "-".)
        ident = csr_ident(build)
        out = con.command("mem_read 0x{:08x} 16".format(ident))
        check(re.search(r"{:08x}\s+00 00 00 4c 00 00 00 69 00 00 00 74 00 00 00 65".format(ident), out),
            "identifier memory: not 'Lite' in the low byte of each word")

        con.command("mem_write 0x40001000 0x12345678")
        out = con.command("mem_read 0x40001000 4")
        check(re.search(r"40001000\s+12 34 56 78", out), "mem_read: not 12 34 56 78, MSB first")

        out = con.command("mem_test 0x40000000 0x4000", timeout=600)
        check("Memtest OK" in out, "mem_test failed")

        bios = open(os.path.join(build, "software", "bios", "bios.bin"), "rb").read()
        out = con.command("crc 0x0 0x400")
        m = re.search(r"CRC32:\s*([0-9a-fA-F]+)", out)
        check(m, "crc: no CRC32 in the answer")
        expected = zlib.crc32(bios[:0x400]) & 0xffffffff
        check(int(m.group(1), 16) == expected,
            "crc: {} from the BIOS, {:08x} from zlib".format(m.group(1), expected))

        def uptime():
            out = con.command("uptime")
            m = re.search(r"Uptime:\s*(\d+)\s*sys_clk", out)
            check(m, "uptime: no sys_clk count")
            return int(m.group(1))
        if "timer0_uptime" in open(os.path.join(build, "csr.csv")).read():
            t0 = uptime()
            t1 = uptime()
            check(t1 > t0, "uptime does not move: {} then {}".format(t0, t1))
        else:
            print("\n(no --timer-uptime in this SoC: uptime not checked)")

        con.command("mem_write 0x40001004 0xcafef00d")
        con.command("flush_cpu_dcache")
        out = con.command("mem_read 0x40001004 4")
        check(re.search(r"40001004\s+ca fe f0 0d", out), "mem_read after flush_cpu_dcache")

        out = con.command("mem_read 0x90000000 4", until="SR ")
        check(re.search(r"exception: format/vector b008", out),
            "no bus error report (format $B, vector offset $008)")

        print("\nL3 PASS", flush=True)
        return 0
    except Fail as e:
        print("\nL3 FAIL: {}".format(e))
        return 1
    finally:
        if sim is not None:
            stop(sim)

if __name__ == "__main__":
    sys.exit(main())
