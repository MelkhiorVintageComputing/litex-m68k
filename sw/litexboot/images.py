#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The images litexboot boots Linux from, for one SoC (layout.h):

    images.py --build-dir <SoC build> --vmlinux V --initramfs I --loader B \\
              --cmdline "console=liteuart" [--dtc DTC] --out-dir D

writes, in D, copies of the vmlinux ELF (stripped), the initramfs and litexboot.bin, a
params.bin saying where they are and the command line, and images.json, which
both litex_term --images (the board) and litex_sim --ram-init (the simulation)
take: {file: address}, litexboot last, since both jump to the last one.

With --dtc, also the SoC's device tree (L10, doc/l10-devicetree.md): soc.dts from
the build's csr.json by LiteX's litex_json2dts_linux (patches/litex: its m68k
branch), compiled to soc.dtb by DTC, which litexboot passes to the kernel."""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess

PARAMS_MAGIC   = 0x4c584250
PARAMS_VERSION = 2
PARAMS_CMDLINE = 256

def main_ram(build_dir):
    mem = open(os.path.join(build_dir, "software", "include", "generated", "mem.h")).read()
    base = int(re.search(r"#define MAIN_RAM_BASE (0x[0-9a-fA-F]+)", mem).group(1), 16)
    size = int(re.search(r"#define MAIN_RAM_SIZE (0x[0-9a-fA-F]+)", mem).group(1), 16)
    return base, size

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", required=True, help="The SoC's build directory (its generated mem.h).")
    parser.add_argument("--vmlinux",   required=True)
    parser.add_argument("--initramfs", default=None)
    parser.add_argument("--loader",    required=True, help="litexboot.bin, built for this SoC.")
    parser.add_argument("--cmdline",   default="console=liteuart0")
    parser.add_argument("--sys-clk",   default=0, type=float,
        help="The sys clock frequency told to the kernel (default: the SoC's). In "
             "litex_sim, everything runs at 1 MHz, and a tick every 10,000 CPU clocks "
             "(HZ=100) leaves the CPU no time: say 100e6.")
    parser.add_argument("--dtc",       default=None,
        help="dtc (buildroot's host-dtc): make and load the SoC's device tree.")
    parser.add_argument("--out-dir",   required=True)
    args = parser.parse_args()

    base, size = main_ram(args.build_dir)
    elf_addr    = base + size // 2
    initrd_addr = base + size // 4 * 3
    params_addr = base + size - 0x200000
    dtb_addr    = base + size - 0x180000
    loader_addr = base + size - 0x100000
    os.makedirs(args.out_dir, exist_ok=True)

    def place(src, name, addr, limit):
        dst = os.path.join(args.out_dir, name)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        n = os.path.getsize(dst)
        assert addr + n <= limit, "{} ({} bytes) does not fit below 0x{:08x}".format(name, n, limit)
        return n

    images = {}
    # Stripped: the symbols are not loaded, only uploaded.
    stripped = os.path.join(args.out_dir, "vmlinux")
    subprocess.check_call(["m68k-linux-gnu-objcopy", "--strip-all", args.vmlinux, stripped])
    elf_size = place(stripped, "vmlinux", elf_addr, initrd_addr)
    images["vmlinux"] = "0x{:08x}".format(elf_addr)
    initrd_size = 0
    if args.initramfs:
        initrd_size = place(args.initramfs, "initramfs.cpio.gz", initrd_addr, params_addr)
        images["initramfs.cpio.gz"] = "0x{:08x}".format(initrd_addr)
    dtb_size = 0
    if args.dtc:
        from litex.tools.litex_json2dts_linux import generate_dts
        dts = os.path.join(args.out_dir, "soc.dts")
        dtb = os.path.join(args.out_dir, "soc.dtb")
        with open(os.path.join(args.build_dir, "csr.json")) as f:
            d = json.load(f)
        with open(dts, "w") as f:
            # The kernel takes its initrd and command line from the bootinfo,
            # not /chosen: none there, so that the tree says nothing else.
            f.write(generate_dts(d, initrd="disabled"))
        subprocess.check_call([args.dtc, "-q", "-I", "dts", "-O", "dtb", "-o", dtb, dts])
        dtb_size = place(dtb, "soc.dtb", dtb_addr, base + size - 0x100000)
        images["soc.dtb"] = "0x{:08x}".format(dtb_addr)
    cmdline = args.cmdline.encode()
    assert len(cmdline) < PARAMS_CMDLINE, "command line too long"
    with open(os.path.join(args.out_dir, "params.bin"), "wb") as f:
        f.write(struct.pack(">9L", PARAMS_MAGIC, PARAMS_VERSION, elf_addr, elf_size,
                            initrd_addr, initrd_size, int(args.sys_clk), dtb_addr, dtb_size))
        f.write(cmdline.ljust(PARAMS_CMDLINE, b"\0"))
    images["params.bin"] = "0x{:08x}".format(params_addr)
    place(args.loader, "litexboot.bin", loader_addr, base + size - 0x10000)
    images["litexboot.bin"] = "0x{:08x}".format(loader_addr)
    with open(os.path.join(args.out_dir, "images.json"), "w") as f:
        json.dump(images, f, indent=4)
    print("main RAM 0x{:08x}+0x{:x}: {}".format(base, size, images))

if __name__ == "__main__":
    main()
