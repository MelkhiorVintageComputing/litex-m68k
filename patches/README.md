# Patches

Changes this project needs in code it does not own: the projects in `Inputs/` and the
repositories in `~/LITEX/`. Neither is ever modified in place; the patches are applied
to disposable copies (`~/LITEX-68K/<repo>` for LiteX repositories).

One subdirectory per project, named after it (`patches/litex/`, `patches/RD68031/`):

- `NNNN-short-description.patch`, made with `git format-patch` against the upstream
  commit named in the subdirectory's README, so it can be sent upstream as is;
- `README.md`: the upstream commit the series applies to, and for each patch what it
  changes, why this project needs it, and its upstream status (not sent, sent, merged).

- `linux/`: Linux 7.2.9, the m68k LiteX machine (L7, `doc/l7-linux.md`).
- `RD68884/`: none now. L7's fix for FSAVE during the release of a
  register-to-register instruction was replaced by upstream's own, RD68884
  7a188db (`doc/l7-linux.md`). `scripts/patch_rd68884.sh` applies any that come.
