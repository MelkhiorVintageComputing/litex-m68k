// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

#ifndef __SMP_H
#define __SMP_H

/* "+smp" (core.py): the two-processor board's registers (system.h, SMP_*;
 * litex_68k/m68040_smp.py; doc/l18-smp.md). Nothing here is cached: the
 * registers are in the I/O region, and the board answers with TCI. */

#include <system.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SMP_REG(a) (*(volatile unsigned long *)(a))

/* This processor's number, 0 or 1. */
static inline int smp_whoami(void)
{
	return SMP_REG(SMP_WHOAMI);
}

/* Interrupt processor `cpu` (level 5, autovectored: vector 29), until it
 * acknowledges with smp_ipi_ack(). */
static inline void smp_ipi(int cpu)
{
	SMP_REG(SMP_IPI_SET) = cpu;
}

static inline void smp_ipi_ack(void)
{
	SMP_REG(SMP_IPI_ACK) = 0;
}

/* Start CPU 1 at `pc` with its stack at `sp`, in supervisor mode with
 * interrupts masked, VBR 0 (the ROM's vectors), MMU, caches and TTRs as reset
 * leaves them: crt0.S jumps there. Out of reset first if it was held there.
 * `pc` must not return. */
static inline void smp_start_cpu1(void (*pc)(void), void *sp)
{
	SMP_REG(SMP_BOOT_SP) = (unsigned long)sp;
	SMP_REG(SMP_BOOT_PC) = (unsigned long)pc;
	SMP_REG(SMP_RELEASE) = 1;
}

/* Put CPU 1 back in reset (it can then be started again). */
static inline void smp_hold_cpu1(void)
{
	SMP_REG(SMP_RELEASE) = 0;
	SMP_REG(SMP_BOOT_PC) = 0;
}

/* The bus counters, in the board's order (m68040_smp.py, STATS). */
enum smp_stat {
	SMP_STAT_CONTENTION, SMP_STAT_TURNAROUND, SMP_STAT_OWNER, SMP_STAT_ATOMICITY,
	SMP_STAT_INTERVENE, SMP_STAT_MAXWAIT, SMP_STAT_TS0, SMP_STAT_TS1,
	SMP_STAT_HANDOVERS, SMP_STAT_LOCKED, SMP_STAT_MIWAIT, SMP_NSTATS
};

static inline unsigned long smp_stat(enum smp_stat k)
{
	return SMP_REG(SMP_STATS + 4 * k);
}

static inline void smp_stats_clear(void)
{
	SMP_REG(SMP_STATS) = 0;
}

#ifdef __cplusplus
}
#endif

#endif /* __SMP_H */
