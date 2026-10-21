# gcc patches

Against **gcc 15.3.0**, the release tarball, which buildroot 2026.08 builds
(`buildroot/`, L9). Buildroot applies them after its own
(`package/gcc/15.3.0/`, none of which touch these files), through the
FPU-less defconfig's `BR2_GLOBAL_PATCH_DIR`
(`buildroot/board/litex-rd68031/patches-softfloat/gcc`, a link to this
directory), to both of its gcc builds. The FPU defconfig does not use them.

## 0001-m68k-accept-with-float-soft.patch

**What:** `--with-float=soft` for m68k: a gcc whose CPU default (here
`--with-cpu=68030`) would imply the 68881, made to default to soft float
instead. `-mhard-float` still turns the FPU on.

**Why:**
- RD68031 without RD68884 is a 68030 with no FPU. Its userspace must be
  soft-float, and so must the compiler's own libgcc.
- On m68k Linux the float ABI changes with the FPU: functions return
  floating-point values in `%fp0` with one, in `%d0`/`%d1` without. So a
  `-msoft-float` program cannot use a hard-float libgcc. Its C helpers
  (`__floatdidf`, ...) return in the wrong register and use FPU instructions.
- gcc has no way to build that compiler:
  - `-mcpu=68020` and up imply `-mhard-float`;
  - `m68k*-*-linux*` builds no soft-float multilib;
  - `config.gcc` rejects `--with-float` for m68k.
- Buildroot passes `--with-float=soft` only for arm, mips, powerpc and sparc. The
  FPU-less defconfig passes it itself (`BR2_EXTRA_GCC_CONFIG_OPTIONS`).

**Upstream:** not sent.

## 0002-m68k-musl-s-soft-float-dynamic-linker-for-msoft-floa.patch

**What:** with `-msoft-float`, and by default after `--with-float=soft` unless
`-mhard-float` is given, programs are linked against musl's soft-float
dynamic linker, `/lib/ld-musl-m68k-sf.so.1`. MIPS does the same.

**Why:** musl names its m68k soft-float ABI's dynamic linker with `-sf`
(`arch/m68k/reloc.h`). gcc's m68k configuration named only the hard-float one,
`/lib/ld-musl-m68k.so.1`, so no program of the soft-float root filesystem would
start.

**Upstream:** not sent.

## 0003-m68k-extended-precision-soft-float-routines-from-sof.patch

**What:** libgcc's m68k extended precision (`long double`, XFmode) routines,
rewritten on soft-fp. They replace `fpgnulib.c`'s EXTFLOAT part, which did
everything in double precision: even comparisons compared doubles.

**Why:** musl's `printf` converts doubles to decimal in `long double` and
needs it exact. Before this patch, on the FPU-less system (on the board):

| `printf` | before | after, and glibc |
|---|---|---|
| `%.6f` of π | 3.141592 | 3.141593 |
| `%.6f` of 2/3 | 0.666666 | 0.666667 |
| `%g` of 1e100 | 9.99999e+99 | 1e+100 |
| `%.20g` of 0.1 | 0.099999999999994315658 | 0.10000000000000000555 |

**How:**
- `extended-m68k.h` describes the m68k format to soft-fp. It differs from
  soft-fp's `extended.h` (x87) at the bottom of the range:
  - an exponent of 0 with the integer bit set is a normal number, 2⁻¹⁶³⁸³;
  - a denormal is 0.F × 2⁻¹⁶³⁸³ (MC68881UM §3.2; RD68884's quantum
    2⁻¹⁶⁴⁴⁶).

  That is one binade more than a 15-bit exponent leaves an IEEE-style
  format. So soft-fp computes in a format "M" with a 16-bit exponent biased
  by 16384, where every m68k value is exact, and packing maps back.
  Unnormals are normalized on input, as the MC68881 does.
- The words are 64 bits. With 32-bit words, the 67-bit working fraction
  does not reach the top of its four words, and soft-fp's addition clears
  a borrow's bits only in the word holding the implicit bit (`_FP_ADD`,
  `sub3`).
- The functions are soft-fp's TFmode ones, in that format. NaNs follow the
  MC68881: its default NaN, and the first operand's when both are NaNs.
- The cost is size: about 36 KB of code, against a few for the old
  routines, since 64-bit words are two registers on a 68030.

**Tested** on the host, the routines built with a 12-byte struct for
XFtype:
- Against x87 `long double`, which has the same 64-bit mantissa and the same
  rounding wherever the two formats agree: 98 million results of add,
  sub, mul, div and neg, plus the comparisons and the conversions, with
  none wrong.
- Against an exact rational reference (Python's `fractions`, round to
  nearest even on the m68k grid) for m68k's own region: exponent 0 with or
  without the integer bit, denormals, unnormal operands, results that
  underflow. 300,000 cases, none wrong.
- The tests are `test/`, not part of the patch: `test/run.sh`.

**Upstream:** not sent.

All three patches need a `Signed-off-by` or a copyright assignment before they
go to gcc-patches.
