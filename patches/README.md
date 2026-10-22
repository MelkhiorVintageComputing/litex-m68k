# Patches

Changes this project needs in code it does not own: the projects in `Inputs/`, the
repositories in `~/LITEX/`, and the release tarballs Linux and buildroot are built
from (`linux/`, `gcc/`, `musl/`). None is ever modified in place; the patches are applied to
disposable copies (`~/LITEX-68K/<repo>` for LiteX repositories, buildroot's own build
directories for gcc).

One subdirectory per project, named after it (`patches/litex/`, `patches/RD68031/`):

- `NNNN-short-description.patch`, made with `git format-patch` against the upstream
  commit named in the subdirectory's README, so it can be sent upstream as is;
- `README.md`: the upstream commit the series applies to, and for each patch what it
  changes, why this project needs it, and its upstream status (not sent, sent, merged).

- `linux/`: Linux 7.2.9, the m68k LiteX machine (L7, `doc/l7-linux.md`), and
  its devices from a device tree (L10, `doc/l10-devicetree.md`).
- `litex/`: LiteX, `litex_json2dts_linux` for m68k and the SD card driver's SCR
  on a big-endian CPU (L10); the copy is made by
  `scripts/patch_litex.sh`.
- `gcc/`: gcc 15.3.0, `--with-float=soft` for m68k, for the FPU-less
  buildroot (L9, `doc/l9-nofpu.md`).
- `musl/`: musl 1.2.6, m68k's setjmp and longjmp without FPU registers in
  the soft-float ABI (L9, `doc/l9-nofpu.md`).
- `RD68884/`: none now. L7's fix for FSAVE during the release of a
  register-to-register instruction was replaced by upstream's own, RD68884
  7a188db (`doc/l7-linux.md`). `scripts/patch_rd68884.sh` applies any that come.
