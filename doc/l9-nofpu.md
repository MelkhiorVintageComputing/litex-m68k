# L9: Linux on RD68031 without an FPU

RD68031 alone (the `standard` variant, no RD68884), with a soft-float
userspace built by buildroot. The SoC, litexboot, the kernel's LiteX machine
and the way the images reach main RAM are L7's and L8's (`doc/l7-linux.md`,
`doc/l8-buildroot.md`).

```sh
buildroot/build.sh nofpu              # -> ~/LITEX-68K/build/buildroot-nofpu/images
sim/litex/l7_test.py --variant standard --login \
    --linux-dir ~/LITEX-68K/build/buildroot-nofpu/images \
    --build-dir sim/litex/build/standard/l9              # in litex_sim
sim/litex/l7_test.py --variant standard --login \
    --linux-dir ~/LITEX-68K/build/buildroot-nofpu/images \
    --serial /dev/ttyUSB0 --speed 1000000 \
    --build-dir build/qmtech_wukong_30m_1m               # on the Wukong, BIOS at its prompt
```

## What a soft-float 68030 needed

- **A compiler that defaults to soft float** (`patches/gcc`).
  - **Why `-msoft-float` is not enough:**
    - In buildroot, a 68030 is always hard float: `-mcpu=68030` implies the
      68881, and `BR2_SOFT_FLOAT` is offered only for the 68000 and ColdFire.
    - Adding `-msoft-float` to every compilation would not do either. On m68k
      Linux the float ABI changes with the FPU: functions return
      floating-point values in `%fp0` with one, in `%d0`/`%d1` without. The
      compiler's own libgcc would still be hard-float, its C helpers
      (`__floatdidf`, ...) returning in the wrong register and using FPU
      instructions.
  - **Patch 0001** makes gcc accept `--with-float=soft` for m68k, as it does
    for arm or mips. Configured so, no CPU defaults to the FPU, and libgcc is
    soft-float. The defconfig passes the option (`BR2_EXTRA_GCC_CONFIG_OPTIONS`),
    since buildroot does so only for arm, mips, powerpc and sparc.
  - **Patch 0002** names musl's soft-float dynamic linker,
    `/lib/ld-musl-m68k-sf.so.1`. gcc had only the hard-float name, so the
    first build's programs asked for a dynamic linker that the target did not
    have.
- **Exact `long double` in software** (`patches/gcc` 0003). This one was
  found on the board, by the command set's awk printing π as 3.141592.
  - **The cause:** m68k's `long double` is the 80-bit extended format, with
    or without an FPU. libgcc's software routines for it converted every
    operand to `double`, comparisons included. musl's `printf` converts to
    decimal in `long double` and needs it exact. So the values were right
    (`%a` matched the host bit for bit), but their decimal conversion was
    wrong: 2/3 printed as 0.666666, 1e100 as 9.99999e+99, 0.1 as
    0.099999999999994316.
  - **The fix:** those routines rebuilt on soft-fp, libgcc's own IEEE
    emulation, in the m68k format. It differs from x87's at the bottom of
    the range: an exponent of 0 with the integer bit set is a normal
    number, and denormals sit one binade lower (MC68881UM §3.2).
  - **Tested on the host** (`patches/gcc/test/run.sh`): against x87
    `long double` where the two formats agree, 98 million results, and
    against an exact rational reference for m68k's own region. None
    were wrong.
  - On the way, a limit of soft-fp's addition with 32-bit words: it clears
    a borrow only in the word holding the implicit bit. So these routines
    use 64-bit words.
- **musl as the C library.** glibc's m680x0 port needs the FPU; its soft-float
  m68k configuration is ColdFire's. musl has a soft-float m68k ABI (`-sf`),
  chosen from the compiler's own macros (`__HAVE_68881__`), so it follows
  gcc's default with no option of its own.
- **musl's setjmp and longjmp** (`patches/musl`). They saved and restored
  `%fp2`-`%fp7` in every ABI, the only FPU instructions left in the root
  filesystem. They are now kept only when there is an FPU.
- **The kernel's FPU emulator** (`linux-nofpu.config`: `CONFIG_M68KFPU_EMU`).
  Without it, an m68k kernel built for an FPU saves it at every task switch
  (FSAVE, a line-F trap here) and stops at boot: "no FPU". With it, both are
  skipped when the bootinfo says there is no FPU, which litexboot's probe
  finds. The userspace never runs the emulator.
- **A check on what was built** (`check-softfloat.sh`, the defconfig's
  post-build script). The emulator would run a hard-float program silently,
  only slower, so the build fails on any FPU instruction in the target's ELF
  files. It also fails on a program whose dynamic linker the target does not
  have. It found the setjmp instructions. Its dynamic linker check was
  added after the loader name, and catches it.

## The pieces

- `buildroot/configs/litex_rd68031_nofpu_defconfig`: as the FPU one but
  for the toolchain (musl, `--with-float=soft`), the kernel configuration
  (`linux-nofpu.defconfig`), `patches-softfloat` in `BR2_GLOBAL_PATCH_DIR`
  (links to `patches/gcc` and `patches/musl`), and the post-build check.
- `buildroot/board/litex-rd68031/make-linux-defconfig.sh` generates both kernel
  configurations; `linux-nofpu.defconfig` is `linux.defconfig` plus the
  emulator.
- `buildroot/build.sh fpu|nofpu` builds either, into
  `~/LITEX-68K/build/buildroot-<variant>`.
- `l7_test.py --variant standard`: litexboot must find no FPU, and
  `/proc/cpuinfo` say `FPU: none`. With `--login`, the C library checked is
  musl's soft-float dynamic linker, and Lua's results are computed in
  software. Their expected values are the host's, the same as for the FPU.
  Both variants also check `printf`'s decimal rounding (`%.6f` of 2/3,
  `%g` of 1e100, `%.17g` of 0.1).

## Results

| | litex_sim | Wukong V3 (CPU 30 MHz, 1 Mbaud) |
|---|---|---|
| build (`build.sh nofpu`, from scratch, `-j4`) | about 23 min | |
| images | `rootfs.cpio.gz` 1.38 MB, `vmlinux` 3.9 MB | |
| upload | preloaded | 175 s |
| litexboot to the login | 1250-1650 s | 41 s |
| the two awk (3000 terms) | 1315 s | |
| Lua's 2000-term loop | 2767 s | |
| `l7_test.py --variant standard --login` | PASS (7340 s) | PASS |

- **Stress on the board:** two Lua at once (30,000 terms of `sin(i)*cos(i/3)`,
  three times) and two awk at once (20,000 square roots, twice). Every result
  was the host's (0.732234782, 1885688.586251), with the timer preempting them.
- **The FPU build is unchanged:** L8 still passes on the board, now with the
  `printf` check too (glibc and the 68881 print the same three numbers).
- **Software float is slow in the simulation.** Lua's loop is the longest
  command, so `l7_test.py` gives each command 7200 s by default without the
  FPU (1800 s with it). The first run, at 1800 s, stopped there.
