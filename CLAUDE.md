# LiteX-68K

m68k CPUs in the LiteX SoC builder: soft-cores in the FPGA first (RD68031, an
MC68030, and optionally its RD68884 FPU), real hard CPUs on FPGA pins later, with the
LiteX BIOS and bare-metal code as the first goal and buildroot Linux after it. The
milestones (L0-L6) and the design decisions are in `doc/plan.md`.

## Hard rules

### `Inputs/` is add-only

New reference material may be added under `Inputs/`; nothing already there is ever
modified. A fix one of those projects needs is a patch in `patches/<project>/`
(see `patches/README.md`), applied to a copy outside `Inputs/`.

### `~/LITEX/` is never modified

Not its files, not its git state, and not by side effect: no `pip install -e` against
it (that rewrites `*.egg-info`), no build trees inside it, no `__pycache__` written
into it (our venv sends bytecode to `~/LITEX-68K/.pycache`). Our venv,
`~/LITEX-68K/litex-venv`, is made by `scripts/setup_venv.sh` and puts the unmodified
repositories on `sys.path` through a `.pth` file. A repository that needs a change is
cloned to `~/LITEX-68K/<repo>`, patched from `patches/<repo>/`, and the setup script
then prefers that copy. The copies are disposable; the patches are what is kept.

### RD68031's own rules still apply to its code

`Inputs/RD68031/CLAUDE.md`: its reference implementations (`Inputs/RD68031/Inputs/ref/`)
are oracles, never sources — do not open them when writing RTL. Our bridge is written
from the MC68030 manual (UM §7, bus operation), not from RD68031 internals, because it
must later drive a real MC68030.

### Mind the host's memory

15 GB, no swap. Heavy jobs (Verilator builds of the core, Vivado, Linux builds) run one
at a time, with `-j4` at most.

## Layout

| Path | What |
|---|---|
| `doc/plan.md` | the plan: milestones L0-L6 and why |
| `Inputs/` | RD68031, RD68884, IIsiFPGA (a MC68030 PDS card, whose bus bridge ours started from), and the Motorola and board documentation (add-only) |
| `litex_68k/m68030_bus.py` | the MC68030 bus record and `M68030ToWishbone`, the bridge |
| `sim/bench/` | L1: core + bridge + Wishbone RAM, no LiteX SoC (`make -C sim/bench check`; `doc/l1-bench.md`) |
| `litex_68k/cpu/rd68031/` | the LiteX CPU class and its software files (`litex_68k.cpu.register()`; `doc/l2-litex.md`) |
| `targets/sim.py` | `litex_sim` with RD68031 |
| `sim/litex/` | L2: smoke test in a LiteX SoC under `litex_sim` (`make -C sim/litex check`); L3: the BIOS, driven over the UART (`make -C sim/litex bios`; `doc/l3-bios.md`); L4: LiteX's demo and `cputest/` (exceptions, caches, MMU), loaded by the BIOS through `litex_term` (`make -C sim/litex l4`; `doc/l4-bare-metal.md`) |
| `patches/<project>/` | patches to `Inputs/` projects or `~/LITEX` repositories, each documented |
| `scripts/setup_venv.sh` | creates `~/LITEX-68K/litex-venv` |
| `scripts/sync_pythondata.sh` | fills `../pythondata-cpu-rd68031` from `Inputs/RD68031` |
| `../pythondata-cpu-rd68031` | the RD68031 RTL snapshot LiteX builds from (`get_data_mod("cpu", "rd68031")`) |

## Environment

- `source ~/LITEX-68K/litex-venv/bin/activate`. LiteX's console scripts (`litex_sim`,
  `litex_term`, ...) are wrappers in that venv's `bin/`.
- Toolchain: `m68k-linux-gnu-gcc` 14.2 (Debian); no m68k-elf. Verilator 5.032,
  iverilog 12.0, `libevent-dev` and `libjson-c-dev` (for `litex_sim`), Vivado 2025.2 in `/opt/Xilinx/2025.2/`.
- Documentation: `Inputs/doc/MC68030_Doc_More_Readable/MC68030UM_split/`,
  `.../MC68881UM_split/`, `Inputs/doc/QM_XC7A100T_WUKONG_BOARD/`. Read PDFs with
  `pdftotext -layout <file> -`.
