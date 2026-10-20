// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

/* cputest: the MC68030 as a program loaded by the LiteX BIOS sees it.
 *
 *   exceptions  every exception a program can cause on purpose, each checked
 *               for its frame format, vector, and the addresses in the frame
 *               (UM 8.1, 8.4)
 *   icache      code written as data runs stale until CACR's CI, the cache
 *               frozen meanwhile (FI) so that nothing else evicts it (UM 6.1.1)
 *   mmu         a three-level table with early termination: identity mapping
 *               of everything, plus two aliases of one page, a write-protected
 *               alias and an invalid page above them; U and M updates, PTEST,
 *               and the data cache's logical tags seen through the aliases,
 *               with and without write allocation (UM 9, 6.1.2.1)
 *   fpu         whether there is one (FNOP, a line F exception without);
 *               C doubles, bit for bit: the FPU's with an FPU build (-m68881),
 *               compiler-rt's without (-msoft-float, the FPU-less variants);
 *               with RD68884: a constant ROM value, a transcendental, FPSR,
 *               an FPU exception and the MC68881's idle FSAVE frame
 *               (FPU 2, 4, 6)
 *
 * The cache checks print what they see ("stale", or "coherent" when a cache
 * is not built in), and the FPU check whether there is one ("fpu: present" or
 * "fpu: absent"); sim/litex/l4_test.py knows which variant should say what.
 * Then "cputest: PASS" or "cputest: FAIL", and a last illegal instruction left
 * to crt0's handler, which reports it and stops. */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <system.h>

/* traps.S */
extern unsigned trap_run(void (*fn)(void));
extern void trap_catch(void);
extern volatile uint8_t trap_frame[20];

#define DECLARE(t) extern void t(void), t##_insn(void), t##_next(void)
DECLARE(t_illegal); DECLARE(t_zerodiv); DECLARE(t_chk); DECLARE(t_trapv);
DECLARE(t_trap5); DECLARE(t_linea); DECLARE(t_linef); DECLARE(t_priv);
DECLARE(t_trace); DECLARE(t_addrerr); DECLARE(t_berr_read); DECLARE(t_berr_write);
DECLARE(t_mmu_read); DECLARE(t_mmu_write); DECLARE(t_unhandled);
DECLARE(t_fnop); DECLARE(t_fpdz);

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
#define FRAME_ADDRESS  frame_long(8)    /* format $2: the instruction address */
#define FRAME_SSW      frame_word(10)   /* formats $A, $B */
#define FRAME_FAULT    frame_long(16)   /* formats $A, $B: data cycle fault address */

#define SSW_DF  0x0100
#define SSW_RW  0x0040                  /* 1: read */
#define SSW_FC  0x0007

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
 * the instruction address must be insn. Format $A and $B are both accepted
 * for bus faults: which one the processor uses depends on where in the
 * instruction the fault happens (UM 8.1.2). */
static void expect(const char *name, void (*t)(void), unsigned fv, void (*pc)(void), void (*insn)(void))
{
	unsigned got = trap_run(t);
	unsigned format = got >> 12, want_format = fv >> 12;
	int format_ok = format == want_format || (want_format == 0xb && format == 0xa);

	CHECK(got != 0, name, "no exception");
	if (got == 0)
		return;
	CHECK(format_ok && (got & 0xfff) == (fv & 0xfff), name,
		"format/vector %04x, expected %04x", got, fv);
	if (pc)
		CHECK(FRAME_PC == (uint32_t)pc, name, "PC %08x, expected %08x", FRAME_PC, (unsigned)pc);
	if (format == 2 && insn)
		CHECK(FRAME_ADDRESS == (uint32_t)insn, name,
			"instruction address %08x, expected %08x", FRAME_ADDRESS, (unsigned)insn);
}

/* A data fault: the SSW says so, with the direction and the supervisor data
 * space (FC 5), and the frame has the address. */
static void expect_data_fault(const char *name, int read, uint32_t address)
{
	unsigned want = SSW_DF | (read ? SSW_RW : 0) | 5;
	CHECK((FRAME_SSW & (SSW_DF | SSW_RW | SSW_FC)) == want, name,
		"SSW %04x, expected DF, RW and FC as in %04x", FRAME_SSW, want);
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
	expect("address error", t_addrerr, 0xb00c, 0, 0);
	expect("bus error, read", t_berr_read, 0xb008, 0, 0);
	expect_data_fault("bus error, read", 1, 0x90000000);
	expect("bus error, write", t_berr_write, 0xb008, 0, 0);
	expect_data_fault("bus error, write", 0, 0x90000000);

	printf("exceptions: %s\n", failures == f0 ? "ok" : "FAIL");
}

/* --- Instruction cache ----------------------------------------------------- */

static uint16_t code[8] __attribute__((aligned(16)));

/* Run code[], with d1 = cacr: the code ends with MOVEC D1,CACR. */
static int run_code(unsigned long cacr)
{
	register unsigned long d1 __asm__("d1") = cacr;
	register int d0 __asm__("d0");
	__asm__ __volatile__ ("jsr (%2)" : "=d" (d0) : "d" (d1), "a" (code) : "memory");
	return d0;
}

/* The instruction cache is direct-mapped by A7-A4 (UM 6.1.1): any code run
 * between two runs of code[] on code[]'s line evicts it, which depends on
 * where the linker put everything. So code[] freezes the cache itself (CACR
 * FI, UM 6.3.1), from its own line, already loaded: frozen, the cache keeps
 * its lines, and hits still hit. */
static void test_icache(void)
{
	int f0 = failures, first, second, third;
	unsigned long cacr = get_cacr();

	code[0] = 0x7001;               /* moveq #1, d0 */
	code[1] = 0x4e7b;               /* movec d1, cacr */
	code[2] = 0x1002;
	code[3] = 0x4e75;               /* rts */
	flush_cpu_icache();
	first = run_code(cacr | CACR_FI);
	code[0] = 0x7002;               /* moveq #2, d0: memory only, the */
	second = run_code(cacr | CACR_FI); /* instruction cache keeps "#1" */
	set_cacr(cacr);                 /* not frozen */
	flush_cpu_icache();
	third = run_code(cacr);

	CHECK(first == 1, "icache", "first run returned %u", first);
	CHECK(second == 1 || second == 2, "icache", "second run returned %u", second);
	CHECK(third == 2, "icache", "run after CI returned %u", third);
	printf("icache: %s, %s\n", second == 1 ? "stale until CI" : "coherent",
		failures == f0 ? "ok" : "FAIL");
}

/* --- MMU ------------------------------------------------------------------- */

/* TC: enabled, 4 KiB pages, no initial shift, TIA 8, TIB 4, TIC 8 (UM 9.7.2):
 * level A (A31-A24) maps 16 MiB blocks, all of them identity by early
 * termination page descriptors, except 0x50xxxxxx, which goes through level
 * B (A23-A20, 1 MiB) to level C (A19-A12, 4 KiB pages). */
#define TC_E        0x80000000
#define TC_VALUE    (TC_E | (12 << 20) | (0 << 16) | (8 << 12) | (4 << 8) | (8 << 4))

#define DT_INVALID  0
#define DT_PAGE     1
#define DT_SHORT    2                   /* table of 4-byte descriptors */
#define D_WP        0x04
#define D_U         0x08
#define D_M         0x10
#define D_CI        0x40

#define MMUSR_B     0x8000
#define MMUSR_W     0x0800
#define MMUSR_I     0x0400
#define MMUSR_M     0x0200
#define MMUSR_N     0x0007

#define ALIAS       0x50000000
#define ALIAS0      (ALIAS + 0x0000)    /* the page, read-write */
#define ALIAS1      (ALIAS + 0x1000)    /* the page again, read-write */
#define ALIAS_WP    (ALIAS + 0x2000)    /* the page again, write-protected */
#define ALIAS_NONE  (ALIAS + 0x3000)    /* invalid */

static uint32_t level_a[256] __attribute__((aligned(16)));
static uint32_t level_b[16]  __attribute__((aligned(16)));
static uint32_t level_c[256] __attribute__((aligned(16)));
static uint32_t page[1024]   __attribute__((aligned(4096)));

static void pmove_to_tc(uint32_t tc)
{
	__asm__ __volatile__ ("pmove %0, %%tc" : : "m" (tc) : "memory");
}

static void pmove_to_crp(uint32_t limit_dt, uint32_t table)
{
	uint32_t crp[2] = { limit_dt, table };
	__asm__ __volatile__ ("pmove %0, %%crp" : : "m" (crp) : "memory");
}

static uint16_t ptest_read(uint32_t address)
{
	uint16_t mmusr;
	__asm__ __volatile__ ("ptestr #5, (%1), #7\n\tpmove %%psr, %0"
		: "=m" (mmusr) : "a" (address) : "memory");
	return mmusr;
}

static uint32_t peek(uint32_t a) { return *(volatile uint32_t *)a; }
static void poke(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* The descriptors, as the processor left them in memory: the data cache may
 * hold the values written when the tables were built. */
static uint32_t descriptor(int i)
{
	flush_cpu_dcache();
	return ((volatile uint32_t *)level_c)[i];
}

/* Read through ALIAS0, write through ALIAS1, read through ALIAS0 again, with
 * write allocation on or off; return the last read, then clear the cache. */
static uint32_t alias_read(int wa)
{
	unsigned long cacr = get_cacr();
	uint32_t v;

	set_cacr((wa ? cacr | CACR_WA : cacr & ~CACR_WA) | CACR_CD);
	poke(ALIAS0, 0x11111111);
	(void)peek(ALIAS0);
	poke(ALIAS1, 0x22222222);
	v = peek(ALIAS0);
	set_cacr(cacr | CACR_CD);
	return v;
}

static void test_mmu(void)
{
	int f0 = failures, i;
	uint32_t p = (uint32_t)page, d, stale, mmusr;

	for (i = 0; i < 256; i++)
		level_a[i] = (i << 24) | (i >= 0x80 ? D_CI : 0) | DT_PAGE;
	level_a[ALIAS >> 24] = (uint32_t)level_b | DT_SHORT;
	memset(level_b, 0, sizeof(level_b));
	level_b[0] = (uint32_t)level_c | DT_SHORT;
	memset(level_c, 0, sizeof(level_c));
	level_c[0] = p | DT_PAGE;
	level_c[1] = p | DT_PAGE;
	level_c[2] = p | D_WP | DT_PAGE;
	level_c[3] = DT_INVALID;

	/* The root pointer: lower limit 0 (no limit), short descriptors. Loading
	 * TC flushes the address translation cache. */
	pmove_to_crp(0x80000000 | DT_SHORT, (uint32_t)level_a);
	pmove_to_tc(TC_VALUE);
	printf("mmu: on, TC %08x\n", (unsigned)TC_VALUE);

	/* Everything still runs (code, stack, UART), and the aliases are the page. */
	poke(p, 0x12345678);
	CHECK(peek(ALIAS0) == 0x12345678, "mmu alias 0", "read %08x", peek(ALIAS0));
	CHECK(peek(ALIAS1) == 0x12345678, "mmu alias 1", "read %08x", peek(ALIAS1));
	CHECK(peek(ALIAS_WP) == 0x12345678, "mmu alias wp", "read %08x", peek(ALIAS_WP));

	/* The data cache's tags are logical addresses, and it is direct mapped by
	 * A7-A4 (UM 6.1.2): two aliases with the same page offset share a line.
	 * With write allocation (CACR WA, as crt0 sets it), a write through one
	 * alias takes the line over, and a read through the other misses: no
	 * stale data (UM 6.1.2.1). Without it, the write misses and leaves the
	 * line alone, and the other alias reads stale data until CD. */
	stale = alias_read(1);
	CHECK(stale == 0x22222222, "dcache alias, WA", "read %08x", stale);
	stale = alias_read(0);
	CHECK(stale == 0x11111111 || stale == 0x22222222, "dcache alias", "read %08x", stale);
	CHECK(peek(ALIAS0) == 0x22222222, "dcache alias", "read after CD %08x", peek(ALIAS0));
	CHECK(peek(p) == 0x22222222, "dcache alias", "physical %08x", peek(p));
	printf("dcache: write allocation coherent, without it %s\n",
		stale == 0x11111111 ? "stale until CD" : "coherent");

	/* The write-protected alias faults on writes, as does anything on the
	 * invalid page; the fault address is the logical one. */
	mmu_probe = ALIAS_WP;
	CHECK(trap_run(t_mmu_read) == 0, "mmu wp read", "faulted");
	expect("mmu wp write", t_mmu_write, 0xb008, 0, 0);
	expect_data_fault("mmu wp write", 0, ALIAS_WP);
	CHECK(peek(p) == 0x22222222, "mmu wp write", "the page changed to %08x", peek(p));
	mmu_probe = ALIAS_NONE;
	expect("mmu invalid read", t_mmu_read, 0xb008, 0, 0);
	expect_data_fault("mmu invalid read", 1, ALIAS_NONE);
	expect("mmu invalid write", t_mmu_write, 0xb008, 0, 0);
	expect_data_fault("mmu invalid write", 0, ALIAS_NONE);

	/* Used and modified bits, as the processor wrote them (UM 9.5.1.1): the
	 * two aliases written, the write-protected one read only (a refused write
	 * does not set M), the invalid one untouched. */
	d = descriptor(0);
	CHECK((d & (D_U | D_M)) == (D_U | D_M), "mmu U/M", "alias 0 descriptor %08x", d);
	d = descriptor(1);
	CHECK((d & (D_U | D_M)) == (D_U | D_M), "mmu U/M", "alias 1 descriptor %08x", d);
	d = descriptor(2);
	CHECK((d & (D_U | D_M)) == D_U, "mmu U/M", "write-protected descriptor %08x", d);
	d = descriptor(3);
	CHECK(d == DT_INVALID, "mmu U/M", "invalid descriptor %08x", d);

	/* PTEST to the bottom (level 7): three levels for the aliases, the
	 * flags from the tree. */
	mmusr = ptest_read(ALIAS0);
	CHECK((mmusr & (MMUSR_B | MMUSR_W | MMUSR_I | MMUSR_M | MMUSR_N)) == (MMUSR_M | 3), "ptest alias 0",
		"MMUSR %04x, expected %04x", mmusr, MMUSR_M | 3);
	mmusr = ptest_read(ALIAS_WP);
	CHECK((mmusr & (MMUSR_B | MMUSR_W | MMUSR_I | MMUSR_M | MMUSR_N)) == (MMUSR_W | 3), "ptest wp",
		"MMUSR %04x, expected %04x", mmusr, MMUSR_W | 3);
	mmusr = ptest_read(ALIAS_NONE);
	CHECK((mmusr & (MMUSR_B | MMUSR_I | MMUSR_N)) == (MMUSR_I | 3), "ptest invalid",
		"MMUSR %04x, expected %04x", mmusr, MMUSR_I | 3);
	mmusr = ptest_read((uint32_t)page);
	CHECK((mmusr & (MMUSR_B | MMUSR_I | MMUSR_N)) == 1, "ptest identity",
		"MMUSR %04x, expected %04x", mmusr, 1);

	pmove_to_tc(0);
	printf("mmu: off\n");
	printf("mmu: %s\n", failures == f0 ? "ok" : "FAIL");
}

/* --- FPU ------------------------------------------------------------------- */

/* Doubles in C: FPU instructions in an FPU build (__HAVE_68881__, the
 * cross-compiler's default, the +fpu variants), compiler-rt's soft float
 * otherwise. Either way IEEE double, so the same bits, computed here on the
 * host. FPU builds run them only once FNOP has shown an FPU. */

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
	/* A null frame resets the FPU (FPU 6.4.2): FPCR, FPSR, any exception. */
	static const uint32_t null_frame = 0;
	__asm__ __volatile__ ("frestore %0" : : "m" (null_frame));
}

/* Not inlined: its prologue may save FPU registers, which only an FPU can. */
static void __attribute__((noinline)) fpu_checks(void)
{
	double d;
	uint32_t fpsr, frame[64];

	__asm__ __volatile__ ("fsqrt.x %1, %0" : "=f" (d) : "f" (two));
	CHECK_BITS(d, 0x3ff6a09e667f3bcdULL, "fsqrt");
	__asm__ __volatile__ ("fmovecr #0, %0" : "=f" (d));            /* pi */
	CHECK_BITS(d, 0x400921fb54442d18ULL, "fmovecr pi");
	__asm__ __volatile__ ("fsin.x %1, %0" : "=f" (d) : "f" (d / 6.0));
	CHECK(bits(d) == 0x3fe0000000000000ULL || bits(d) == 0x3fdfffffffffffffULL,
		"fsin", "sin(pi/6) %08x%08x", (unsigned)(bits(d) >> 32), (unsigned)bits(d));

	/* Divide by zero, not enabled: an infinity, and DZ in FPSR's accrued
	 * exception byte (FPU 2.2.3; its exception byte is the last
	 * instruction's, which may be a move of the result). d is an input of the
	 * FPSR read so that the read comes after the division. */
	d = one / (one - one);
	__asm__ __volatile__ ("fmove.l %%fpsr, %0" : "=d" (fpsr) : "f" (d));
	CHECK_BITS(d, 0x7ff0000000000000ULL, "fdiv by 0");
	CHECK(fpsr & 0x0010, "fpsr dz", "FPSR %08x", (unsigned)fpsr);

	/* An idle frame, after an instruction: the MC68881's, version $1F, $18
	 * bytes after its format word (FPU 6.4.2). */
	__asm__ __volatile__ ("fsave %0" : "=m" (frame));
	CHECK((frame[0] >> 16) == 0x1f18, "fsave", "frame word %08x", (unsigned)frame[0]);
	frestore_null();

	/* Enabled: an exception, on the next FPU instruction. */
	expect("fpu divide by zero", t_fpdz, 0x00c8, t_fpdz_insn, 0);
	frestore_null();
}
#endif

static void test_fpu(void)
{
	unsigned got = trap_run(t_fnop);

	if (got == 0x002c) {
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

	printf("\ncputest: RD68031 as a program sees it, CACR %08x\n", (unsigned)get_cacr());

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
