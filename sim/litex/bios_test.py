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
import signal
import socket
import subprocess
import sys
import time
import zlib

TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ANSI = re.compile(rb"\x1b\[[0-9;]*m")
PROMPT = "litex>"

class Fail(Exception):
    pass

class Console:
    def __init__(self, port, log, sim):
        self.log = log
        deadline = time.time() + 600
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if sim.poll() is not None:
                    raise Fail("the simulation ended before its UART appeared (see sim.log)")
                if time.time() > deadline:
                    raise Fail("no UART on port {}".format(port))
                time.sleep(0.5)
        self.buf = ""
        self.raw = b""

    def expect(self, pattern, timeout):
        """Read until pattern (a regex) appears; return everything up to it."""
        deadline = time.time() + timeout
        while True:
            m = re.search(pattern, self.buf)
            if m:
                out, self.buf = self.buf[:m.end()], self.buf[m.end():]
                return out
            if time.time() > deadline:
                raise Fail("timeout waiting for {!r}; last output: {!r}".format(pattern, self.buf[-300:]))
            self.sock.settimeout(max(0.1, min(1.0, deadline - time.time())))
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise Fail("UART closed; last output: {!r}".format(self.buf[-300:]))
            # serial2tcp sends a byte at a time: escape sequences are stripped
            # from what has accumulated, not from each piece.
            self.raw += data
            text = ANSI.sub(b"", self.raw)
            if b"\x1b" in text:            # an escape sequence not complete yet
                continue
            self.raw = b""
            text = text.decode("latin-1").replace("\r", "")
            self.log.write(text)
            self.log.flush()
            self.buf += text

    def command(self, cmd, timeout=120, until=PROMPT):
        # The BIOS echoes as it reads; type slowly enough for a 1 MHz CPU.
        for c in cmd + "\n":
            self.sock.sendall(c.encode())
            time.sleep(0.02)
        out = self.expect(re.escape(until), timeout)
        # Drop the echoed command line.
        return out.split("\n", 1)[1] if "\n" in out else out

def csr_ident(build):
    for line in open(os.path.join(build, "csr.csv")):
        f = line.strip().split(",")
        if f[:2] == ["csr_base", "identifier_mem"]:
            return int(f[2], 0)
    raise Fail("no identifier_mem in csr.csv")

def check(cond, what):
    if not cond:
        raise Fail(what)

def main():
    parser = argparse.ArgumentParser(description="L3: the LiteX BIOS on RD68031, under litex_sim.")
    parser.add_argument("--variant",   default="standard", help="CPU variant.")
    parser.add_argument("--port",      default=2430, type=int, help="TCP port for the UART.")
    parser.add_argument("--build-dir", default=None, help="Output directory (default: build/<variant>/bios).")
    args = parser.parse_args()

    build = args.build_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", args.variant, "bios")
    os.makedirs(build, exist_ok=True)
    sim_log = open(os.path.join(build, "sim.log"), "w")
    # The build runs tools from the venv (meson, ninja) whether it is activated or not.
    env = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
    sim = subprocess.Popen([sys.executable, os.path.join(TOP, "targets", "sim.py"),
        "--cpu-variant", args.variant,
        "--output-dir", build,
        "--integrated-main-ram-size", "0x10000",
        "--sim-debug",
        "--timer-uptime",                       # for the uptime command
        "--uart-tcp", "--uart-tcp-port", str(args.port),
        "--non-interactive"],
        stdout=sim_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
        start_new_session=True) # The simulator is a grandchild: end the whole group.

    try:
        con = Console(args.port, sys.stdout, sim)
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
        t0 = uptime()
        t1 = uptime()
        check(t1 > t0, "uptime does not move: {} then {}".format(t0, t1))

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
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(sim.pid, sig)
                sim.wait(timeout=10)
                break
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

if __name__ == "__main__":
    sys.exit(main())
