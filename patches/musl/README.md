# musl patches

Against **musl 1.2.6**, the release tarball, which buildroot 2026.08 builds for
the FPU-less defconfig (`buildroot/`, L9, `doc/l9-nofpu.md`). Buildroot applies
them after its own (`package/musl/`, none of which touch these files), through
that defconfig's `BR2_GLOBAL_PATCH_DIR`
(`buildroot/board/litex-rd68031/patches-softfloat/musl`, a link to this
directory).

## 0001-m68k-no-FPU-registers-in-setjmp-and-longjmp-without.patch

**What:** m68k's `setjmp` and `longjmp` keep `%fp2`-`%fp7` only when there is an
FPU (`__HAVE_68881__`, `__mcffpu__`, as musl's C sources test). The two files
become `.S`, so that they are preprocessed. The `jmp_buf` layout does not change.

**Why:**
- In musl's soft-float m68k ABI (`ld-musl-m68k-sf.so.1`) they still saved
  and restored the FPU registers. That is a line-F trap on a CPU without an
  FPU: an illegal instruction, or here a trip through the kernel's FPU
  emulator.
- These were the only FPU instructions left in the FPU-less root filesystem.
  `buildroot/board/litex-rd68031/check-softfloat.sh` found them.

**Upstream:** not sent (musl's git, 2026-10-21, still has them).
