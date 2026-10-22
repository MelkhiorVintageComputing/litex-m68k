/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */

/* Where litexboot's inputs are in main RAM: offsets from its base, for any
 * size (images.py writes the same layout for the host). The kernel goes at
 * the base: head.S maps virtual 0 to the start of the first memory chunk. */
#ifndef LITEXBOOT_LAYOUT_H
#define LITEXBOOT_LAYOUT_H

#define LAYOUT_ELF(base, size)      ((base) + (size) / 2)        /* vmlinux, as a file */
#define LAYOUT_INITRD(base, size)   ((base) + (size) / 4 * 3)    /* initramfs */
#define LAYOUT_PARAMS(base, size)   ((base) + (size) - 0x200000) /* struct params */
#define LAYOUT_DTB(base, size)      ((base) + (size) - 0x180000) /* the device tree, kept by the kernel */
#define LAYOUT_LOADER(base, size)   ((base) + (size) - 0x100000) /* litexboot; its stack above */

#define PARAMS_MAGIC    0x4c584250  /* "LXBP" */
#define PARAMS_VERSION  2
#define PARAMS_CMDLINE  256

/* Written by images.py, big-endian. */
struct params {
	unsigned long magic, version;
	unsigned long elf_addr, elf_size;
	unsigned long initrd_addr, initrd_size;   /* initrd_size 0: none */
	unsigned long sys_clk;  /* Hz told to the kernel; 0: the SoC's. A
				   simulation says more than it runs at, so
				   that ticks leave the CPU time to work */
	unsigned long dtb_addr, dtb_size;         /* dtb_size 0: none */
	char cmdline[PARAMS_CMDLINE];
};

#endif
