// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

#ifndef __IRQ_H
#define __IRQ_H

#ifdef __cplusplus
extern "C" {
#endif

#include <system.h>
#include <generated/csr.h>

/* Every LiteX interrupt line raises one interrupt priority level, autovectored
 * (core.py, IRQ_LEVEL; vector 24 + level, UM Table 8-1). Which lines are pending
 * and which may interrupt are two CSRs of the CPU; whether the CPU takes them is
 * the status register's interrupt mask. */
#define RD68041_IRQ_LEVEL 2

static inline unsigned int irq_getie(void)
{
	unsigned short sr;
	__asm__ __volatile__ ("move.w %%sr, %0" : "=d" (sr));
	return ((sr >> 8) & 7) < RD68041_IRQ_LEVEL;
}

static inline void irq_setie(unsigned int ie)
{
	if (ie)
		__asm__ __volatile__ ("andi.w #0xf8ff, %%sr" : : : "memory"); /* mask 0 */
	else
		__asm__ __volatile__ ("ori.w #0x0700, %%sr" : : : "memory");  /* mask 7 */
}

static inline unsigned int irq_getmask(void)
{
	return csr_read_simple(CSR_CPU_IRQ_MASK_ADDR);
}

static inline void irq_setmask(unsigned int mask)
{
	csr_write_simple(mask, CSR_CPU_IRQ_MASK_ADDR);
}

static inline unsigned int irq_pending(void)
{
	return csr_read_simple(CSR_CPU_IRQ_PENDING_ADDR);
}

#ifdef __cplusplus
}
#endif

#endif /* __IRQ_H */
