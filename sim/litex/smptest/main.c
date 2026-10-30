// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* smptest (L20, doc/l18-smp.md): two RD68041s on one bus, "+smp" (core.py),
 * loaded by the BIOS into main RAM and run by CPU 0, which starts CPU 1.
 *
 * Setup A (RD68041 doc/smp.md): every page both processors use is written
 * through with U = 01, so the other processor snoops its writes; private
 * pages are copyback with U = 00. The MMU (4 KiB pages, UM 3.2) gives each
 * page its mode, both processors through the same tables:
 *
 *   $4000_0000-$400F_FFFF  this program (code, data, the tables)  write-through, U 01
 *   SHARED  $4010_0000     shared data, 16 KiB                    write-through, U 01
 *   CI      $4010_4000     locks, flags, results                  noncachable, serialized
 *   CODE    $4010_5000     a routine CPU 0 rewrites               write-through, U 01
 *   REMAP   $4010_6000     mapped to PAGE_A, then PAGE_B          write-through, U 01
 *   PRIV0   $4011_0000     CPU 0's private data, 64 KiB           copyback, U 00
 *   PRIV1   $4012_0000     CPU 1's, and its stack at the top      copyback, U 00
 *
 * The I/O region by DTT0 (crt0.S); CPU 0's stack, at the top of main RAM, by
 * DTT1 (write-through, U 01) when it is outside the mapped megabyte.
 *
 * Each processor runs the same tests (run_tests), in phases ended by a
 * barrier; with timer1 (L23), a timer per CPU too, as NetBSD routes them.
 * CPU 1's failures go to the CI page, and CPU 0 reports both. The line
 * "smptest: PASS (n checks)" or "smptest: FAIL (...)" ends it. */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <generated/csr.h>
#include <generated/soc.h>
#include <irq.h>
#include <smp.h>
#include <system.h>

#ifndef ITERS
#define ITERS 16
#endif

#define SHARED   0x40100000u
#define CI_PAGE  0x40104000u
#define CODE     0x40105000u
#define REMAP    0x40106000u
#define PAGE_A   0x40107000u
#define PAGE_B   0x40108000u
#define PRIV0    0x40110000u
#define PRIV1    0x40120000u
#define PRIV_SIZE 0x10000u

/* Page descriptors (UM 3.2.2.3): resident, the cache mode, U1-U0. */
#define PD_RESIDENT 0x001
#define PD_WT       0x000
#define PD_CB       0x020
#define PD_SER      0x040
#define PD_U01      0x100
#define UDT_RESIDENT 0x002
#define TC_E        0x8000

#define MOVEC_TO(reg, v) __asm__ __volatile__ ("movec %0, %%" reg : : "d" (v) : "memory")

/* The tables: the root (A31-A25), one pointer table (A24-A18), five page
 * tables of 64 pages (A17-A12): $4000_0000-$4013_FFFF. Written through. */
static uint32_t root[128]       __attribute__((aligned(512)));
static uint32_t pointer[128]    __attribute__((aligned(512)));
static uint32_t pages[5][64]    __attribute__((aligned(256)));

static uint32_t *pte(uint32_t va)
{
	return &pages[(va - 0x40000000u) >> 18][(va >> 12) & 63];
}

/* What CPU 1 takes in cpu1_entry (smp.S), in its order. */
struct {
	uint32_t vbr, dtt0, dtt1, root, tc, cacr;
} cpu1_setup;

/* The CI page: everything the two processors exchange outside the tests'
 * own data, never cached. */
struct ci {
	unsigned long arrived[2];     /* the barrier: each one's phase */
	unsigned long errors[2];      /* failed checks, and the first's code */
	unsigned long first[2];
	unsigned long checks[2];
	unsigned long alive;          /* CPU 1's WHOAMI, + 1 */
	unsigned long ipis[2];        /* interprocessor interrupts taken */
	unsigned long ticks[2];       /* timer interrupts taken */
	unsigned long tticks[2][2];   /* NetBSD's routing: each CPU's ticks from timer0, timer1 */
	unsigned long shoot;          /* CPU 0 asks CPU 1 to flush its ATC */
	unsigned long shot;
	unsigned long seq, ack;       /* the producer and the consumer */
	unsigned long cilock;         /* a CAS lock, cache-inhibited */
	unsigned long cicount;
	unsigned long citas;          /* a TAS lock (its first byte) */
	unsigned long citcount;
	unsigned long cifree;         /* lock-free CAS */
	unsigned long unexpected[2];  /* format/vector of an unexpected exception, + 1 */
	unsigned long unexpected_pc[2];
	unsigned long routine[2];     /* what CPU 1's routine returned, before and after */
	unsigned long remap[2][2];    /* what each read at REMAP, before and after */
	unsigned long busy[2][2];     /* clocks each took to read its private data: alone, both */
};
#define ci ((volatile struct ci *)CI_PAGE)

/* The shared write-through page. */
struct shared {
	unsigned long lock;           /* a CAS lock */
	unsigned long count;
	unsigned long tas;            /* a TAS lock */
	unsigned long tcount;
	unsigned long free;           /* lock-free CAS */
	unsigned long pad[3];
	unsigned long fs[2];          /* false sharing: one line, a word each */
	unsigned long pad2[2];
	unsigned long data[8];        /* the producer's */
};
#define sh ((volatile struct shared *)SHARED)

static void (*vectors[256])(void) __attribute__((aligned(4)));
extern void ipi_handler(void), irq_handler(void), unexpected_handler(void);
extern void cpu1_entry(void), routine_one(void), routine_one_end(void);
void cpu1_main(void);

/* Checks: counted per processor, the first failure's code kept. */
static void check(int cpu, int ok, uint32_t code)
{
	ci->checks[cpu]++;
	if (!ok) {
		if (ci->errors[cpu]++ == 0)
			ci->first[cpu] = code;
	}
}

static void barrier(int cpu, uint32_t phase)
{
	ci->arrived[cpu] = phase;
	while (ci->arrived[1 - cpu] < phase)
		;
}

static void set_ipl_mask(int level)
{
	if (level == 0)
		__asm__ __volatile__ ("andi.w #0xf8ff, %%sr" : : : "memory");
	else
		__asm__ __volatile__ ("ori.w #0x0700, %%sr" : : : "memory");
}

/* Locks (CAS, TAS) and a lock-free increment. */
static void cas_lock(volatile unsigned long *l)
{
	unsigned long old;
	do {
		old = 0;
		__asm__ __volatile__ ("cas.l %0, %2, %1" : "+d" (old), "+m" (*l) : "d" (1) : "cc", "memory");
	} while (old != 0);
}

static void cas_unlock(volatile unsigned long *l)
{
	*l = 0;
}

static void tas_lock(volatile unsigned long *l)
{
	__asm__ __volatile__ ("1: tas (%0)\n\tbne.s 1b" : : "a" (l) : "cc", "memory");
}

static void tas_unlock(volatile unsigned long *l)
{
	*(volatile uint8_t *)l = 0;
}

static void cas_increment(volatile unsigned long *p)
{
	unsigned long old, new;
	do {
		old = *p;
		new = old + 1;
		__asm__ __volatile__ ("cas.l %0, %2, %1" : "+d" (old), "+m" (*p) : "d" (new) : "cc", "memory");
	} while (old != new - 1);
}

static void cpusha_dc(void)
{
	__asm__ __volatile__ ("cpusha %%dc" : : : "memory");
}

static void pflusha(void)
{
	__asm__ __volatile__ ("pflusha" : : : "memory");
}

/* The handlers (smp.S calls them). */
void ipi_c(void);
void ipi_c(void)
{
	int cpu = smp_whoami();
	smp_ipi_ack();
	ci->ipis[cpu]++;
	if (cpu == 1 && ci->shoot) {
		pflusha();
		ci->shoot = 0;
		ci->shot = 1;
	}
}

void irq_c(void);
void irq_c(void)
{
	int cpu = smp_whoami();
	/* Only the lines this CPU's mask lets through: the other's are its own
	 * to acknowledge. */
	uint32_t lines = irq_pending() & irq_getmask();
	if (lines & (1 << TIMER0_INTERRUPT)) {
		timer0_ev_pending_write(timer0_ev_pending_read());
		ci->ticks[cpu]++;
		ci->tticks[cpu][0]++;
	}
#ifdef CSR_TIMER1_BASE
	if (lines & (1 << TIMER1_INTERRUPT)) {
		timer1_ev_pending_write(timer1_ev_pending_read());
		ci->tticks[cpu][1]++;
	}
#endif
}

void unexpected_c(uint32_t pc, uint32_t fv);
void unexpected_c(uint32_t pc, uint32_t fv)
{
	int cpu = smp_whoami();
	ci->unexpected[cpu] = fv + 1;
	ci->unexpected_pc[cpu] = pc;
}

static uint32_t timer_now(void)
{
	timer0_update_value_write(1);
	return timer0_value_read();
}

/* The tests, both processors. */
static void run_tests(int cpu)
{
	uint32_t phase = 10;
	int other = 1 - cpu;

	set_ipl_mask(0);

	/* Interprocessor interrupts: each sends ITERS to the other, one at a
	 * time, waiting for each to be taken. */
	barrier(cpu, phase++);
	for (int i = 0; i < ITERS; i++) {
		uint32_t before = ci->ipis[other];
		smp_ipi(other);
		while (ci->ipis[other] == before)
			;
	}
	barrier(cpu, phase++);
	check(cpu, ci->ipis[cpu] == ITERS, 0x100 + cpu);

	/* Locks around counters, write-through and cache-inhibited, and
	 * lock-free increments. */
	for (int i = 0; i < ITERS; i++) {
		cas_lock(&sh->lock);  sh->count++;  cas_unlock(&sh->lock);
		tas_lock(&sh->tas);   sh->tcount++; tas_unlock(&sh->tas);
		cas_lock(&ci->cilock); ci->cicount++; cas_unlock(&ci->cilock);
		tas_lock(&ci->citas); ci->citcount++; tas_unlock(&ci->citas);
		cas_increment(&sh->free);
		cas_increment(&ci->cifree);
		sh->fs[cpu]++;           /* false sharing: one line, unlocked */
	}
	barrier(cpu, phase++);
	check(cpu, sh->count  == 2 * ITERS, 0x201);
	check(cpu, sh->tcount == 2 * ITERS, 0x202);
	check(cpu, ci->cicount == 2 * ITERS, 0x203);
	check(cpu, ci->citcount == 2 * ITERS, 0x204);
	check(cpu, sh->free == 2 * ITERS, 0x205);
	check(cpu, ci->cifree == 2 * ITERS, 0x206);
	check(cpu, sh->fs[0] == ITERS && sh->fs[1] == ITERS, 0x207);

	/* A producer (CPU 0) and a consumer (CPU 1), write-through: the data,
	 * then the sequence number; the consumer must see the data with it. */
	barrier(cpu, phase++);
	for (uint32_t s = 1; s <= ITERS; s++) {
		if (cpu == 0) {
			for (int k = 0; k < 8; k++)
				sh->data[k] = s * 0x01010101u + k;
			ci->seq = s;
			while (ci->ack != s)
				;
		} else {
			int ok = 1;
			while (ci->seq != s)
				;
			for (int k = 0; k < 8; k++)
				ok &= sh->data[k] == s * 0x01010101u + k;
			check(cpu, ok, 0x300 + s);
			ci->ack = s;
		}
	}

	/* The instruction cache: CPU 1 runs the routine (cached), CPU 0
	 * rewrites it (write-through, snooped: CPU 1's line invalidated), CPU 1
	 * runs it again. */
	barrier(cpu, phase++);
	if (cpu == 1)
		ci->routine[0] = ((uint32_t (*)(void))CODE)();
	barrier(cpu, phase++);
	if (cpu == 0)
		*(volatile uint16_t *)CODE = 0x7002;            /* moveq #2,d0 */
	barrier(cpu, phase++);
	if (cpu == 1) {
		ci->routine[1] = ((uint32_t (*)(void))CODE)();
		check(cpu, ci->routine[0] == 1 && ci->routine[1] == 2, 0x400);
	}

	/* A private copyback buffer handed over: CPU 0 fills it, pushes its
	 * data cache (CPUSHA), and CPU 1 reads it. */
	barrier(cpu, phase++);
	if (cpu == 0) {
		for (int k = 0; k < 64; k++)
			((volatile uint32_t *)PRIV0)[k] = 0xC0DE0000u + k;
		cpusha_dc();
	}
	barrier(cpu, phase++);
	if (cpu == 1) {
		int ok = 1;
		for (int k = 0; k < 64; k++)
			ok &= ((volatile uint32_t *)PRIV0)[k] == 0xC0DE0000u + k;
		check(cpu, ok, 0x500);
	}

	/* A TLB shootdown: both read REMAP (PAGE_A), CPU 0 maps it to PAGE_B
	 * and flushes its own ATC, then interrupts CPU 1, whose handler flushes
	 * its; both read REMAP again. */
	barrier(cpu, phase++);
	ci->remap[cpu][0] = *(volatile uint32_t *)REMAP;
	barrier(cpu, phase++);
	if (cpu == 0) {
		*pte(REMAP) = PAGE_B | PD_U01 | PD_WT | PD_RESIDENT;
		pflusha();
		ci->shoot = 1;
		smp_ipi(1);
		while (!ci->shot)
			;
	}
	barrier(cpu, phase++);
	ci->remap[cpu][1] = *(volatile uint32_t *)REMAP;
	check(cpu, ci->remap[cpu][0] == 0xAAAA0000u && ci->remap[cpu][1] == 0xBBBB0000u, 0x600 + cpu);

	/* LiteX's interrupts, a mask per CPU: timer0's, to CPU 1, then to CPU 0. */
	barrier(cpu, phase++);
	if (cpu == 0) {
		uint32_t t1;
		timer0_en_write(0);
		timer0_load_write(0);
		timer0_reload_write(CONFIG_CLOCK_FREQUENCY / 20000);
		timer0_ev_pending_write(timer0_ev_pending_read());
		timer0_ev_enable_write(1);
		csr_write_simple(0, CSR_CPU_IRQ_MASK_ADDR);
		csr_write_simple(1 << TIMER0_INTERRUPT, CSR_CPU_IRQ_MASK1_ADDR);
		timer0_en_write(1);
		while (ci->ticks[1] < 4)
			;
		csr_write_simple(0, CSR_CPU_IRQ_MASK1_ADDR);
		t1 = ci->ticks[1];
		csr_write_simple(1 << TIMER0_INTERRUPT, CSR_CPU_IRQ_MASK_ADDR);
		while (ci->ticks[0] < 4)
			;
		csr_write_simple(0, CSR_CPU_IRQ_MASK_ADDR);
		timer0_en_write(0);
		timer0_ev_enable_write(0);
		check(cpu, ci->ticks[1] <= t1 + 1, 0x700);
	}
	barrier(cpu, phase++);
	check(cpu, ci->ticks[cpu] >= 4, 0x710 + cpu);

#ifdef CSR_TIMER1_BASE
	/* A timer each, both running, as NetBSD routes them: timer0 to CPU 0
	 * alone, timer1 (another period) to CPU 1 alone. */
	if (cpu == 0) {
		ci->tticks[0][0] = ci->tticks[0][1] = ci->tticks[1][0] = ci->tticks[1][1] = 0;
		timer0_en_write(0);
		timer0_load_write(0);
		timer0_reload_write(CONFIG_CLOCK_FREQUENCY / 20000);
		timer0_ev_pending_write(timer0_ev_pending_read());
		timer0_ev_enable_write(1);
		timer1_en_write(0);
		timer1_load_write(0);
		timer1_reload_write(CONFIG_CLOCK_FREQUENCY / 13000);
		timer1_ev_pending_write(timer1_ev_pending_read());
		timer1_ev_enable_write(1);
		csr_write_simple(1 << TIMER0_INTERRUPT, CSR_CPU_IRQ_MASK_ADDR);
		csr_write_simple(1 << TIMER1_INTERRUPT, CSR_CPU_IRQ_MASK1_ADDR);
		timer0_en_write(1);
		timer1_en_write(1);
		while (ci->tticks[0][0] < 8 || ci->tticks[1][1] < 8)
			;
		csr_write_simple(0, CSR_CPU_IRQ_MASK_ADDR);
		csr_write_simple(0, CSR_CPU_IRQ_MASK1_ADDR);
		timer0_en_write(0);
		timer0_ev_enable_write(0);
		timer1_en_write(0);
		timer1_ev_enable_write(0);
		check(cpu, ci->tticks[0][1] == 0 && ci->tticks[1][0] == 0, 0x720);
	}
	barrier(cpu, phase++);
	check(cpu, ci->tticks[cpu][cpu] >= 8, 0x730 + cpu);
#endif

	/* The bus shared: each reads its private 64 KiB (copyback, through its
	 * 4 KiB data cache: a line fill every 16 bytes), alone, then both at
	 * once. The time, in sys clocks, is CPU 0's timer. */
	set_ipl_mask(7);
	for (int both = 0; both < 2; both++) {
		barrier(cpu, phase++);
		if (both || cpu == 0) {
			volatile uint32_t *p = (volatile uint32_t *)(cpu ? PRIV1 : PRIV0);
			uint32_t sum = 0, t0, t1;
			if (cpu == 0) {
				timer0_en_write(0);
				timer0_load_write(0xffffffff);
				timer0_reload_write(0);
				timer0_en_write(1);
			}
			barrier(cpu, phase++);
			t0 = timer_now();
			for (uint32_t k = 0; k < PRIV_SIZE / 4; k += 4)
				sum += p[k];
			t1 = timer_now();
			ci->busy[both][cpu] = t0 - t1;
			(void)sum;
		} else {
			barrier(cpu, phase++);
		}
	}
	barrier(cpu, phase++);
}

/* CPU 1, from cpu1_entry. */
void cpu1_main(void)
{
	ci->alive = smp_whoami() + 1;
	run_tests(1);
	ci->arrived[1] = 0xffffffff;
	for (;;)
		;
}

static void report_stats(void)
{
	static const char *names[SMP_NSTATS] = {
		"contention", "turnaround", "owner", "atomicity", "intervene", "maxwait",
		"ts0", "ts1", "handovers", "locked", "miwait",
	};
	printf("smptest: bus:");
	for (int k = 0; k < SMP_NSTATS; k++)
		printf(" %s %lu", names[k], smp_stat(k));
	printf("\n");
}

int main(void)
{
	uint32_t sp, i;
	uint32_t dtt1 = 0;

	irq_setmask(0);
	printf("\nsmptest: two RD68041s, %d iterations\n", ITERS);
	if (SMP_REG(SMP_NCPUS) != 2 || smp_whoami() != 0) {
		printf("smptest: FAIL (no two-processor board: NCPUS %lu, WHOAMI %d)\n",
		       SMP_REG(SMP_NCPUS), smp_whoami());
		return 0;
	}

	/* The vectors. */
	for (i = 0; i < 256; i++)
		vectors[i] = unexpected_handler;
	vectors[26] = irq_handler;
	vectors[SMP_IPI_VECTOR] = ipi_handler;

	/* The tables: everything mapped one to one. */
	for (i = 0; i < 128; i++)
		root[i] = pointer[i] = 0;
	root[0x40000000u >> 25] = (uint32_t)pointer | UDT_RESIDENT;
	for (i = 0; i < 5; i++)
		pointer[i] = (uint32_t)pages[i] | UDT_RESIDENT;
	for (uint32_t va = 0x40000000u; va < 0x40140000u; va += 0x1000)
		*pte(va) = va | PD_U01 | PD_WT | PD_RESIDENT;
	*pte(CI_PAGE) = CI_PAGE | PD_SER | PD_RESIDENT;
	for (uint32_t va = PRIV0; va < PRIV1 + PRIV_SIZE; va += 0x1000)
		*pte(va) = va | PD_CB | PD_RESIDENT;          /* U 00: private */
	*pte(REMAP) = PAGE_A | PD_U01 | PD_WT | PD_RESIDENT;
#ifdef SMPTEST_NO_SNOOP_CODE
	/* The negative test (smp_test.py --negative): the rewritten routine's
	 * page not snooped (U 00), so CPU 1 runs the old routine: check $400. */
	*pte(CODE) = CODE | PD_WT | PD_RESIDENT;
#endif

	/* Memory as the tests begin, before any cache holds it. */
	memset((void *)CI_PAGE, 0, 0x1000);
	memset((void *)SHARED, 0, 0x4000);
	memset((void *)PRIV0, 0, 2 * PRIV_SIZE);
	*(volatile uint32_t *)PAGE_A = 0xAAAA0000u;
	*(volatile uint32_t *)PAGE_B = 0xBBBB0000u;
	memcpy((void *)CODE, routine_one, (char *)routine_one_end - (char *)routine_one);

	/* CPU 0's stack, by DTT1 if it is outside the mapped megabyte. */
	__asm__ __volatile__ ("move.l %%sp, %0" : "=d" (sp));
	if (sp < 0x40000000u || sp >= 0x40100000u)
		dtt1 = (sp & 0xff000000u) | TTR_E | TTR_S_ANY | TTR_CM_WT | TTR_U01;

	/* CPU 0: translation on, its caches flushed and on. */
	__asm__ __volatile__ ("cpusha %%bc" : : : "memory");
	MOVEC_TO("dtt1", dtt1);
	MOVEC_TO("urp", (uint32_t)root);
	MOVEC_TO("srp", (uint32_t)root);
	pflusha();
	MOVEC_TO("tc", TC_E);
	MOVEC_TO("vbr", (uint32_t)vectors);
	__asm__ __volatile__ ("cinva %%bc" : : : "memory");
	MOVEC_TO("cacr", CACR_DE | CACR_IE);

	/* CPU 1: what it takes, then out of reset. */
	cpu1_setup.vbr  = (uint32_t)vectors;
	cpu1_setup.dtt0 = DTT_IO;
	cpu1_setup.dtt1 = dtt1;
	cpu1_setup.root = (uint32_t)root;
	cpu1_setup.tc   = TC_E;
	cpu1_setup.cacr = CACR_DE | CACR_IE;
	smp_stats_clear();
	smp_start_cpu1(cpu1_entry, (void *)(PRIV1 + PRIV_SIZE));
	for (i = 0; i < 10000000 && ci->alive == 0; i++)
		;
	if (ci->alive != 2) {
		printf("smptest: FAIL (CPU 1 did not start: %lu)\n", ci->alive);
		return 0;
	}
	printf("smptest: CPU 1 started, WHOAMI 1\n");

	run_tests(0);
	for (i = 0; i < 10000000 && ci->arrived[1] != 0xffffffff; i++)
		;

	/* The report. */
	printf("smptest: ipis %lu %lu, ticks %lu %lu, routine %lu then %lu, remap %08lx then %08lx\n",
	       ci->ipis[0], ci->ipis[1], ci->ticks[0], ci->ticks[1], ci->routine[0], ci->routine[1],
	       ci->remap[1][0], ci->remap[1][1]);
#ifdef CSR_TIMER1_BASE
	printf("smptest: timer0 ticks %lu %lu, timer1 ticks %lu %lu\n",
	       ci->tticks[0][0], ci->tticks[1][0], ci->tticks[0][1], ci->tticks[1][1]);
#endif
	printf("smptest: reading 64 KiB, sys clocks: CPU 0 alone %lu; both at once, CPU 0 %lu, CPU 1 %lu\n",
	       ci->busy[0][0], ci->busy[1][0], ci->busy[1][1]);
	report_stats();
	for (int cpu = 0; cpu < 2; cpu++)
		if (ci->unexpected[cpu])
			printf("smptest: CPU %d: unexpected exception, format/vector %04lx at %08lx\n",
			       cpu, ci->unexpected[cpu] - 1, ci->unexpected_pc[cpu]);
	check(0, ci->arrived[1] == 0xffffffff, 0x800);
	for (int k = SMP_STAT_CONTENTION; k <= SMP_STAT_INTERVENE; k++)
		check(0, smp_stat(k) == 0, 0x900 + k);
	if (ci->errors[0] || ci->errors[1] || ci->unexpected[0] || ci->unexpected[1])
		printf("smptest: FAIL (CPU 0: %lu of %lu, first %03lx; CPU 1: %lu of %lu, first %03lx)\n",
		       ci->errors[0], ci->checks[0], ci->first[0], ci->errors[1], ci->checks[1], ci->first[1]);
	else
		printf("smptest: PASS (%lu checks)\n", ci->checks[0] + ci->checks[1]);
	return 0;
}
