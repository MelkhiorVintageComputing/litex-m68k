# LiteX-68K

m68k CPUs in the LiteX SoC builder: soft-cores in the FPGA first (RD68031, an
MC68030, and optionally its RD68884 FPU; RD68041, an MC68040 or MC68LC040), real hard CPUs on FPGA pins later, with the
LiteX BIOS and bare-metal code as the first goal and buildroot Linux after it. The
milestones (L0-L10 for RD68031, L11-L17 for RD68041) and the design decisions are in `doc/plan.md`.

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

### So do RD68041's

`Inputs/RD68041/CLAUDE.md`: its oracles (`Inputs/RD68041/Inputs/ref/`) are never opened
either. `Inputs/QuadraFPGA` is a *bus witness*: a 68040 slave known to work against a
real MC68040, evidence of what the part does on the bus, but not a golden model. Our 040
bridge is written from the MC68040 manual (UM §7), for the same reason as the 030's.

### Mind the host's memory

15 GB, no swap. Heavy jobs (Verilator builds of the core, Vivado, Linux builds) run one
at a time, with `-j4` at most. Simulations already built, on one CPU and with little
memory (a litex_sim `Vsim`, the L1 bench's `Vtb`), may run up to 4 at a time.

## Layout

| Path | What |
|---|---|
| `doc/plan.md` | the plan: milestones L0-L10 and why |
| `doc/board.md` | the board for software work: the current bitstream (`~/LITEX-68K/bitstreams/`), `scripts/board.py` to load it and boot a kernel, interactive or unattended |
| `doc/l11-rd68041.md` | L11-L16: RD68041, from the bridge to Linux, and the core's bug L15 found |
| `doc/l18-smp.md` | L18-L23: two RD68041s on one bus (setup A), from the arbiter to the Wukong, and what NetBSD asked for |
| `doc/porting-bsd.md` | for porting a BSD OS: the boot protocol and a specification of each driver the SoC needs, from BSD and observed sources only (no GPL code) |
| `Inputs/` | RD68031, RD68884, IIsiFPGA (a MC68030 PDS card, whose bus bridge ours started from), RD68041 (an MC68040), QuadraFPGA (a 68040 PDS card, our 040 bridge's bus witness), and the Motorola and board documentation (add-only) |
| `litex_68k/m68030_bus.py` | the MC68030 bus record and `M68030ToWishbone`, L1-L5's bridge (`RD68031.bus_bridge = "sync"`) |
| `litex_68k/m68030_fifo.py` | L6: the FIFO bridge, the default: the CPU on any clock, burst line fills, main RAM on a 128-bit LiteDRAM port (`doc/l6-fifo-bridge.md`) |
| `litex_68k/m68k_fifo.py` | the FIFO bridges' SoC side (`FIFOToSoC`, its "wide" requests for 040 line writes) and the FIFOs, shared by both CPUs |
| `litex_68k/m68040_bus.py` | L11: the MC68040 bus record and `M68040ToFIFOToSoC`, from the 040 manual's section 7 (`doc/l11-rd68041.md`) |
| `litex_68k/m68040_smp.py` | L18: two MC68040s on one bus, RD68041's setup A: the corrected fairness arbiter, the bus resolved, the MI gate, the board's registers at $FFFF_0000 (`doc/l18-smp.md`) |
| `sim/bench/` | L1: core + bridge + Wishbone RAM, no LiteX SoC (`make -C sim/bench check`, `BRIDGE=fifo` for L6's on a CPU clock of its own; `doc/l1-bench.md`; `CPU=rd68041 [FPU=0]`, `programs040/`, L11; `SMP=1`, two of them running RD68041's `m14_smp.S`, L19) |
| `litex_68k/cpu/rd68031/` | the LiteX CPU class and its software files (`litex_68k.cpu.register()`; `doc/l2-litex.md`) |
| `litex_68k/cpu/rd68041/` | L12: RD68041's (`standard`: the MC68LC040, `standard+fpu`: the MC68040); crt0's DTT0 for I/O, CINVA/CPUSHA; L20: `+smp`, two of them (CPU 1's start in crt0, `smp.h`, `cpu_irq_mask1`; L23: `timer1`) |
| `targets/sim.py` | `litex_sim` with RD68031, or `--cpu-type rd68041` (`--uart-tx-clocks` paces its UART like a baud rate; `--cpu-clk-freq` puts the CPU on a clock of its own) |
| `sim/unit/` | migen unit tests of `core.py`'s bus crossing and time-out, two clocks, in migen's simulator: no Verilator build (`make -C sim/unit check`); L18: the SMP arbiter (and against RD68041's under iverilog), the devices, the MI gate |
| `targets/qmtech_wukong.py` | L5/L6: the Wukong V3 board, DDR3 (CPU 30 MHz on its own MMCM, SoC 100 MHz) or block RAM, `--bus-bridge fifo` (default) or `sync` (CPU 25 MHz), `--cpu-variant standard+fpu` for RD68884 (`doc/l6-fpu.md`), `--build` (Vivado) and `--load` (JTAG, through Vivado's hardware manager); `doc/l5-wukong.md`; `--cpu-type rd68041` (25 MHz, L14; `--cpu-variant standard+smp`, two LC040s, L21); L10: litex-boards' `--with-ethernet --with-sdcard` (the L2 cache then off; `doc/l10-devicetree.md`) |
| `sim/litex/` | L2: smoke test in a LiteX SoC under `litex_sim` (`make -C sim/litex check`); L3: the BIOS, driven over the UART (`make -C sim/litex bios`; `doc/l3-bios.md`); L4: LiteX's demo and `cputest/` (exceptions, caches, MMU), loaded by the BIOS through `litex_term` (`make -C sim/litex l4`; `doc/l4-bare-metal.md`); the tests take `--cpu-clk-freq` and `--sdram` (DDR3, 128-bit native port) |
| `linux/` | L7: Linux 7.2.9 and a BusyBox initramfs (`fetch.sh`, `build.sh` into `build/linux`; the kernel tree is `~/LITEX-68K/src/linux-7.2.9`, made from the tarball and `patches/linux`); `doc/l7-linux.md` |
| `buildroot/` | the br2-external tree (`LITEX68K`), buildroot 2026.08, the kernel from `patches/linux`, a cpio initramfs. L8: `litex_rd68031_fpu_defconfig`, glibc, hard float (`doc/l8-buildroot.md`); L9: `litex_rd68031_nofpu_defconfig`, musl, soft float with `patches/gcc` and `patches/musl` (`doc/l9-nofpu.md`). L16: `litex_rd68041_{fpu,nofpu}_defconfig`, the 68040's. `build.sh fpu\|nofpu\|040-fpu\|040-nofpu` into `~/LITEX-68K/build/buildroot-<variant>` |
| `sw/litexboot/` | L7: the loader the BIOS boots with the images: vmlinux's segments, the bootinfo, the jump; `images.py` lays the images out (`images.json` for `litex_term --images` and litex_sim `--ram-init`); L10: with `--dtc`, the SoC's device tree too; L23: `+smp`'s records, and `bidump/`, a test kernel printing its bootinfo |
| `sim/litex/l7_test.py` | L7: Linux to a shell and a command set, in litex_sim or on a board (`--serial`); L8: `--login` for buildroot's getty; `--variant standard` for the FPU-less one (L9); L10: the device tree (with buildroot's dtc), `--sdcard` (litex_sim's emulator), `--io` (a board's Ethernet and SD card; `--no-sd`, Ethernet only), `--sd-install` and `--sd-boot` (the board booting from its SD card) |
| `sim/litex/fpswitch/` | two FPU tasks switched by timer0, the FPU saved as Linux does (`fpswitch_test.py`); the reduction of L7's FSAVE freeze |
| `sim/litex/cputest040/` | L13: cputest for RD68041 (the 040's frames, MMU, copyback, FPU traps); the tests take `--cpu-type rd68041` (`CPU=rd68041` in the Makefile) |
| `sim/litex/smptest/` | L20: the two-processor test (`+smp`): IPIs, locks, coherence, the snooped instruction cache, a TLB shootdown, per-CPU interrupts, the bus counters; `smp_test.py` runs it (`--serial` for a board) |
| `sim/litex/trace/` | debugging only: RD68041's user-mode trace in litex_sim (`RD68041_TRACE=1` at build time), which found L15's bug |
| `patches/<project>/` | patches to `Inputs/` projects or `~/LITEX` repositories, each documented: `linux/` (the m68k LiteX machine, and its devices from a device tree), `litex/` (json2dts for m68k; the SD card driver's SCR on a big-endian CPU), `gcc/` (m68k soft float: `--with-float=soft`, musl's `-sf` loader, exact `long double`; host tests in `gcc/test/`), `musl/` (setjmp without an FPU), `RD68041/` and `RD68884/` have none (L15's and L7's fixes are upstream) |
| `scripts/setup_venv.sh` | creates `~/LITEX-68K/litex-venv` |
| `scripts/board.py` | load a bitstream snapshot, stop the BIOS, boot a kernel by serial or from the SD card, a console; `--until`/`--send` for unattended runs (`doc/board.md`) |
| `scripts/snapshot_bitstream.sh` | copies a board build (bitstream, CSR maps, software) to `~/LITEX-68K/bitstreams/NAME` |
| `scripts/patch_litex.sh` | makes `~/LITEX-68K/litex` (or another LiteX repository) from `~/LITEX` and `patches/litex`; `setup_venv.sh` then prefers it |
| `scripts/patch_rd68884.sh` | when `patches/RD68884` has patches: makes `~/LITEX-68K/RD68884`, `Inputs/RD68884` with them and its microcode regenerated |
| `scripts/patch_rd68041.sh` | when `patches/RD68041` has patches: makes `~/LITEX-68K/RD68041`, `Inputs/RD68041` with them |
| `scripts/sync_pythondata_rd68041.sh` | fills `../pythondata-cpu-rd68041` from `Inputs/RD68041` (or its patched copy) |
| `scripts/sync_pythondata.sh` | fills `../pythondata-cpu-rd68031` from `Inputs/RD68031`, and the FPU from `Inputs/RD68884` (or its patched copy, `scripts/patch_rd68884.sh`) |
| `../pythondata-cpu-rd68031` | the RD68031 and RD68884 RTL snapshot LiteX builds from (`get_data_mod("cpu", "rd68031")`) |
| `../pythondata-cpu-rd68041` | RD68041's (`get_data_mod("cpu", "rd68041")`) |

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
