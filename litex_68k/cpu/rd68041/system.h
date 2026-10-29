// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

#ifndef __SYSTEM_H
#define __SYSTEM_H

/* The cache control register, CACR (UM 4.2): the MC68040 has only the two
 * enables; its caches are cleared and pushed by CINV and CPUSH (UM 4.2). MOVEC
 * and those are privileged: everything here runs in supervisor mode, as the
 * BIOS does. */
#define CACR_DE  0x80000000 /* enable data cache */
#define CACR_IE  0x00008000 /* enable instruction cache */

/* The transparent translation registers (UM 3.1.3), which give the I/O region
 * its cache mode while translation is off (crt0.S): base and mask of A31-A24,
 * enable, the S field (1x: either mode), the cache mode. */
#define TTR_E       0x8000
#define TTR_S_ANY   0x4000
#define TTR_CM_WT   0x0000  /* cachable, write-through */
#define TTR_CM_CB   0x0020  /* cachable, copyback */
#define TTR_CM_SER  0x0040  /* noncachable, serialized */
#define TTR_CM_NC   0x0060  /* noncachable */
/* $8000_0000-$FFFF_FFFF: the I/O region of core.py (io_regions). */
#define DTT_IO      (0x807F0000 + TTR_E + TTR_S_ANY + TTR_CM_SER)
#define TTR_U01     0x0100  /* U1-U0 = 01: on a two-processor board, the other one snoops (SC 01) */

/* "+smp" (core.py): the two-processor board's registers (litex_68k/m68040_smp.py,
 * doc/l18-smp.md), long words, in the I/O region. smp.h has C for them. */
#define SMP_BASE        0xFFFF0000
#define SMP_WHOAMI      (SMP_BASE + 0x00)   /* R: this processor, 0 or 1 */
#define SMP_IPI_SET     (SMP_BASE + 0x04)   /* W: interrupt CPU n (level 5, vector 29) */
#define SMP_IPI_ACK     (SMP_BASE + 0x08)   /* W: acknowledge one's own */
#define SMP_RELEASE     (SMP_BASE + 0x0C)   /* W: non-zero lets CPU 1 out of reset, 0 holds it */
#define SMP_BOOT_PC     (SMP_BASE + 0x10)   /* RW: where crt0 sends CPU 1 (0: it waits) */
#define SMP_BOOT_SP     (SMP_BASE + 0x14)   /* RW: and its stack */
#define SMP_NCPUS       (SMP_BASE + 0x18)   /* R: 2 */
#define SMP_STATS       (SMP_BASE + 0x20)   /* R: the bus counters; a write here clears them */
#define SMP_IPI_VECTOR  29

#ifndef __ASSEMBLER__

#ifdef __cplusplus
extern "C" {
#endif

static inline unsigned long get_cacr(void)
{
	unsigned long cacr;
	__asm__ __volatile__ ("movec %%cacr, %0" : "=d" (cacr));
	return cacr;
}

static inline void set_cacr(unsigned long cacr)
{
	__asm__ __volatile__ ("movec %0, %%cacr" : : "d" (cacr) : "memory");
}

/* The instruction cache holds no dirty data: invalidating it is the flush. */
__attribute__((unused)) static void flush_cpu_icache(void)
{
	__asm__ __volatile__ ("cinva %%ic" : : : "memory");
}

/* Lines in copyback mode may be dirty: CPUSHA writes them out and invalidates
 * every line (UM 4.2), so that the next reads go to memory, which is what a
 * flush is for, before and after DMA alike. */
__attribute__((unused)) static void flush_cpu_dcache(void)
{
	__asm__ __volatile__ ("cpusha %%dc" : : : "memory");
}

void flush_l2_cache(void);

void busy_wait(unsigned int ms);
void busy_wait_us(unsigned int us);

#ifdef __cplusplus
}
#endif

#endif /* ! __ASSEMBLER__ */

#endif /* __SYSTEM_H */
