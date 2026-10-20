# LiteX-68K

m68k CPUs for the [LiteX](https://github.com/enjoy-digital/litex) SoC builder. The
first is the RD68031 soft-core (an MC68030, `Inputs/RD68031`), aiming at the LiteX
BIOS and bare-metal code in simulation and then on a QMTech Wukong (Artix-7 100T).
Hard 68k CPUs wired to FPGA pins and buildroot Linux come later.

`doc/plan.md` has the milestones and the design; `CLAUDE.md` the project's rules.

## Setting up

The projects under `Inputs/` are submodules. Fetch them without `--recursive`:
RD68031's own submodules (MAME, WinUAE, Hatari, ...) are its oracles, which this
project does not need.

```sh
git submodule update --init    # Inputs/: RD68031, RD68884, the manuals, the board docs
scripts/sync_pythondata.sh     # Inputs/RD68031's RTL -> ../pythondata-cpu-rd68031
scripts/setup_venv.sh          # ~/LITEX-68K/litex-venv, using ~/LITEX unmodified
source ~/LITEX-68K/litex-venv/bin/activate
```
