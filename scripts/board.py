#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The Wukong board for software work: load a bitstream, boot a kernel on it,
talk to it. No Vivado knowledge needed (doc/board.md).

    board.py load                      load the bitstream (JTAG); the BIOS boots as it likes
    board.py prompt                    load it and stop the BIOS at its prompt
    board.py console                   a terminal on the board's UART (litex_term; Ctrl-C quits)
    board.py boot --kernel ELF [--initrd F] [--cmdline S] [--no-dtb]
                                       load, upload litexboot and the kernel by serial, boot,
                                       then the terminal
    board.py boot --images JSON        the same with images made already (images.json)
    board.py sdboot                    load and let the BIOS boot from the SD card's boot.json

boot and sdboot start litex_term first and then load the bitstream, so that the
BIOS's serial boot (the first it tries) finds litex_term at once, and the whole
boot is seen. Interactive by default; with --until REGEX they run unattended
instead: the console goes to stdout (and --log), they stop once REGEX has
appeared (exit 0) or after --timeout (exit 1), typing each --send line first,
each answered by --prompt.

The bitstream and the SoC's software it goes with (headers, libraries,
csr.json) are a snapshot directory (scripts/snapshot_bitstream.sh), by default
~/LITEX-68K/bitstreams/wukong-fpu-eth-sd-7c668f4. Run from the project's venv
(source ~/LITEX-68K/litex-venv/bin/activate)."""

import argparse
import os
import pty
import re
import shutil
import signal
import subprocess
import sys
import time

TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(TOP, "sim", "litex"), os.path.join(TOP, "targets")]
from simtest import PROMPT, Fail, fd_console, serial_console, venv_env  # noqa: E402

HOME = os.path.expanduser("~")
DEFAULT_BITSTREAM = os.path.join(HOME, "LITEX-68K", "bitstreams", "wukong-fpu-eth-sd-7c668f4")
DTC_CANDIDATES = [os.path.join(HOME, "LITEX-68K", "build", d, "host", "bin", "dtc")
                  for d in ("buildroot-fpu", "buildroot-nofpu")]

def bitstream_file(snapshot):
    bit = os.path.join(snapshot, "gateware", "qmtech_wukong.bit")
    if not os.path.exists(bit):
        sys.exit("no bitstream: {} (scripts/snapshot_bitstream.sh)".format(bit))
    return bit

def load(args):
    """The bitstream into the FPGA over JTAG, Vivado's output in a log."""
    from qmtech_wukong import vivado_load
    log = os.path.join(args.out_dir, "vivado-load.log")
    os.makedirs(args.out_dir, exist_ok=True)
    print("[board] loading {} (Vivado's log: {})".format(bitstream_file(args.bitstream), log), flush=True)
    vivado_load(bitstream_file(args.bitstream), log=log)
    print("[board] loaded", flush=True)

def litex_term(args, images=None):
    return [os.path.join(os.path.dirname(sys.executable), "litex_term"), args.port,
            "--speed", str(args.speed)] + (["--images", images] if images else [])

def find_dtc(args):
    if args.no_dtb:
        return None
    if args.dtc:
        return args.dtc
    for c in [shutil.which("dtc")] + DTC_CANDIDATES:
        if c and os.path.exists(c):
            return c
    sys.exit("no dtc found: give --dtc, or --no-dtb")

def make_images(args):
    """litexboot built against the snapshot's software, and images.py's layout."""
    lb = os.path.join(args.out_dir, "litexboot")
    images = os.path.join(args.out_dir, "images")
    os.makedirs(lb, exist_ok=True)
    shutil.rmtree(images, ignore_errors=True)
    subprocess.check_call(["make", "-s", "-f", os.path.join(TOP, "sw", "litexboot", "Makefile"),
                           "BUILD_DIR=" + args.bitstream], cwd=lb, env=venv_env())
    cmd = [sys.executable, os.path.join(TOP, "sw", "litexboot", "images.py"),
           "--build-dir", args.bitstream, "--vmlinux", args.kernel,
           "--loader", os.path.join(lb, "litexboot.bin"), "--cmdline", args.cmdline,
           "--out-dir", images]
    if args.initrd:
        cmd += ["--initramfs", args.initrd]
    dtc = find_dtc(args)
    if dtc:
        cmd += ["--dtc", dtc]
    subprocess.check_call(cmd, env=venv_env())
    return os.path.join(images, "images.json")

class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, s):
        for f in self.files:
            f.write(s)
    def flush(self):
        for f in self.files:
            f.flush()

def run_with_term(args, images=None):
    """litex_term on the port, then the bitstream loaded: interactive, or
    unattended with --until."""
    os.makedirs(args.out_dir, exist_ok=True)
    if args.until is None:
        term = subprocess.Popen(litex_term(args, images), env=venv_env())
        time.sleep(1)                       # the port open before the BIOS starts
        try:
            load(args)
            print("[board] litex_term is the console now (Ctrl-C quits)", flush=True)
            return term.wait()
        except KeyboardInterrupt:
            return 0
        finally:
            if term.poll() is None:
                term.send_signal(signal.SIGINT)
                term.wait()

    log = open(args.log, "w") if args.log else None
    out = Tee(sys.stdout, log) if log else sys.stdout
    master, slave = pty.openpty()
    term = subprocess.Popen(litex_term(args, images), stdin=slave, stdout=slave, stderr=slave,
                            env=venv_env(), start_new_session=True)
    os.close(slave)
    con = fd_console(master, out, alive=lambda: term.poll() is None)
    time.sleep(1)
    try:
        load(args)
        con.expect(args.until, args.timeout)
        for line in args.send:
            con.type(line)
            con.expect(args.prompt, args.timeout)
        print("\n[board] done", flush=True)
        return 0
    except Fail as e:
        print("\n[board] FAIL: {}".format(e), flush=True)
        return 1
    finally:
        os.killpg(term.pid, signal.SIGTERM)
        term.wait()
        if log:
            log.close()

def prompt(args):
    """Load with the port open, and stop the BIOS's boot sequence (Q) at
    "Press Q or ESC": the BIOS waits at its prompt."""
    con = serial_console(args.port, sys.stdout, args.speed)
    load(args)
    con.expect(r"Press Q or ESC", 120)
    con.send(b"Q")
    con.expect(re.escape(PROMPT), 60)
    print("\n[board] the BIOS is at its prompt", flush=True)
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["load", "prompt", "console", "boot", "sdboot"])
    parser.add_argument("--bitstream", default=DEFAULT_BITSTREAM,
        help="the snapshot directory (gateware/qmtech_wukong.bit, software/, csr.json)")
    parser.add_argument("--port",      default="/dev/ttyUSB0")
    parser.add_argument("--speed",     default=1000000, type=int, help="the UART's baud rate (the gateware's)")
    parser.add_argument("--out-dir",   default=os.path.join(HOME, "LITEX-68K", "board-work"),
        help="where litexboot, the images and Vivado's log go")
    boot = parser.add_argument_group("boot")
    boot.add_argument("--kernel",  help="the kernel's ELF (litexboot loads its segments at main RAM + p_paddr)")
    boot.add_argument("--initrd",  help="an initial ramdisk, passed as BI_RAMDISK")
    boot.add_argument("--cmdline", default="console=liteuart0", help="BI_COMMAND_LINE")
    boot.add_argument("--images",  help="an images.json made already, instead of --kernel")
    boot.add_argument("--dtc",     help="dtc for the SoC's device tree (default: on PATH, or buildroot's)")
    boot.add_argument("--no-dtb",  action="store_true", help="no device tree (no BI_LITEX_DTB)")
    auto = parser.add_argument_group("unattended (boot, sdboot)")
    auto.add_argument("--until",   help="stop once this regex has appeared on the console")
    auto.add_argument("--send",    action="append", default=[], help="then type this line (repeatable)")
    auto.add_argument("--prompt",  default=r"# $", help="the regex each --send line waits for afterwards")
    auto.add_argument("--timeout", default=600, type=float, help="seconds for --until and each --send")
    auto.add_argument("--log",     help="also write the console here")
    args = parser.parse_args()
    args.bitstream = os.path.abspath(args.bitstream)
    args.out_dir = os.path.abspath(args.out_dir)

    if args.command == "load":
        load(args)
        return 0
    if args.command == "prompt":
        return prompt(args)
    if args.command == "console":
        return subprocess.call(litex_term(args), env=venv_env())
    if args.command == "sdboot":
        return run_with_term(args)
    # boot
    if args.images:
        images = os.path.abspath(args.images)
    elif args.kernel:
        args.kernel = os.path.abspath(args.kernel)
        images = make_images(args)
    else:
        parser.error("boot needs --kernel or --images")
    return run_with_term(args, images)

if __name__ == "__main__":
    sys.exit(main())
