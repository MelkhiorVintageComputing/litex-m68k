# Patches

Changes this project needs in code it does not own: the projects in `Inputs/` and the
repositories in `~/LITEX/`. Neither is ever modified in place; the patches are applied
to disposable copies (`~/LITEX-68K/<repo>` for LiteX repositories).

One subdirectory per project, named after it (`patches/litex/`, `patches/RD68031/`):

- `NNNN-short-description.patch`, made with `git format-patch` against the upstream
  commit named in the subdirectory's README, so it can be sent upstream as is;
- `README.md`: the upstream commit the series applies to, and for each patch what it
  changes, why this project needs it, and its upstream status (not sent, sent, merged).

There are no patches yet.
