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
at a time, with `-j4` at most. Simulations already built, on one CPU and with little
memory (a litex_sim `Vsim`, the L1 bench's `Vtb`), may run up to 4 at a time.

## Layout

| Path | What |
|---|---|
| `doc/plan.md` | the plan: milestones L0-L6 and why |
| `Inputs/` | RD68031, RD68884, IIsiFPGA (a MC68030 PDS card, whose bus bridge ours started from), and the Motorola and board documentation (add-only) |
| `litex_68k/m68030_bus.py` | the MC68030 bus record and `M68030ToWishbone`, L1-L5's bridge (`RD68031.bus_bridge = "sync"`) |
| `litex_68k/m68030_fifo.py` | L6: the FIFO bridge, the default: the CPU on any clock, burst line fills, main RAM on a 128-bit LiteDRAM port (`doc/l6-fifo-bridge.md`) |
| `sim/bench/` | L1: core + bridge + Wishbone RAM, no LiteX SoC (`make -C sim/bench check`, `BRIDGE=fifo` for L6's on a CPU clock of its own; `doc/l1-bench.md`) |
| `litex_68k/cpu/rd68031/` | the LiteX CPU class and its software files (`litex_68k.cpu.register()`; `doc/l2-litex.md`) |
| `targets/sim.py` | `litex_sim` with RD68031 (`--uart-tx-clocks` paces its UART like a baud rate; `--cpu-clk-freq` puts the CPU on a clock of its own) |
| `sim/unit/` | migen unit tests of `core.py`'s bus crossing and time-out, two clocks, in migen's simulator: no Verilator build (`make -C sim/unit check`) |
| `targets/qmtech_wukong.py` | L5/L6: the Wukong V3 board, DDR3 (CPU 30 MHz on its own MMCM, SoC 100 MHz) or block RAM, `--bus-bridge fifo` (default) or `sync` (CPU 25 MHz), `--cpu-variant standard+fpu` for RD68884 (`doc/l6-fpu.md`), `--build` (Vivado) and `--load` (JTAG, through Vivado's hardware manager); `doc/l5-wukong.md` |
| `sim/litex/` | L2: smoke test in a LiteX SoC under `litex_sim` (`make -C sim/litex check`); L3: the BIOS, driven over the UART (`make -C sim/litex bios`; `doc/l3-bios.md`); L4: LiteX's demo and `cputest/` (exceptions, caches, MMU), loaded by the BIOS through `litex_term` (`make -C sim/litex l4`; `doc/l4-bare-metal.md`); the tests take `--cpu-clk-freq` and `--sdram` (DDR3, 128-bit native port) |
| `linux/` | L7: Linux 7.2.9 and a BusyBox initramfs (`fetch.sh`, `build.sh` into `build/linux`; the kernel tree is `~/LITEX-68K/src/linux-7.2.9`, made from the tarball and `patches/linux`); `doc/l7-linux.md` |
| `sw/litexboot/` | L7: the loader the BIOS boots with the images: vmlinux's segments, the bootinfo, the jump; `images.py` lays the images out (`images.json` for `litex_term --images` and litex_sim `--ram-init`) |
| `sim/litex/l7_test.py` | L7: Linux to a shell and a command set, in litex_sim or on a board (`--serial`) |
| `sim/litex/fpswitch/` | two FPU tasks switched by timer0, the FPU saved as Linux does (`fpswitch_test.py`); the reduction of L7's FSAVE freeze |
| `patches/<project>/` | patches to `Inputs/` projects or `~/LITEX` repositories, each documented: `linux/` (the m68k LiteX machine), `RD68884/` (FSAVE in its microcode) |
| `scripts/setup_venv.sh` | creates `~/LITEX-68K/litex-venv` |
| `scripts/patch_rd68884.sh` | makes `~/LITEX-68K/RD68884`, `Inputs/RD68884` with `patches/RD68884` and its microcode regenerated |
| `scripts/sync_pythondata.sh` | fills `../pythondata-cpu-rd68031` from `Inputs/RD68031`, and the FPU from the patched RD68884 (`scripts/patch_rd68884.sh`) |
| `../pythondata-cpu-rd68031` | the RD68031 and RD68884 RTL snapshot LiteX builds from (`get_data_mod("cpu", "rd68031")`) |

## Environment

- `source ~/LITEX-68K/litex-venv/bin/activate`. LiteX's console scripts (`litex_sim`,
  `litex_term`, ...) are wrappers in that venv's `bin/`.
- Toolchain: `m68k-linux-gnu-gcc` 14.2 (Debian); no m68k-elf. Verilator 5.032,
  iverilog 12.0, `libevent-dev` and `libjson-c-dev` (for `litex_sim`), Vivado 2025.2 in `/opt/Xilinx/2025.2/`.
- Board: Wukong V3, JTAG on a Xilinx Platform Cable USB II (Vivado's `hw_server`; no
  openocd), UART on `/dev/ttyUSB0`. A loaded bitstream lasts until power-off or the PROG
  key; nothing here writes the board's flash.
- Documentation: `Inputs/doc/MC68030_Doc_More_Readable/MC68030UM_split/`,
  `.../MC68881UM_split/`, `Inputs/doc/QM_XC7A100T_WUKONG_BOARD/`. Read PDFs with
  `pdftotext -layout <file> -`.
