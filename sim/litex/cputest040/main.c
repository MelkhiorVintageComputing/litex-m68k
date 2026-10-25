// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* cputest040: the MC68040 (RD68041) as a program loaded by the LiteX BIOS
 * sees it. cputest/'s counterpart for the 030.
 *
 *   exceptions  every exception a program can cause on purpose, each checked
 *               for its frame format, vector, and the addresses in the frame
 *               (UM 8.2, 8.4); bus errors as access error frames, format $7,
 *               with their SSW and fault address (UM 8.4.6)
 *   icache      code written as data runs stale until CINV (UM 4.2): the
 *               processor's own writes are not snooped by its instruction
 *               cache
 *   mmu         4 KiB pages through the three levels of tables (UM 3.2),
 *               the program itself transparently translated (ITT0, DTT0-1,
 *               UM 3.1.3); four aliases of one page: copyback, noncachable,
 *               write-protected, invalid. A copyback write seen through the
 *               physically tagged data cache (UM 4.1), and through the
 *               noncachable alias, which pushes the dirty line (UM 4.3.2);
 *               CPUSH (UM 4.2); ATC faults; U and M updates; PTEST and MMUSR
 *               (UM 3.1.4)
 *   fpu         whether there is one (FNOP: on the MC68LC040, F-line, format
 *               $4); C doubles, bit for bit: the FPU's with an FPU build,
 *               compiler-rt's without (-msoft-float); with the FPU: FSQRT,
 *               FPSR, an enabled divide by zero, the idle and unimplemented
 *               instruction FSAVE frames (version $41), and FSIN and FMOVECR,
 *               which the MC68040 leaves to the M68040FPSP (UM 9.6.1)
 *
 * The cache checks print what they see, and the FPU check whether there is
 * one ("fpu: present" or "fpu: absent"); sim/litex/l4_test.py knows what each
 * variant should say. Then "cputest: PASS" or "cputest: FAIL", and a last
 * illegal instruction left to crt0's handler, which reports it and stops. */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <system.h>

/* traps.S */
extern unsigned trap_run(void (*fn)(void));
extern void trap_catch(void);
extern volatile uint8_t trap_frame[24];

#define DECLARE(t) extern void t(void), t##_insn(void), t##_next(void)
DECLARE(t_illegal); DECLARE(t_zerodiv); DECLARE(t_chk); DECLARE(t_trapv);
DECLARE(t_trap5); DECLARE(t_linea); DECLARE(t_linef); DECLARE(t_priv);
DECLARE(t_trace); DECLARE(t_addrerr); DECLARE(t_berr_read); DECLARE(t_berr_write);
DECLARE(t_mmu_read); DECLARE(t_mmu_write); DECLARE(t_unhandled);
DECLARE(t_fnop); DECLARE(t_fsin); DECLARE(t_fmovecr); DECLARE(t_fpdz);

volatile uint32_t mmu_probe;            /* the address t_mmu_read/write use */

static uint16_t frame_word(int off)
{
	return (trap_frame[off] << 8) | trap_frame[off + 1];
}

static uint32_t frame_long(int off)
{
	return ((uint32_t)frame_word(off) << 16) | frame_word(off + 2);
}

#define FRAME_SR       frame_word(0)
#define FRAME_PC       frame_long(2)
#define FRAME_ADDRESS  frame_long(8)    /* format $2: the address */
#define FRAME_SSW      frame_word(12)   /* format $7 */
#define FRAME_FAULT    frame_long(20)   /* format $7: the fault address */

/* The access error SSW (UM 8.4.6.2, Figure 8-7). */
#define SSW_ATC   0x0400
#define SSW_RW    0x0100                /* 1: read */
#define SSW_SIZE  0x0060
#define SSW_TT    0x0018
#define SSW_TM    0x0007

static int failures, checks;

/* CHECK(condition, what, printf arguments for when it fails) */
#define CHECK(ok, what, ...) do {                                       \
		checks++;                                               \
		if (!(ok)) {                                            \
			failures++;                                     \
			printf("  FAIL %s: ", what);                    \
			printf(__VA_ARGS__);                            \
			printf("\n");                                   \
		}                                                       \
	} while (0)

/* --- Exceptions ------------------------------------------------------------ */

static void (*vectors[256])(void) __attribute__((aligned(4)));

static unsigned long get_vbr(void)
{
	unsigned long vbr;
	__asm__ __volatile__ ("movec %%vbr, %0" : "=r" (vbr));
	return vbr;
}

static void set_vbr(unsigned long vbr)
{
	__asm__ __volatile__ ("movec %0, %%vbr" : : "r" (vbr) : "memory");
}

/* Run t, which must take an exception with the frame format and vector in
 * fv, and with the stacked PC at pc (0: not checked). For format $2 frames,
 * the address must be addr (0: not checked). */
static void expect(const char *name, void (*t)(void), unsigned fv, void (*pc)(void), void (*addr)(void))
{
	unsigned got = trap_run(t);

	CHECK(got != 0, name, "no exception");
	if (got == 0)
		return;
	CHECK(got == fv, name, "format/vector %04x, expected %04x", got, fv);
	if (pc)
		CHECK(FRAME_PC == (uint32_t)pc, name, "PC %08x, expected %08x", FRAME_PC, (unsigned)pc);
	if ((got >> 12) == 2 && addr)
		CHECK(FRAME_ADDRESS == (uint32_t)addr, name,
			"address %08x, expected %08x", FRAME_ADDRESS, (unsigned)addr);
}

/* An access error on a long-word data access in supervisor data space (TM 5,
 * a normal transfer): the SSW says so, with the direction and whether the
 * MMU refused it (ATC) or the bus did, and the frame has the address. */
static void expect_access_error(const char *name, int read, int atc, uint32_t address)
{
	unsigned want = (atc ? SSW_ATC : 0) | (read ? SSW_RW : 0) | 5;
	unsigned mask = SSW_ATC | SSW_RW | SSW_SIZE | SSW_TT | SSW_TM;

	CHECK((FRAME_SSW & mask) == want, name, "SSW %04x, expected %04x in %04x", FRAME_SSW, want, mask);
	CHECK(FRAME_FAULT == address, name, "fault address %08x, expected %08x", FRAME_FAULT, address);
}

static void test_exceptions(void)
{
	int f0 = failures;

	expect("illegal",    t_illegal, 0x0010, t_illegal_insn, 0);
	expect("zero divide", t_zerodiv, 0x2014, t_zerodiv_next, t_zerodiv_insn);
	expect("chk",        t_chk,     0x2018, t_chk_next, t_chk_insn);
	expect("trapv",      t_trapv,   0x201c, t_trapv_next, t_trapv_insn);
	expect("trap #5",    t_trap5,   0x0094, t_trap5_next, 0);
	expect("line A",     t_linea,   0x0028, t_linea_insn, 0);
	expect("line F",     t_linef,   0x002c, t_linef_insn, 0);
	expect("privilege",  t_priv,    0x0020, t_priv_insn, 0);
	CHECK(!(FRAME_SR & 0x2000), "privilege", "taken in supervisor mode, SR %04x", FRAME_SR);
	expect("trace",      t_trace,   0x2024, t_trace_next, t_trace_insn);
	/* Format $2, the address the reference address - 1 (UM 8.4.3). */
	expect("address error", t_addrerr, 0x200c, 0, t_addrerr_next);
	expect("bus error, read", t_berr_read, 0x7008, 0, 0);
	expect_access_error("bus error, read", 1, 0, 0x90000000);
	expect("bus error, write", t_berr_write, 0x7008, 0, 0);
	expect_access_error("bus error, write", 0, 0, 0x90000000);

	printf("exceptions: %s\n", failures == f0 ? "ok" : "FAIL");
}

/* --- Instruction cache ----------------------------------------------------- */

static uint16_t code[8] __attribute__((aligned(16)));

static int run_code(void)
{
	register int d0 __asm__("d0");
	__asm__ __volatile__ ("jsr (%1)" : "=d" (d0) : "a" (code) : "memory");
	return d0;
}

/* The data cache is write-through here (crt0), so code[] is in memory as soon
 * as it is written; the instruction cache keeps the line it loaded (four
 * ways: nothing run in between evicts it) until CINV. */
static void test_icache(void)
{
	int f0 = failures, first, second, third;

	code[0] = 0x7001;               /* moveq #1, d0 */
	code[1] = 0x4e75;               /* rts */
	flush_cpu_icache();
	first = run_code();
	code[0] = 0x7002;               /* moveq #2, d0: memory only */
	second = run_code();
	flush_cpu_icache();
	third = run_code();

	CHECK(first == 1, "icache", "first run returned %u", first);
	CHECK(second == 1 || second == 2, "icache", "second run returned %u", second);
	CHECK(third == 2, "icache", "run after CINV returned %u", third);
	printf("icache: %s, %s\n", second == 1 ? "stale until CINV" : "coherent",
		failures == f0 ? "ok" : "FAIL");
}

/* --- MMU ------------------------------------------------------------------- */

/* TC: enabled, 4 KiB pages (UM 3.1.2). The tables (UM 3.2.1): 128 root
 * descriptors by A31-A25, 128 pointer descriptors by A24-A18, 64 page
 * descriptors by A17-A12. Only ALIAS's 16 KiB go through them: the program,
 * its stack and the UART are transparently translated, as crt0 has them. */
#define TC_E        0x8000

#define UDT_RESIDENT 2                  /* table descriptors (UM 3.2.2.3) */
#define PDT_INVALID  0                  /* page descriptors */
#define PDT_RESIDENT 1
#define D_W         0x0004
#define D_U         0x0008
#define D_M         0x0010
#define D_CM_CB     0x0020
#define D_CM_SER    0x0040

#define MMUSR_B     0x0800
#define MMUSR_CM    0x0060
#define MMUSR_M     0x0010
#define MMUSR_W     0x0004
#define MMUSR_T     0x0002
#define MMUSR_R     0x0001

#define ALIAS       0x50000000
#define ALIAS_CB    (ALIAS + 0x0000)    /* the page, copyback */
#define ALIAS_NC    (ALIAS + 0x1000)    /* the page again, noncachable, serialized */
#define ALIAS_WP    (ALIAS + 0x2000)    /* the page again, write-protected */
#define ALIAS_NONE  (ALIAS + 0x3000)    /* invalid */

/* ITT0: $0000_0000-$7FFF_FFFF, instructions, write-through. DTT1:
 * $4000_0000-$4FFF_FFFF, main RAM's data, write-through. DTT0 is crt0's, the
 * I/O region. */
#define ITT0_LOW    (0x007F0000 + TTR_E + TTR_S_ANY + TTR_CM_WT)
#define DTT1_RAM    (0x400F0000 + TTR_E + TTR_S_ANY + TTR_CM_WT)

static uint32_t root[128]    __attribute__((aligned(512)));
static uint32_t pointer[128] __attribute__((aligned(512)));
static uint32_t pages[64]    __attribute__((aligned(256)));
static uint32_t page[1024]   __attribute__((aligned(4096)));

#define MOVEC_TO(reg, v) __asm__ __volatile__ ("movec %0, %%" reg : : "d" (v) : "memory")

static uint32_t ptest_read(uint32_t address)
{
	uint32_t mmusr;
	/* PTEST looks the address up in DFC's space: supervisor data (5). */
	__asm__ __volatile__ ("movec %2, %%dfc\n\tptestr (%1)\n\tmovec %%mmusr, %0"
		: "=d" (mmusr) : "a" (address), "d" (5) : "memory");
	return mmusr;
}

static uint32_t peek(uint32_t a) { return *(volatile uint32_t *)a; }
static void poke(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* The descriptors, as the processor left them in memory (written through,
 * and the data cache holds nothing dirty of them). */
static uint32_t descriptor(int i)
{
	return ((volatile uint32_t *)pages)[i];
}

static void test_mmu(void)
{
	int f0 = failures, i;
	uint32_t p = (uint32_t)page, d, v, mmusr;

	memset(root, 0, sizeof(root));
	memset(pointer, 0, sizeof(pointer));
	memset(pages, 0, sizeof(pages));
	root[ALIAS >> 25] = (uint32_t)pointer | UDT_RESIDENT;
	pointer[(ALIAS >> 18) & 0x7f] = (uint32_t)pages | UDT_RESIDENT;
	pages[0] = p | D_CM_CB  | PDT_RESIDENT;
	pages[1] = p | D_CM_SER | PDT_RESIDENT;
	pages[2] = p | D_W      | PDT_RESIDENT;
	pages[3] = PDT_INVALID;
	for (i = 0; i < 1024; i++)
		page[i] = 0;
	poke(p, 0x11111111);

	MOVEC_TO("itt0", ITT0_LOW);
	MOVEC_TO("dtt1", DTT1_RAM);
	MOVEC_TO("urp", (uint32_t)root);
	MOVEC_TO("srp", (uint32_t)root);
	__asm__ __volatile__ ("pflusha" : : : "memory");
	MOVEC_TO("tc", TC_E);
	printf("mmu: on, TC %04x\n", TC_E);

	CHECK(peek(ALIAS_CB) == 0x11111111, "mmu alias cb", "read %08x", peek(ALIAS_CB));
	CHECK(peek(ALIAS_NC) == 0x11111111, "mmu alias nc", "read %08x", peek(ALIAS_NC));
	CHECK(peek(ALIAS_WP) == 0x11111111, "mmu alias wp", "read %08x", peek(ALIAS_WP));

	/* A copyback write stays in the data cache. Its tags are physical
	 * (UM 4.1), so the page read through DTT1 sees it; so does a read
	 * through the noncachable alias, as a cache-inhibited access that matches
	 * a dirty line pushes it first (UM 4.3.2). */
	poke(ALIAS_CB, 0x22222222);
	v = peek(p);
	CHECK(v == 0x22222222, "dcache physical tags", "read %08x", v);
	v = peek(ALIAS_NC);
	CHECK(v == 0x22222222, "dcache copyback", "noncachable read %08x", v);
	poke(ALIAS_CB, 0x33333333);
	flush_cpu_dcache();
	CHECK(peek(ALIAS_NC) == 0x33333333, "dcache copyback", "read after CPUSH %08x", peek(ALIAS_NC));
	printf("dcache: copyback %s\n", v == 0x22222222 ? "pushed by a cache-inhibited access" : "stale");

	/* The write-protected alias faults on writes, as does anything on the
	 * invalid page; the fault address is the logical one (UM 8.4.6.4). */
	mmu_probe = ALIAS_WP;
	CHECK(trap_run(t_mmu_read) == 0, "mmu wp read", "faulted");
	expect("mmu wp write", t_mmu_write, 0x7008, 0, 0);
	expect_access_error("mmu wp write", 0, 1, ALIAS_WP);
	CHECK(peek(p) == 0x33333333, "mmu wp write", "the page changed to %08x", peek(p));
	mmu_probe = ALIAS_NONE;
	expect("mmu invalid read", t_mmu_read, 0x7008, 0, 0);
	expect_access_error("mmu invalid read", 1, 1, ALIAS_NONE);
	expect("mmu invalid write", t_mmu_write, 0x7008, 0, 0);
	expect_access_error("mmu invalid write", 0, 1, ALIAS_NONE);

	/* Used and modified bits, as the processor wrote them (UM 3.2.2.3): the
	 * copyback alias written, the noncachable one read only, the invalid
	 * one untouched. */
	d = descriptor(0);
	CHECK((d & (D_U | D_M)) == (D_U | D_M), "mmu U/M", "copyback descriptor %08x", d);
	d = descriptor(1);
	CHECK((d & (D_U | D_M)) == D_U, "mmu U/M", "noncachable descriptor %08x", d);
	d = descriptor(3);
	CHECK(d == PDT_INVALID, "mmu U/M", "invalid descriptor %08x", d);

	/* PTEST: the physical page, the cache mode, the flags; a TTR's hit. */
	mmusr = ptest_read(ALIAS_CB);
	CHECK((mmusr & 0xfffff000) == p && (mmusr & (MMUSR_B | MMUSR_CM | MMUSR_M | MMUSR_W | MMUSR_T | MMUSR_R))
		== (D_CM_CB | MMUSR_M | MMUSR_R), "ptest copyback", "MMUSR %08x", mmusr);
	mmusr = ptest_read(ALIAS_WP);
	CHECK((mmusr & (MMUSR_B | MMUSR_W | MMUSR_T | MMUSR_R)) == (MMUSR_W | MMUSR_R), "ptest wp",
		"MMUSR %08x", mmusr);
	mmusr = ptest_read(ALIAS_NONE);
	CHECK((mmusr & (MMUSR_B | MMUSR_T | MMUSR_R)) == 0, "ptest invalid", "MMUSR %08x", mmusr);
	mmusr = ptest_read(p);
	CHECK((mmusr & (MMUSR_B | MMUSR_T | MMUSR_R)) == (MMUSR_T | MMUSR_R), "ptest ttr",
		"MMUSR %08x", mmusr);

	MOVEC_TO("tc", 0);
	__asm__ __volatile__ ("pflusha" : : : "memory");
	MOVEC_TO("itt0", 0);
	MOVEC_TO("dtt1", 0);
	printf("mmu: off\n");
	printf("mmu: %s\n", failures == f0 ? "ok" : "FAIL");
}

/* --- FPU ------------------------------------------------------------------- */

/* Doubles in C: FPU instructions in an FPU build (__HAVE_68881__, the +fpu
 * variant), compiler-rt's soft float otherwise. Either way IEEE double, so
 * the same bits, computed here on the host. FPU builds run them only once
 * FNOP has shown an FPU. */

static uint64_t bits(double d)
{
	uint64_t b;
	memcpy(&b, &d, sizeof b);
	return b;
}

#define CHECK_BITS(d, want, what) do {                                  \
		uint64_t _b = bits(d);                                  \
		CHECK(_b == (want), what, "%08x%08x, expected %08x%08x",\
			(unsigned)(_b >> 32), (unsigned)_b,             \
			(unsigned)((want) >> 32), (unsigned)(want));    \
	} while (0)

/* volatile: computed at run time, not by the compiler. */
static volatile double one = 1.0, two = 2.0, three = 3.0, x15 = 1.5, x225 = 2.25;
static volatile double m75 = -7.5;
static volatile int i123 = 123456789;
static volatile float f1 = 1.0f, f3 = 3.0f;

/* Not inlined: in an FPU build, its prologue may save FPU registers, which
 * only an FPU can. */
static void __attribute__((noinline)) double_checks(void)
{
	volatile float q;

	CHECK_BITS(x15 * x225, 0x400b000000000000ULL, "double mul");      /* 3.375 */
	CHECK_BITS(one / three, 0x3fd5555555555555ULL, "double div");     /* 1/3 */
	CHECK_BITS(one - x225 + two, 0x3fe8000000000000ULL, "double add"); /* 0.75 */
	CHECK((int)m75 == -7, "double to int", "%d", (int)m75);
	CHECK_BITS((double)i123, 0x419d6f3454000000ULL, "int to double");
	CHECK(m75 < one && !(one < m75) && x15 != x225, "double compare", "wrong");
	q = f1 / f3;                    /* stored: rounded to float */
	CHECK_BITS((double)q, 0x3fd5555560000000ULL, "float div, to double");
	CHECK_BITS(one / (one - one), 0x7ff0000000000000ULL, "double div by 0");
}

#ifdef __HAVE_68881__
static void frestore_null(void)
{
	/* A null frame resets the FPU: FPCR, FPSR, any exception (UM 9.8). */
	static const uint32_t null_frame = 0;
	__asm__ __volatile__ ("frestore %0" : : "m" (null_frame));
}

static uint32_t fsave_word(void)
{
	uint32_t frame[32];
	__asm__ __volatile__ ("fsave %0" : "=m" (frame));
	return frame[0];
}

/* Not inlined: its prologue may save FPU registers, which only an FPU can. */
static void __attribute__((noinline)) fpu_checks(void)
{
	double d;
	uint32_t fpsr, w;

	__asm__ __volatile__ ("fsqrt.x %1, %0" : "=f" (d) : "f" (two));
	CHECK_BITS(d, 0x3ff6a09e667f3bcdULL, "fsqrt");

	/* Divide by zero, not enabled: an infinity, and DZ in FPSR's accrued
	 * exception byte (UM 9.2.3.4). d is an input of the FPSR read so that
	 * the read comes after the division. */
	d = one / (one - one);
	__asm__ __volatile__ ("fmove.l %%fpsr, %0" : "=d" (fpsr) : "f" (d));
	CHECK_BITS(d, 0x7ff0000000000000ULL, "fdiv by 0");
	CHECK(fpsr & 0x0010, "fpsr dz", "FPSR %08x", (unsigned)fpsr);

	/* An idle frame after an instruction: version $41, size 0 (UM 9.8,
	 * Figure 9-10); a null one after FRESTORE of null. */
	w = fsave_word();
	CHECK((w >> 16) == 0x4100, "fsave idle", "frame word %08x", (unsigned)w);
	frestore_null();
	w = fsave_word();
	CHECK((w >> 24) == 0x00, "fsave null", "frame word %08x", (unsigned)w);

	/* Unimplemented instructions, for the FPSP: vector 11, format $2, the
	 * next instruction's PC; then an unimplemented instruction frame, $41
	 * $30. */
	expect("fsin unimplemented", t_fsin, 0x202c, t_fsin_next, 0);
	w = fsave_word();
	CHECK((w >> 16) == 0x4130, "fsave unimplemented", "frame word %08x", (unsigned)w);
	frestore_null();
	expect("fmovecr unimplemented", t_fmovecr, 0x202c, t_fmovecr_next, 0);
	frestore_null();

	/* Enabled: an exception, on the next FPU instruction. */
	expect("fpu divide by zero", t_fpdz, 0x00c8, t_fpdz_insn, 0);
	frestore_null();
}
#endif

static void test_fpu(void)
{
	unsigned got = trap_run(t_fnop);

	if (got == 0x402c) {
		printf("fpu: absent\n");
#ifndef __HAVE_68881__
		double_checks();        /* soft float */
#endif
		return;
	}
	CHECK(got == 0, "fnop", "format/vector %04x", got);
	if (got)
		return;
	printf("fpu: present\n");
#ifdef __HAVE_68881__
	frestore_null();
	double_checks();
	fpu_checks();
#else
	double_checks();        /* soft float, though there is an FPU */
#endif
}

/* --- Main ------------------------------------------------------------------ */

int main(void)
{
	unsigned long vbr = get_vbr();
	int i;

	printf("\ncputest: RD68041 as a program sees it, CACR %08x\n", (unsigned)get_cacr());

	/* Every vector to trap_catch, but the reset ones and the LiteX interrupt. */
	memcpy(vectors, (void *)vbr, sizeof(vectors));
	for (i = 2; i < 64; i++)
		if (i != 26)
			vectors[i] = trap_catch;
	set_vbr((unsigned long)vectors);

	test_exceptions();
	test_icache();
	test_mmu();
	test_fpu();

	set_vbr(vbr);
	printf("cputest: %s (%d checks, %d failed)\n", failures ? "FAIL" : "PASS", checks, failures);
	printf("cputest: illegal instruction at %08x, for crt0\n", (unsigned)t_unhandled_insn);
	t_unhandled();
	return 0;
}
