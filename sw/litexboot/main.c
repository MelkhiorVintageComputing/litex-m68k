// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* litexboot: Linux/m68k for RD68031 or RD68041 in a LiteX SoC (doc/l7-linux.md).
 *
 * The BIOS loads the images images.py placed (layout.h) and jumps here. This
 * program, built against the SoC's generated headers:
 *  - copies vmlinux's loadable segments to the start of main RAM (the kernel
 *    runs where virtual 0 is the start of its first memory chunk);
 *  - writes the bootinfo right after the kernel (_end, the end of its last
 *    segment), where head.S looks for it: the machine, CPU, FPU (FNOP says
 *    whether there is one; the record is always there, head.S reads garbage
 *    otherwise), MMU, main RAM, the initramfs, the command line, and where
 *    the SoC's cores are (bootinfo-litex.h); with two processors ("+smp"),
 *    the board's registers and CPU 1's timer too;
 *  - leaves the SoC quiet (interrupts masked, timer0 and timer1 stopped), the caches off,
 *    and jumps to the kernel's entry point, MMU off, IPL 7. */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <generated/csr.h>
#include <generated/mem.h>
#include <generated/soc.h>
#include <irq.h>
#include <libbase/uart.h>
#include <system.h>

#include "bootinfo.h"
#include "layout.h"

/* The processor, as the SoC was built (generated/soc.h). */
#ifdef CONFIG_CPU_TYPE_RD68041
#define CPU_TYPE   CPU_68040
#define MMU_TYPE   MMU_68040
#define FPU_TYPE   FPU_68040
#define FPU_NAME   "68040"
#else
#define CPU_TYPE   CPU_68030
#define MMU_TYPE   MMU_68030
#define FPU_TYPE   FPU_68881
#define FPU_NAME   "68881"
#endif

/* probe.S */
extern int fpu_probe(void);
extern void fline_catch(void);
extern void jump_kernel(unsigned long entry) __attribute__((noreturn));

/* ELF, 32-bit big-endian: the parts read here. */
#define EM_68K   4
#define PT_LOAD  1
struct elf_header {
	uint8_t  ident[16];
	uint16_t type, machine;
	uint32_t version, entry, phoff, shoff, flags;
	uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx;
};
struct elf_phdr {
	uint32_t type, offset, vaddr, paddr, filesz, memsz, flags, align;
};

static uint8_t *bi;     /* the next bootinfo record */

static void record(uint16_t tag, const void *data, uint16_t size)
{
	uint16_t total = (4 + size + 3) & ~3;

	memcpy(bi, &tag, 2);
	memcpy(bi + 2, &total, 2);
	memset(bi + 4, 0, total - 4);
	memcpy(bi + 4, data, size);
	bi += total;
}

static void record32(uint16_t tag, uint32_t a)
{
	record(tag, &a, 4);
}

static void record64(uint16_t tag, uint32_t a, uint32_t b)
{
	uint32_t d[2] = { a, b };
	record(tag, d, 8);
}

static int has_fpu(void)
{
	static void (*vectors[256])(void) __attribute__((aligned(4)));
	unsigned long vbr;
	int fpu;

	__asm__ __volatile__ ("movec %%vbr, %0" : "=r" (vbr));
	memcpy(vectors, (void *)vbr, sizeof(vectors));
	vectors[11] = fline_catch;
	__asm__ __volatile__ ("movec %0, %%vbr" : : "r" (vectors) : "memory");
	fpu = fpu_probe();
	__asm__ __volatile__ ("movec %0, %%vbr" : : "r" (vbr) : "memory");
	return fpu;
}

int main(void)
{
	const struct params *p = (const struct params *)LAYOUT_PARAMS(MAIN_RAM_BASE, MAIN_RAM_SIZE);
	const struct elf_header *eh;
	const struct elf_phdr *ph;
	unsigned long end = 0;
	int i, fpu;

	printf("\nlitexboot: Linux/m68k on " CONFIG_CPU_HUMAN_NAME ", main RAM 0x%08lx+0x%lx\n",
	       (unsigned long)MAIN_RAM_BASE, (unsigned long)MAIN_RAM_SIZE);
	if (p->magic != PARAMS_MAGIC || p->version != PARAMS_VERSION) {
		printf("litexboot: no parameters at %p (images.py)\n", p);
		return 1;
	}

	eh = (const struct elf_header *)p->elf_addr;
	if (memcmp(eh->ident, "\177ELF\1\2", 6) || eh->machine != EM_68K) {
		printf("litexboot: no 32-bit big-endian m68k ELF at 0x%08lx\n", p->elf_addr);
		return 1;
	}
	ph = (const struct elf_phdr *)(p->elf_addr + eh->phoff);
	for (i = 0; i < eh->phnum; i++) {
		unsigned long dst = MAIN_RAM_BASE + ph[i].paddr;

		if (ph[i].type != PT_LOAD)
			continue;
		printf("litexboot: segment 0x%08lx+0x%lx (file 0x%lx) at 0x%08lx\n",
		       (unsigned long)ph[i].paddr, (unsigned long)ph[i].memsz,
		       (unsigned long)ph[i].filesz, dst);
		memcpy((void *)dst, (const void *)(p->elf_addr + ph[i].offset), ph[i].filesz);
		memset((void *)(dst + ph[i].filesz), 0, ph[i].memsz - ph[i].filesz);
		if (ph[i].paddr + ph[i].memsz > end)
			end = ph[i].paddr + ph[i].memsz;
	}

	fpu = has_fpu();
	bi = (uint8_t *)(MAIN_RAM_BASE + end);
	record32(BI_MACHTYPE, MACH_LITEX);
	record32(BI_CPUTYPE, CPU_TYPE);
	record32(BI_FPUTYPE, fpu ? FPU_TYPE : 0);
	record32(BI_MMUTYPE, MMU_TYPE);
	record64(BI_MEMCHUNK, MAIN_RAM_BASE, MAIN_RAM_SIZE);
	if (p->initrd_size)
		record64(BI_RAMDISK, p->initrd_addr, p->initrd_size);
	record(BI_COMMAND_LINE, p->cmdline, strnlen(p->cmdline, PARAMS_CMDLINE - 1) + 1);
	record32(BI_LITEX_SYS_CLK, p->sys_clk ? p->sys_clk : CONFIG_CLOCK_FREQUENCY);
	record64(BI_LITEX_IRQCTRL, CSR_CPU_BASE, 2);     /* core.py's IRQ_LEVEL */
	record64(BI_LITEX_CTRL, CSR_CTRL_BASE, 0);
	record64(BI_LITEX_UART, CSR_UART_BASE, UART_INTERRUPT);
	record64(BI_LITEX_TIMER0, CSR_TIMER0_BASE, TIMER0_INTERRUPT);
	if (p->dtb_size)
		record64(BI_LITEX_DTB, p->dtb_addr, p->dtb_size);
#ifdef __rd68041_smp__
	/* Two processors (core.py's "+smp"): the board's registers, and CPU 1's
	 * timer. A kernel without these records is on one processor. */
	record64(BI_LITEX_SMP, SMP_BASE, *(volatile uint32_t *)SMP_NCPUS);
#ifdef CSR_TIMER1_BASE
	record64(BI_LITEX_TIMER1, CSR_TIMER1_BASE, TIMER1_INTERRUPT);
#endif
#endif
	record(BI_LAST, NULL, 0);

	printf("litexboot: bootinfo at 0x%08lx (%ld bytes), FPU %s, initrd 0x%08lx+0x%lx\n",
	       (unsigned long)(MAIN_RAM_BASE + end), (long)(bi - (uint8_t *)(MAIN_RAM_BASE + end)),
	       fpu ? FPU_NAME : "none", p->initrd_addr, p->initrd_size);
	if (p->dtb_size)
		printf("litexboot: device tree 0x%08lx+0x%lx\n", p->dtb_addr, p->dtb_size);
	printf("litexboot: command line \"%s\", sys clock %lu Hz\n", p->cmdline,
	       p->sys_clk ? p->sys_clk : (unsigned long)CONFIG_CLOCK_FREQUENCY);
	printf("litexboot: entry 0x%08lx\n", (unsigned long)(MAIN_RAM_BASE + eh->entry));

	/* Quiet: no core asks for an interrupt, the kernel unmasks and enables
	 * what it uses. The BIOS leaves the UART's events enabled (rx and tx):
	 * left so, the line is up from the moment the kernel unmasks it, before
	 * liteuart enables the events it handles, and an interrupt it does not
	 * handle is "nobody cared", then disabled for good. */
	uart_sync();
	irq_setie(0);
	irq_setmask(0);
	uart_ev_enable_write(0);
	uart_ev_pending_write(uart_ev_pending_read());
	timer0_en_write(0);
	timer0_ev_enable_write(0);
	timer0_ev_pending_write(timer0_ev_pending_read());
#ifdef CSR_TIMER1_BASE
	csr_write_simple(0, CSR_CPU_IRQ_MASK1_ADDR);   /* "+smp": CPU 1's mask, and its timer */
	timer1_en_write(0);
	timer1_ev_enable_write(0);
	timer1_ev_pending_write(timer1_ev_pending_read());
#endif
#ifdef CSR_SDCARD_PHY_SETTINGS_ADDR
	/* LiteSDCard's PHY data width back to its reset value, 4-bit: Linux's
	 * litex_mmc never sets it (it puts the card in 4-bit mode and assumes
	 * the PHY is), and the BIOS's sdcard_init may leave it at 1-bit (it
	 * did on m68k, reading the card's SCR in the wrong byte order:
	 * patches/litex 0002; the kernel then failed its first data transfer
	 * on CRC). */
	sdcard_phy_settings_write(1);          /* SD_PHY_SPEED_4X */
#endif
	jump_kernel(MAIN_RAM_BASE + eh->entry);
}
