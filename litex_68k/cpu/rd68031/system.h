// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

#ifndef __SYSTEM_H
#define __SYSTEM_H

#ifdef __cplusplus
extern "C" {
#endif

/* The cache control register, CACR (UM 6.3.1). MOVEC is privileged: everything
 * here runs in supervisor mode, as the BIOS does. */
#define CACR_EI  0x0001 /* enable instruction cache */
#define CACR_FI  0x0002 /* freeze instruction cache */
#define CACR_CEI 0x0004 /* clear entry in instruction cache */
#define CACR_CI  0x0008 /* clear instruction cache */
#define CACR_IBE 0x0010 /* instruction burst enable */
#define CACR_ED  0x0100 /* enable data cache */
#define CACR_FD  0x0200 /* freeze data cache */
#define CACR_CED 0x0400 /* clear entry in data cache */
#define CACR_CD  0x0800 /* clear data cache */
#define CACR_DBE 0x1000 /* data burst enable */
#define CACR_WA  0x2000 /* write allocate */

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

/* The clear bits always read as zero (UM 6.3.1), so setting one alongside the
 * current value clears the cache and changes nothing else. */
__attribute__((unused)) static void flush_cpu_icache(void)
{
	set_cacr(get_cacr() | CACR_CI);
}

/* The data cache is write-through (UM 6.1.2): clearing it loses nothing, and
 * makes the next reads go to memory, which is what a flush is for here. */
__attribute__((unused)) static void flush_cpu_dcache(void)
{
	set_cacr(get_cacr() | CACR_CD);
}

void flush_l2_cache(void);

void busy_wait(unsigned int ms);
void busy_wait_us(unsigned int us);

#ifdef __cplusplus
}
#endif

#endif /* __SYSTEM_H */
