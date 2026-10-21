#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L7: Linux on RD68031 in a LiteX SoC, to a shell, and a command set
(doc/l7-linux.md).

    l7_test.py                          the simulation (litex_sim)
    l7_test.py --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong_30m_fpu
                                        a board, its BIOS at the prompt
    l7_test.py --login --linux-dir ~/LITEX-68K/build/buildroot-fpu/images
                                        L8: buildroot's images, a getty to log in
                                        on (doc/l8-buildroot.md)

1. The kernel and initramfs are linux/build.sh's (--linux-dir). litexboot
   (sw/litexboot) is built against the SoC's software, and images.py places
   them all in main RAM.
2. Simulation: the SoC is built with main RAM holding the images
   (--ram-init), and the BIOS jumps to litexboot. litex_sim runs everything at
   1 MHz, so the kernel is told 100 MHz (--sys-clk): its ticks then leave the
   CPU time to work, and its clock runs slow. Board: litex_term uploads the
   images (--images) at --speed, and the BIOS jumps to litexboot.
3. litexboot's report, the kernel's boot, /init's report, then commands typed
   at the shell, each answer checked, as RD68031's rd-virt does
   (Inputs/RD68031/sim/virt/linux/commands.script).

Prints the exchanges, then "L7 PASS" or "L7 FAIL: <why>"; exit status 0 on pass.
"""

import argparse
import os
import pty
import re
import signal
import subprocess
import sys
import time

from simtest import (PROMPT, TOP, Fail, check, fd_console, serial_console, sim_command,
                     socket_console, start, stop, venv_env, wait_listening)

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL_PROMPT = "l7# "

# (command, regex its answer must match)
COMMANDS = [
    ("echo sum $((6*7)) product $((123*457))", r"sum 42 product 56211"),
    ("uname -m -r",                            r"7\.2\.9-litex m68k"),
    ("grep -E '^(CPU|MMU|FPU):' /proc/cpuinfo", r"CPU:\s+68030\nMMU:\s+68030\nFPU:\s+68881"),
    ("seq 1 200 | md5sum",                     r"304f7b9574921ad21a2bc72f200483d7"),
    ("echo hello > /f; wc -c < /f; rm /f",     r"^6$"),
    ("awk 'BEGIN{printf \"%.6f\\n\", atan2(1,1)*4}'", r"3\.141593"),
    ("for i in 1 2 3; do echo loop$i; done | sort -r | tr '\\n' ' '", r"loop3 loop2 loop1"),
    ("cat /proc/cmdline",                      r"console=liteuart0"),
    # Two processes using the FPU, preempting each other: the FPU saved and
    # restored by the scheduler (RD68884 7a188db, doc/l7-linux.md).
    ("S='BEGIN{s=0;for(i=1;i<=3000;i++)s+=sqrt(i);printf \"%.6f\\n\",s}'; "
     "awk \"$S\" & awk \"$S\"; wait",            r"109571\.690503\n109571\.690503"),
]

# --login (buildroot, doc/l8-buildroot.md): what only its userspace has. glibc
# dynamically linked, and Lua's doubles through glibc's libm, which uses the
# FPU's own transcendentals (the values are the host's).
LOGIN_COMMANDS = [
    ("grep ^NAME= /etc/os-release",            r'NAME=Buildroot'),
    ("ls /lib/libc.so.6",                      r"^/lib/libc\.so\.6$"),
    ("lua -e 'print(string.format(\"%.12f\", math.sin(1)+math.exp(1)+math.log(10)+math.atan(0.5)+math.sqrt(2)))'",
                                               r"7\.740199077635"),
    ("lua -e 's=0 for i=1,2000 do s=s+math.sin(i)*math.cos(i/3) end print(string.format(\"%.9f\", s))'",
                                               r"1\.487327306"),
]

def run(cmd, cwd, log, what):
    with open(log, "w") as f:
        if subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=venv_env()):
            raise Fail("{} failed (see {})".format(what, log))

def interrupts(con):
    """/proc/interrupts' counts, by name."""
    out = con.command("cat /proc/interrupts", until=SHELL_PROMPT)
    return {m.group(2): int(m.group(1)) for m in re.finditer(r"^\s*\d+:\s+(\d+)\s+\S+\s+(\S+)", out, re.M)}

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    parser = argparse.ArgumentParser(description="L7: Linux on RD68031, to a shell.")
    parser.add_argument("--variant",   default="standard+fpu", help="CPU variant.")
    parser.add_argument("--port",      default=2432, type=int, help="Simulation: TCP port for the UART.")
    parser.add_argument("--main-ram",  default=0x2000000, type=lambda x: int(x, 0),
        help="Simulation: main RAM size (block RAM).")
    parser.add_argument("--sys-clk",   default=None, type=float,
        help="The sys clock the kernel is told (simulation default: 100e6).")
    parser.add_argument("--linux-dir", default=os.path.join(TOP, "build", "linux"),
        help="linux/build.sh's output: vmlinux, initramfs.cpio.gz.")
    parser.add_argument("--build-dir", default=None,
        help="The SoC's build directory (default: build/<variant>/l7, a simulation).")
    parser.add_argument("--serial",    default=None,
        help="A board's serial port instead of the simulation: the board runs the SoC "
             "built in --build-dir, its BIOS at the prompt.")
    parser.add_argument("--speed",     default=115200, type=int, help="Board: the UART's baud rate.")
    parser.add_argument("--try", dest="tries", action="append", default=[], metavar="CMD",
        help="Instead of the command set: run CMD (repeatable), print its answer, stop "
             "at the first that does not come back within --try-timeout. For bring-up.")
    parser.add_argument("--try-timeout", default=120, type=float)
    parser.add_argument("--login", action="store_true",
        help="The userspace logs in on a getty (buildroot's) instead of linux/build.sh's "
             "/init report and shell: log in as root, and add LOGIN_COMMANDS.")
    parser.add_argument("--command-timeout", default=1800, type=float,
        help="Seconds for each command of the set. In the simulation, the two awk take 850 s on L7's BusyBox, longer on buildroot's.")
    parser.add_argument("--boot-timeout", default=3600, type=float,
        help="Seconds from litexboot to the shell (the simulation takes tens of minutes).")
    args = parser.parse_args()
    sys_clk = args.sys_clk if args.sys_clk is not None else (0 if args.serial else 100e6)

    build = os.path.abspath(args.build_dir or os.path.join(HERE, "build", args.variant, "l7"))
    out = os.path.join(build, "l7")
    os.makedirs(out, exist_ok=True)
    sim = term = None
    try:
        # 1. litexboot and the images, against the SoC's software.
        if not args.serial:
            run(sim_command(args.variant, build, 0, ["--no-compile-gateware"], main_ram=args.main_ram),
                build, os.path.join(out, "software.log"), "the SoC's software")
        lb = os.path.join(out, "litexboot")
        os.makedirs(lb, exist_ok=True)
        run(["make", "-f", os.path.join(TOP, "sw", "litexboot", "Makefile"), "BUILD_DIR=" + build],
            lb, os.path.join(out, "litexboot.log"), "litexboot")
        images = os.path.join(out, "images")
        run([sys.executable, os.path.join(TOP, "sw", "litexboot", "images.py"), "--build-dir", build,
             "--vmlinux", os.path.join(args.linux_dir, "vmlinux"),
             "--initramfs", os.path.join(args.linux_dir, "initramfs.cpio.gz"),
             "--loader", os.path.join(lb, "litexboot.bin"), "--sys-clk", str(sys_clk),
             "--out-dir", images], out, os.path.join(out, "images.log"), "images.py")
        images_json = os.path.join(images, "images.json")

        # 2. To litexboot.
        t0 = time.time()
        if args.serial:
            master, slave = pty.openpty()
            term = subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "litex_term"),
                args.serial, "--speed", str(args.speed), "--images", images_json],
                stdin=slave, stdout=slave, stderr=slave, env=venv_env(), start_new_session=True)
            os.close(slave)
            con = fd_console(master, sys.stdout, alive=lambda: term.poll() is None)
            # The BIOS's own boot sequence may ask litex_term for the images
            # (when it reaches serial boot after litex_term is there); if it is
            # at its prompt already, serialboot. A newline only on a silent line.
            got = ""
            for _ in range(10):
                try:
                    got += con.expect(r"Booting from serial|" + re.escape(PROMPT), 10)
                    break
                except Fail:
                    con.type("")
            else:
                raise Fail("no BIOS on the serial port")
            if got.endswith(PROMPT):
                con.type("serialboot")
            got = con.expect(r"Executing booted program", 3600)
            check(re.search(r"Upload complete", got), "litex_term did not upload the images")
            print("\n[l7_test] upload: {:.0f} s".format(time.time() - t0), flush=True)
        else:
            sim = start(sim_command(args.variant, build, args.port, ["--ram-init", images_json],
                main_ram=args.main_ram), open(os.path.join(build, "sim.log"), "w"))
            wait_listening(args.port, sim)
            con = socket_console(args.port, sys.stdout, sim)

        # 3. litexboot, the kernel, /init.
        got = con.expect(r"litexboot: entry 0x[0-9a-f]+", 600)
        check("FPU 68881" in got if "fpu" in args.variant else "FPU none" in got,
              "litexboot: wrong FPU for the {} variant".format(args.variant))
        t1 = time.time()
        if args.login:
            got = con.expect(r"login: ", args.boot_timeout)
            print("\n[l7_test] litexboot to the login: {:.0f} s".format(time.time() - t1), flush=True)
            check("Run /init as init process" in got, "the kernel did not run /init")
            con.type("root")
            con.expect(r"# ", 300)
        else:
            got = con.expect(r"litex: shell", args.boot_timeout)
            print("\n[l7_test] litexboot to the shell: {:.0f} s".format(time.time() - t1), flush=True)
            check("Run /init as init process" in got, "the kernel did not run /init")
            check(re.search(r"liteuart.*timer0|timer0[\s\S]*liteuart|liteuart[\s\S]*timer0", got),
                  "/init: no liteuart and timer0 in /proc/interrupts")
            con.expect(r"# ", 300)
        # The prompt at the start of a line: the echoed PS1= line has it too.
        con.type("PS1='{}'".format(SHELL_PROMPT))
        con.expect(r"\n" + re.escape(SHELL_PROMPT), 300)
        # Kernel messages off the console, so that none lands inside an answer
        # ("random: crng init done" did, on the board).
        con.command("dmesg -n 1", timeout=300, until=SHELL_PROMPT)

        if args.tries:
            for cmd in args.tries:
                ans = con.command(cmd, timeout=args.try_timeout, until=SHELL_PROMPT)
                print("\n[l7_test] {!r} -> {!r}".format(cmd, ans), flush=True)
            print("\nL7 TRIED", flush=True)
            return 0

        # 4. The command set.
        before = interrupts(con)
        for cmd, want in COMMANDS + (LOGIN_COMMANDS if args.login else []):
            t = time.time()
            ans = con.command(cmd, timeout=args.command_timeout, until=SHELL_PROMPT)
            print("\n[l7_test] {:.0f} s".format(time.time() - t), flush=True)
            check(re.search(want, ans.replace(SHELL_PROMPT, ""), re.M),
                  "{!r} answered {!r}, expected {!r}".format(cmd, ans, want))
        after = interrupts(con)
        for name in ("timer0", "liteuart"):
            check(name in before and after.get(name, 0) > before[name],
                  "/proc/interrupts: {} not counting ({} then {})".format(name, before.get(name), after.get(name)))

        print("\nL7 PASS", flush=True)
        return 0
    except Fail as e:
        print("\nL7 FAIL: {}".format(e))
        return 1
    finally:
        for p in (term, sim):
            if p is not None:
                stop(p)

if __name__ == "__main__":
    sys.exit(main())
