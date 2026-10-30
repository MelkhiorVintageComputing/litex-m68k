/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */

/* The Linux/m68k boot information records litexboot writes: the numbers of
 * Linux's arch/m68k/include/uapi/asm/bootinfo.h, and of bootinfo-litex.h,
 * which patches/linux adds. */
#ifndef LITEXBOOT_BOOTINFO_H
#define LITEXBOOT_BOOTINFO_H

#define BI_LAST          0x0000
#define BI_MACHTYPE      0x0001
#define BI_CPUTYPE       0x0002
#define BI_FPUTYPE       0x0003
#define BI_MMUTYPE       0x0004
#define BI_MEMCHUNK      0x0005
#define BI_RAMDISK       0x0006
#define BI_COMMAND_LINE  0x0007

#define BI_LITEX_SYS_CLK 0x8000
#define BI_LITEX_IRQCTRL 0x8001
#define BI_LITEX_CTRL    0x8002
#define BI_LITEX_UART    0x8003
#define BI_LITEX_TIMER0  0x8004
#define BI_LITEX_DTB     0x8005
/* "+smp" only (two RD68041s, doc/l18-smp.md); not in patches/linux, whose
 * kernel is uniprocessor. */
#define BI_LITEX_SMP     0x8006         /* base of the board's registers, NCPUS */
#define BI_LITEX_TIMER1  0x8007         /* timer1's CSR base, its interrupt line */

#define MACH_LITEX       15
#define CPU_68030        (1 << 1)
#define CPU_68040        (1 << 2)
#define FPU_68881        (1 << 0)
#define FPU_68040        (1 << 2)       /* the MC68040's own */
#define MMU_68030        (1 << 1)
#define MMU_68040        (1 << 2)

#endif
