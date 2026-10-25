// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* L2 smoke test: the CPU (RD68031 or RD68041) in a LiteX SoC, before the
 * BIOS. From the ROM, with LiteX's generated headers and the CPU's own irq.h:
 * the UART, a CTRL CSR, the CPU's interrupt CSRs and the interrupt enable.
 * Prints "L2 PASS" or "L2 FAIL", then ends the simulation (litex_sim
 * --sim-debug). */

#include <generated/csr.h>
#include <generated/soc.h>
#include <irq.h>

static void put(char c)
{
	while (uart_txfull_read())
		;
	uart_rxtx_write(c);
}

static void print(const char *s)
{
	while (*s)
		put(*s++);
}

static void print_hex(unsigned long v)
{
	for (int i = 28; i >= 0; i -= 4)
		put("0123456789abcdef"[(v >> i) & 15]);
}

static void finish(void)
{
	/* Let the UART drain before the simulation stops. */
	while (!uart_txempty_read())
		;
	sim_finish_finish_write(1);
	for (;;)
		;
}

static int failures;

static void check(const char *what, unsigned long got, unsigned long expected)
{
	print(what);
	print(": ");
	print_hex(got);
	if (got != expected) {
		print(" FAIL, expected ");
		print_hex(expected);
		failures++;
	}
	put('\n');
}

void unexpected(unsigned long format_vector)
{
	print("unexpected exception, format/vector ");
	print_hex(format_vector);
	print("\nL2 FAIL\n");
	finish();
}

static volatile int ticks;

void irq_handler(void)
{
	unsigned int pending = irq_pending() & irq_getmask();

	if (pending & (1 << TIMER0_INTERRUPT)) {
		timer0_ev_pending_write(1);
		ticks++;
	}
}

/* timer0 every 1000 clocks, three of its interrupts taken through the CPU's
 * interrupt CSRs, IPL and the autovector. */
static int timer_interrupts(void)
{
	timer0_en_write(0);
	timer0_reload_write(1000);
	timer0_load_write(1000);
	timer0_en_write(1);
	timer0_ev_pending_write(1);
	timer0_ev_enable_write(1);
	irq_setmask(1 << TIMER0_INTERRUPT);
	irq_setie(1);
	for (int i = 0; i < 100000 && ticks < 3; i++)
		;
	irq_setie(0);
	irq_setmask(0);
	timer0_ev_enable_write(0);
	timer0_en_write(0);
	return ticks;
}

int main(void)
{
	print("\nHello from " CONFIG_CPU_HUMAN_NAME " on LiteX\n");

	ctrl_scratch_write(0xdeadbeef);
	check("ctrl_scratch", ctrl_scratch_read(), 0xdeadbeef);

	irq_setmask(0x00000005);
	check("irq_mask", irq_getmask(), 0x00000005);
	irq_setmask(0);

	irq_setie(0);
	check("irq_getie after setie(0)", irq_getie(), 0);
	irq_setie(1);
	check("irq_getie after setie(1)", irq_getie(), 1);
	irq_setie(0);

	/* Nothing is pending with the UART's events disabled and its FIFO empty. */
	check("irq_pending", irq_pending(), 0);

	check("timer interrupts taken", timer_interrupts() >= 3, 1);

	print(failures ? "L2 FAIL\n" : "L2 PASS\n");
	finish();
	return 0;
}
