// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* bidump: a test kernel for litexboot. Entered as a kernel is (MMU off, IPL
 * 7), it prints the bootinfo records litexboot wrote after its end, one line
 * each, tag, size and long words in hex, then "bidump: end", and stops:
 *
 *   scripts/board.py boot --kernel bidump.elf --no-dtb --until 'bidump: end'
 */

#include <stdint.h>
#include <generated/csr.h>

extern uint8_t _end[];

static void out(char c)
{
	while (uart_txfull_read())
		;
	uart_rxtx_write(c);
}

static void str(const char *s)
{
	while (*s)
		out(*s++);
}

static void hex(uint32_t v, int digits)
{
	while (digits--)
		out("0123456789abcdef"[(v >> (4 * digits)) & 15]);
}

void bidump(void);
void bidump(void)
{
	const uint8_t *bi = _end;

	str("\r\nbidump: bootinfo at ");
	hex((uint32_t)bi, 8);
	str("\r\n");
	for (int n = 0; n < 64; n++) {
		uint16_t tag  = *(const uint16_t *)bi;
		uint16_t size = *(const uint16_t *)(bi + 2);

		str("bidump: ");
		hex(tag, 4);
		out(' ');
		hex(size, 4);
		for (int i = 4; i + 4 <= size && i < 4 + 4 * 4; i += 4) {
			out(' ');
			hex(*(const uint32_t *)(bi + i), 8);
		}
		str("\r\n");
		if (tag == 0 || size < 4)
			break;
		bi += size;
	}
	str("bidump: end\r\n");
}
