// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* fpswitch: two tasks computing with the FPU, switched by timer0 at irregular
 * intervals, the FPU state saved and restored as Linux does (switch.S). The
 * switches land in the middle of FPU instructions, so the FPU is often busy at
 * the FSAVE. Each task repeats a computation and compares its bits with the
 * answer computed first with no switching.
 *
 * Prints the frames FSAVE gave (by size), the tasks' rounds, then
 * "fpswitch: PASS" or "fpswitch: FAIL". A hang is the other outcome
 * (doc/l7-linux.md). */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <irq.h>
#include <libbase/uart.h>
#include <generated/csr.h>

#define SWITCHES 200

struct task {
	uint32_t sp;
	uint8_t frame[256];
	uint8_t fpregs[96 + 12];
} __attribute__((aligned(4)));

static struct task tasks[2];
struct task *fpsw_cur = &tasks[0], *fpsw_other = &tasks[1];
uint32_t fpsw_frames[257];
static uint32_t stack_b[1024];

static volatile uint32_t switches, rounds[2], wrong[2];
static uint64_t answer;
static uint32_t lcg = 12345;

extern uint32_t _vectors[];
void fpsw_irq(void);
void fpsw_prime(struct task *t, void *stack_top, void (*entry)(void));
int fpsw_tick(void);

static void arm(void)
{
	lcg = lcg * 1103515245 + 12345;
	timer0_en_write(0);
	timer0_load_write(300 + ((lcg >> 16) % 3000));
	timer0_reload_write(0);
	timer0_en_write(1);
}

/* From fpsw_irq: 1 to switch tasks. */
int fpsw_tick(void)
{
	uint32_t pending = irq_pending() & irq_getmask();

	if (pending & (1 << UART_INTERRUPT))
		uart_isr();
	if (!(pending & (1 << TIMER0_INTERRUPT)))
		return 0;
	timer0_ev_pending_write(1);
	if (switches >= SWITCHES) {             /* done: back to the first task */
		timer0_en_write(0);
		return fpsw_cur != &tasks[0];
	}
	switches++;
	arm();
	return 1;
}

/* Long FPU instructions, so that switches find the FPU busy. */
static uint64_t __attribute__((noinline)) compute(void)
{
	long double x = 0.5L;
	double d;
	uint64_t bits;

	for (int i = 0; i < 16; i++) {
		long double s, r;
		__asm__ volatile ("fsin.x %1, %0" : "=f" (s) : "f" (x));
		__asm__ volatile ("fsqrt.x %1, %0" : "=f" (r) : "f" (x * x + 1.5L));
		x = r / 1.0001L + s;
	}
	d = x;
	memcpy(&bits, &d, sizeof bits);
	return bits;
}

static void work(int id)
{
	uint32_t shown = 0;

	for (;;) {
		if (id == 0 && switches >= shown + 50) {
			shown = switches;
			printf("fpswitch: %lu switches, rounds %lu + %lu\n", (unsigned long)shown,
			       (unsigned long)rounds[0], (unsigned long)rounds[1]);
		}
		if (compute() != answer)
			wrong[id]++;
		rounds[id]++;
		if (id == 0 && switches >= SWITCHES)
			return;
	}
}

static void task_b(void)
{
	work(1);
}

int main(void)
{
	printf("\nfpswitch: two FPU tasks, %d switches\n", SWITCHES);
	answer = compute();
	printf("fpswitch: answer %08lx%08lx\n", (unsigned long)(answer >> 32), (unsigned long)answer);

	memset(tasks, 0, sizeof tasks);                 /* null frames: a reset FPU */
	fpsw_prime(&tasks[1], &stack_b[1024], task_b);
	irq_setie(0);
	_vectors[26] = (uint32_t)fpsw_irq;
	timer0_ev_enable_write(1);
	irq_setmask(irq_getmask() | (1 << TIMER0_INTERRUPT));
	arm();
#ifdef CSR_SIM_TRACE_BASE
	sim_trace_enable_write(1);              /* litex_sim --trace: from here */
#endif
	irq_setie(1);

	work(0);
	irq_setie(0);
	timer0_en_write(0);
	timer0_ev_enable_write(0);

	printf("fpswitch: %lu switches, rounds %lu + %lu, wrong %lu + %lu\n",
	       (unsigned long)switches, (unsigned long)rounds[0], (unsigned long)rounds[1],
	       (unsigned long)wrong[0], (unsigned long)wrong[1]);
	printf("fpswitch: frames: null %lu", (unsigned long)fpsw_frames[256]);
	for (int i = 0; i < 256; i++)
		if (fpsw_frames[i])
			printf(", $%02x %lu", i, (unsigned long)fpsw_frames[i]);
	printf("\n");
	printf("fpswitch: %s\n", wrong[0] || wrong[1] || !rounds[1] ? "FAIL" : "PASS");
	uart_sync();
	for (;;);
	return 0;
}
