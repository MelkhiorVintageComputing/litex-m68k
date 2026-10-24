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
import hashlib
import os
import pty
import shutil
import socket
import tempfile
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
    ("grep -E '^(CPU|MMU|FPU):' /proc/cpuinfo", r"CPU:\s+68030\nMMU:\s+68030\nFPU:\s+{fpu}"),
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

# --login (buildroot, doc/l8-buildroot.md, doc/l9-nofpu.md): what only its
# userspace has. Its C library, dynamically linked: glibc with the FPU, musl's
# soft-float ABI without. Lua's doubles: through glibc's libm, which uses the
# FPU's own transcendentals, or in software (the values are the host's).
LOGIN_COMMANDS = [
    ("grep ^NAME= /etc/os-release",            r'NAME=Buildroot'),
    ("ls {libc}",                              r"^{libc_re}$"),
    # Decimal conversion, which a C library may do in long double: musl's
    # printf without an FPU was wrong until patches/gcc 0003.
    ("awk 'BEGIN{printf \"%.6f %g %.17g\\n\", 2/3, 1e100, 0.1}'",
                                               r"0\.666667 1e\+100 0\.10000000000000001"),
    ("lua -e 'print(string.format(\"%.12f\", math.sin(1)+math.exp(1)+math.log(10)+math.atan(0.5)+math.sqrt(2)))'",
                                               r"7\.740199077635"),
    ("lua -e 's=0 for i=1,2000 do s=s+math.sin(i)*math.cos(i/3) end print(string.format(\"%.9f\", s))'",
                                               r"1\.487327306"),
]

# A device tree (L10, doc/l10-devicetree.md): the kernel unflattened
# litexboot's, and LiteUART was made from its node (named after its address),
# not from the bootinfo (named "liteuart").
DT_COMMANDS = [
    ("tr '\\0' ' ' < /sys/firmware/devicetree/base/compatible; echo", r"litex,\S+ litex,soc"),
    ("ls /sys/bus/platform/drivers/liteuart/",  r"[0-9a-f]+\.serial"),
]

# --sdcard, in the simulation: litex_sim's SD card is litesdcard's emulator,
# one 512-byte block whatever the address, its words 0 to 127 (big-endian).
SIM_SD_COMMANDS = [
    ("ls /sys/block/",                         r"mmcblk0"),
    ("dd if=/dev/mmcblk0 bs=512 count=1 skip=8 2>/dev/null | md5sum", r"^{sd_block_md5}"),
]

def host_ip():
    """This host's address on its default route (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    finally:
        s.close()

def shell(con, timeout):
    """sh(cmd, want, what[, t]): run cmd, check its answer matches want (a
    regular expression, multi-line), return the match."""
    def sh(cmd, want, what, t=timeout):
        t0 = time.time()
        ans = con.command(cmd, timeout=t, until=SHELL_PROMPT).replace(SHELL_PROMPT, "")
        print("\n[l7_test] {:.0f} s".format(time.time() - t0), flush=True)
        m = re.search(want, ans, re.M)
        check(m, "{}: {!r} answered {!r}".format(what, cmd, ans))
        return m
    return sh

class HTTPServer:
    """This host serving directory `root` on host:port (python3 -m http.server)."""
    def __init__(self, root, host, port=8642):
        self.url = "http://{}:{}".format(host, port)
        self.proc = subprocess.Popen([sys.executable, "-m", "http.server", "--bind", host, str(port)],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            try:
                socket.create_connection((host, port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
    def __enter__(self):
        return self
    def __exit__(self, *_):
        stop(self.proc)

def io_checks(con, timeout, host, size=4 << 20, sd=True):
    """--io, a board with Ethernet and an SD card (L10, doc/l10-devicetree.md):
    a DHCP lease, ping and an HTTP download from this host (its md5), then the
    SD card overwritten: an ext2 filesystem, a file of random data written,
    the card unmounted and mounted again, the file read back (its md5), and the
    card's raw read rate."""
    sh = shell(con, timeout)

    # Ethernet: the file is served by this host, from a directory of its own.
    serve = tempfile.mkdtemp(prefix="l10-http-")
    blob = os.urandom(size)
    with open(os.path.join(serve, "blob"), "wb") as f:
        f.write(blob)
    try:
        with HTTPServer(serve, host) as http:
            sh("ip link set eth0 up; udhcpc -i eth0 -n -q -t 10", r"lease of (\S+) obtained",
               "Ethernet: no DHCP lease", 300)
            sh("ping -c 3 {}".format(host), r"3 packets transmitted, 3 (packets )?received", "ping")
            t0 = time.time()
            sh("wget -q -O - {}/blob | md5sum".format(http.url),
               "^" + hashlib.md5(blob).hexdigest(), "HTTP download")
            print("\n[l7_test] wget {} bytes: {:.0f} KB/s".format(size, size / 1024 / (time.time() - t0)),
                  flush=True)
    finally:
        shutil.rmtree(serve, ignore_errors=True)

    if not sd:
        return
    # The SD card: a 64 MiB filesystem at its start, whatever its size.
    sh("ls /sys/block/", r"mmcblk0", "SD card: no mmcblk0")
    sh("mkfs.ext2 -q /dev/mmcblk0 65536 && echo mkfs ok", r"^mkfs ok", "mkfs.ext2", 900)
    sh("mkdir -p /mnt/sd && mount /dev/mmcblk0 /mnt/sd && echo mounted", r"^mounted", "mount")
    m = sh("dd if=/dev/urandom of=/mnt/sd/r bs=64k count=64 2>/dev/null; md5sum /mnt/sd/r",
           r"^([0-9a-f]{32})", "write")
    md5 = m.group(1)
    # Unmounted and every clean cache dropped: read back from the card.
    sh("umount /mnt/sd && sync && echo 3 > /proc/sys/vm/drop_caches && echo dropped",
       r"^dropped", "umount")
    sh("mount /dev/mmcblk0 /mnt/sd && md5sum /mnt/sd/r", "^" + md5,
       "SD card: the file read back differs")
    sh("umount /mnt/sd && echo umounted", r"^umounted", "umount")
    sh("echo 3 > /proc/sys/vm/drop_caches && echo dropped", r"^dropped", "drop_caches")
    m = sh("time dd if=/dev/mmcblk0 of=/dev/null bs=64k count=128", r"real\s+(.+)", "SD read rate")
    print("\n[l7_test] SD card: 8 MiB read in {}".format(m.group(1)), flush=True)

# --sd-install's card (doc/l10-devicetree.md): p1 FAT, the BIOS's sdcardboot
# files (boot.json, images.json's format); p2 and p3 ext2, root filesystems.
SD_PARTITIONS = [("1", "+64M", "vfat"), ("2", "+256M", "ext2"), ("3", "+256M", "ext2")]
SD_ROOT = "/dev/mmcblk0p2"

def tree_manifest(root):
    """md5sum's lines for every regular file under root, as `find . -type f |
    sort | xargs md5sum` gives them on the board, and their md5."""
    lines = []
    for d, _, files in os.walk(root):
        for f in files:
            path = os.path.join(d, f)
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, "rb") as fh:
                    lines.append((("./" + os.path.relpath(path, root)),
                                  hashlib.md5(fh.read()).hexdigest()))
    text = "".join("{}  {}\n".format(md5, name) for name, md5 in sorted(lines))
    return len(lines), hashlib.md5(text.encode()).hexdigest()

def sd_install(con, timeout, host, boot_dir, roots):
    """--sd-install: the SD card partitioned and filled over Ethernet, every
    file checked by md5 (the root filesystems by a manifest of theirs), then
    checked again from the card, unmounted and its caches dropped.

    boot_dir: the files for p1 (its tree as is); roots: {partition: rootfs.cpio}."""
    sh = shell(con, timeout)
    stage = tempfile.mkdtemp(prefix="l10-sd-")
    try:
        # What is served: p1's files, and the root filesystems' cpio archives.
        boot = {}
        for d, _, files in os.walk(boot_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(d, f), boot_dir)
                with open(os.path.join(d, f), "rb") as fh:
                    boot[rel] = hashlib.md5(fh.read()).hexdigest()
        shutil.copytree(boot_dir, os.path.join(stage, "boot"))
        manifests = {}
        for part, cpio in roots.items():
            shutil.copyfile(cpio, os.path.join(stage, "root{}.cpio".format(part)))
            tree = os.path.join(stage, "tree{}".format(part))
            os.makedirs(tree)
            # Device nodes are not made, as a user: only regular files count.
            with open(cpio, "rb") as fh:
                subprocess.call(["cpio", "-idm", "--quiet", "--no-absolute-filenames"],
                                cwd=tree, stdin=fh, stderr=subprocess.DEVNULL)
            manifests[part] = tree_manifest(tree)

        with HTTPServer(stage, host) as http:
            sh("ip link set eth0 up; udhcpc -i eth0 -n -q -t 10", r"lease of (\S+) obtained",
               "Ethernet: no DHCP lease", 300)
            # A new DOS label, the partitions, p1 FAT32 (LBA).
            keys = "o\n" + "".join("n\np\n{}\n\n{}\n".format(n, size)
                                    for n, size, _ in SD_PARTITIONS) + "t\n1\nc\nw\n"
            sh("printf '{}' | fdisk /dev/mmcblk0 >/dev/null 2>&1; sleep 2; "
               "ls -1 /sys/block/mmcblk0/ | grep p".format(keys.replace("\n", "\\n")),
               r"mmcblk0p1[\s\S]*mmcblk0p2[\s\S]*mmcblk0p3", "fdisk", 300)
            sh("fdisk -l /dev/mmcblk0", r"mmcblk0p1 .* c Win95 FAT32", "fdisk -l")
            for n, _, fs in SD_PARTITIONS:
                mkfs = "mkfs.vfat -n BOOT" if fs == "vfat" else "mkfs.ext2 -q -L root{}".format(n)
                sh("{} /dev/mmcblk0p{} >/dev/null && echo mkfs ok".format(mkfs, n),
                   r"^mkfs ok", "mkfs p" + n, 900)
                sh("mkdir -p /mnt/p{0} && mount /dev/mmcblk0p{0} /mnt/p{0} && echo mounted".format(n),
                   r"^mounted", "mount p" + n)

            # p1: each file, then their md5s.
            t0 = time.time()
            for rel in sorted(boot):
                sh("mkdir -p $(dirname /mnt/p1/{0}) && wget -q -O /mnt/p1/{0} {1}/boot/{0} "
                   "&& echo got".format(rel, http.url), r"^got", "wget " + rel)
            def check_boot():
                for rel, md5 in sorted(boot.items()):
                    sh("md5sum /mnt/p1/{}".format(rel), "^" + md5, "p1: " + rel)
            check_boot()
            # The root filesystems: unpacked from the HTTP stream.
            for part in roots:
                sh("cd /mnt/p{0} && wget -q -O - {1}/root{0}.cpio | cpio -idm 2>&1 | tail -1; cd /"
                   .format(part, http.url), r"blocks", "cpio p" + part, 1800)
            print("\n[l7_test] SD card filled in {:.0f} s".format(time.time() - t0), flush=True)
            def check_roots():
                for part, (n, md5) in manifests.items():
                    sh("cd /mnt/p{} && find . -type f | sort | xargs md5sum | md5sum; cd /"
                       .format(part), "^" + md5, "p{}: the {} files' manifest".format(part, n), 1800)
            check_roots()

            # Again from the card itself.
            sh("cd /; sync; umount /mnt/p1 /mnt/p2 /mnt/p3 && echo 3 > /proc/sys/vm/drop_caches "
               "&& echo dropped", r"^dropped", "umount")
            sh("for n in 1 2 3; do mount -o ro /dev/mmcblk0p$n /mnt/p$n; done; echo mounted",
               r"^mounted", "mount ro")
            check_boot()
            check_roots()
            sh("cd /; umount /mnt/p1 /mnt/p2 /mnt/p3 && echo umounted", r"^umounted", "umount")
    finally:
        shutil.rmtree(stage, ignore_errors=True)

def run(cmd, cwd, log, what):
    with open(log, "w") as f:
        if subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=venv_env()):
            raise Fail("{} failed (see {})".format(what, log))

def interrupts(con):
    """/proc/interrupts' counts, by name. With a device tree, the controller's
    domain adds the line (hwirq) after the chip's name."""
    out = con.command("cat /proc/interrupts", until=SHELL_PROMPT)
    return {m.group(2): int(m.group(1))
            for m in re.finditer(r"^\s*\d+:\s+(\d+)\s+\S+\s+(?:\d+\s+)?(\S+)", out, re.M)}

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
    parser.add_argument("--command-timeout", default=None, type=float,
        help="Seconds for each command of the set (default 1800, 7200 without the FPU). "
             "In the simulation, the two awk take 850 s on L7's BusyBox, longer on buildroot's; "
             "without the FPU, Lua's 2000-term loop takes 2800 s.")
    parser.add_argument("--dtc",       default=None,
        help="dtc, to give the kernel the SoC's device tree (L10; images.py --dtc). "
             "Default: buildroot's host-dtc beside --linux-dir (<out>/host/bin/dtc) if there.")
    parser.add_argument("--no-dtb",    action="store_true", help="No device tree, even with a dtc.")
    parser.add_argument("--sdcard",    action="store_true",
        help="Simulation: litex_sim's SD card (its emulator), and SIM_SD_COMMANDS.")
    parser.add_argument("--io",        action="store_true",
        help="Board: the Ethernet and SD card checks (io_checks; the SD card is overwritten).")
    parser.add_argument("--no-sd",     action="store_true",
        help="--io: Ethernet only; the SD card is not touched (it holds something to keep).")
    parser.add_argument("--host-ip",   default=None,
        help="--io: this host's address the board reaches (default: on its default route).")
    parser.add_argument("--sd-install", action="store_true",
        help="Board: after the command set, partition the SD card and fill it over "
             "Ethernet for --sd-boot (sd_install; the card is overwritten): the kernel, "
             "litexboot and the device tree on a FAT p1 (boot.json for the BIOS's "
             "sdcardboot), --linux-dir's root filesystem on p2, --nofpu-dir's on p3.")
    parser.add_argument("--nofpu-dir", default=os.path.join(os.path.expanduser("~"), "LITEX-68K",
        "build", "buildroot-nofpu", "images"),
        help="--sd-install: the FPU-less buildroot's images (its kernel in p1's nofpu/, its root on p3).")
    parser.add_argument("--sd-boot",   action="store_true",
        help="Board: no upload; the BIOS boots from the SD card (--sd-install's), its "
             "root filesystem there.")
    parser.add_argument("--load",      default=None, metavar="BITSTREAM",
        help="--sd-boot: load BITSTREAM (Vivado, JTAG) once the serial port is open, so "
             "that the BIOS's whole boot is seen; without it, sdcardboot at its prompt.")
    parser.add_argument("--boot-timeout", default=3600, type=float,
        help="Seconds from litexboot to the shell (the simulation takes tens of minutes).")
    args = parser.parse_args()
    if args.command_timeout is None:
        args.command_timeout = 1800 if "fpu" in args.variant else 7200
    sys_clk = args.sys_clk if args.sys_clk is not None else (0 if args.serial else 100e6)
    dtc = args.dtc
    if dtc is None:
        dtc = os.path.join(os.path.dirname(os.path.abspath(args.linux_dir)), "host", "bin", "dtc")
        dtc = dtc if os.path.exists(dtc) else None
    if args.no_dtb:
        dtc = None
    sim_extra = ["--with-sdcard"] if args.sdcard else []

    build = os.path.abspath(args.build_dir or os.path.join(HERE, "build", args.variant, "l7"))
    out = os.path.join(build, "l7")
    os.makedirs(out, exist_ok=True)
    sim = term = None
    try:
        # 1. litexboot and the images, against the SoC's software.
        if not args.serial:
            run(sim_command(args.variant, build, 0, ["--no-compile-gateware"] + sim_extra,
                            main_ram=args.main_ram),
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
             *(["--dtc", dtc] if dtc else []),
             "--out-dir", images], out, os.path.join(out, "images.log"), "images.py")
        images_json = os.path.join(images, "images.json")
        if args.sd_install:
            # The SD card's p1: no initramfs, the root on p2; boot.json for the
            # BIOS is images.json. The FPU-less kernel too, for its own SoC.
            sd_boot_dir = os.path.join(out, "sd-boot")
            shutil.rmtree(sd_boot_dir, ignore_errors=True)
            run([sys.executable, os.path.join(TOP, "sw", "litexboot", "images.py"), "--build-dir", build,
                 "--vmlinux", os.path.join(args.linux_dir, "vmlinux"),
                 "--loader", os.path.join(lb, "litexboot.bin"), "--sys-clk", str(sys_clk),
                 "--cmdline", "console=liteuart0 root={} rootwait".format(SD_ROOT),
                 *(["--dtc", dtc] if dtc else []),
                 "--out-dir", sd_boot_dir], out, os.path.join(out, "images-sd.log"), "images.py (SD)")
            os.rename(os.path.join(sd_boot_dir, "images.json"), os.path.join(sd_boot_dir, "boot.json"))
            os.remove(os.path.join(sd_boot_dir, "soc.dts"))
            os.makedirs(os.path.join(sd_boot_dir, "nofpu"))
            subprocess.check_call(["m68k-linux-gnu-objcopy", "--strip-all",
                os.path.join(args.nofpu_dir, "vmlinux"), os.path.join(sd_boot_dir, "nofpu", "vmlinux")])

        # 2. To litexboot.
        t0 = time.time()
        if args.serial and args.sd_boot:
            # The BIOS's boot sequence reaches the SD card once serial boot
            # finds no litex_term. Without --load, at its prompt: sdcardboot.
            con = serial_console(args.serial, sys.stdout, args.speed)
            if args.load:
                sys.path.insert(0, os.path.join(TOP, "targets"))
                from qmtech_wukong import vivado_load
                vivado_load(args.load)
                t0 = time.time()
                con.expect(r"Booting from SDCard", 300)
            else:
                for _ in range(10):
                    try:
                        con.expect(re.escape(PROMPT), 10)
                        break
                    except Fail:
                        con.type("")
                else:
                    raise Fail("no BIOS prompt on the serial port")
                con.type("sdcardboot")
            got = con.expect(r"Executing booted program|SDCard boot failed", 3600)
            check("Executing" in got, "the BIOS's SD card boot failed")
            print("\n[l7_test] the BIOS's SD card boot: {:.0f} s".format(time.time() - t0), flush=True)
        elif args.serial:
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
            sim = start(sim_command(args.variant, build, args.port, ["--ram-init", images_json] + sim_extra,
                main_ram=args.main_ram), open(os.path.join(build, "sim.log"), "w"))
            wait_listening(args.port, sim)
            con = socket_console(args.port, sys.stdout, sim)

        # 3. litexboot, the kernel, /init.
        got = con.expect(r"litexboot: entry 0x[0-9a-f]+", 600)
        check("FPU 68881" in got if "fpu" in args.variant else "FPU none" in got,
              "litexboot: wrong FPU for the {} variant".format(args.variant))
        check(("litexboot: device tree" in got) == bool(dtc),
              "litexboot: device tree {}expected".format("" if dtc else "not "))
        t1 = time.time()
        if args.login:
            got = con.expect(r"login: ", args.boot_timeout)
            print("\n[l7_test] litexboot to the login: {:.0f} s".format(time.time() - t1), flush=True)
            # /init in an initramfs, /sbin/init on a disk (--sd-boot).
            check(re.search(r"Run /(sbin/)?init as init process", got), "the kernel did not run init")
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
        fpu = "fpu" in args.variant
        libc = "/lib/libc.so.6" if fpu else "/lib/ld-musl-m68k-sf.so.1"
        # The emulator's block: words 0 to 127, big-endian.
        sd_block_md5 = hashlib.md5(b"".join(i.to_bytes(4, "big") for i in range(128))).hexdigest()
        subst = dict(fpu="68881" if fpu else "none", libc=libc, libc_re=re.escape(libc),
                     sd_block_md5=sd_block_md5)
        commands = COMMANDS + (LOGIN_COMMANDS if args.login else []) + \
            (DT_COMMANDS if dtc else []) + (SIM_SD_COMMANDS if args.sdcard else [])
        for cmd, want in commands:
            cmd = cmd.replace("{libc}", libc)
            for k, v in subst.items():
                want = want.replace("{" + k + "}", v)
            t = time.time()
            ans = con.command(cmd, timeout=args.command_timeout, until=SHELL_PROMPT)
            print("\n[l7_test] {:.0f} s".format(time.time() - t), flush=True)
            check(re.search(want, ans.replace(SHELL_PROMPT, ""), re.M),
                  "{!r} answered {!r}, expected {!r}".format(cmd, ans, want))
        if args.sd_boot:
            sh = shell(con, args.command_timeout)
            sh("grep ' / ' /proc/mounts", r"^/dev/(root|mmcblk0p2) / ext2 rw", "root on the SD card")
            sh("cat /proc/cmdline", re.escape("root=" + SD_ROOT), "command line")
        if args.io:
            io_checks(con, args.command_timeout, args.host_ip or host_ip(), sd=not args.no_sd)
        if args.sd_install:
            sd_install(con, args.command_timeout, args.host_ip or host_ip(), sd_boot_dir,
                       {"2": os.path.join(args.linux_dir, "rootfs.cpio"),
                        "3": os.path.join(args.nofpu_dir, "rootfs.cpio")})
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
