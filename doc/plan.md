# LiteX-68K: RD68031 soft-core in LiteX (firmware + bare-metal first)

## Context

We want m68k CPUs as first-class LiteX citizens: soft-cores now, real hard CPUs
(MC68030 on FPGA pins) later, buildroot Linux eventually. First target: the
**RD68031** soft-core (`Inputs/RD68031`, a SystemVerilog MC68030) running the
**LiteX BIOS and bare-metal programs**, first in Verilator simulation
(`litex_sim`), then on the **QMTech Wukong** (xc7a100t, `litex-boards` target
`qmtech_wukong`).

Facts established during exploration that shape the plan:

- **RD68031 is mature** (M15: boots NetBSD and SunOS 4.1.1 in MAME, Linux 7.2 on
  its own `rd-virt` machine). Its top level (`rd68031_top`) is the **real 68030
  pin interface** (FC, A, D, SIZ, AS/DS, DSACK[1:0], STERM, CIIN/CBREQ/CBACK, IPL/AVEC,
  BERR/HALT/RESET), with every tri-state pin split into `_i/_o/_oe`.
  Parameters: `ICACHE_ENTRIES`, `DCACHE_ENTRIES`, `COPROCESSOR`,
  `ADDR_HIZ_BETWEEN_CYCLES`. RTL file list and order: `Inputs/RD68031/Makefile:39-56`
  (`rtl/rd68031_pkg.sv`, `rtl/gen/*_pkg.sv`, `rtl/gen/*_rom.sv`, then the modules).
- **It uses both clock edges** (one bus state per half-period; DSACK/BERR/AVEC
  sampled on the falling edge, STERM/CIIN/CBACK on the rising edge). Fmax on
  xc7a100t-1 ≈ **29.6 MHz** with critical paths that are half-period; requires
  50 % duty cycle.
- Reset: fetches SSP from `$0` and PC from `$4` (FC=6). Interrupts: IPL levels +
  autovector via AVEC on the IACK cycle (FC=7, A19:16=$F).
- Reference bus slaves to learn from (not to copy into RTL constraints):
  `Inputs/RD68031/sim/models/rd68031_slave.sv`, `Inputs/RD68031/sim/virt/cpu_rtl.cpp`.
  RD68884 FPU attaches in CPU space (FC=7, A19:16=2, CpID 1), see
  `Inputs/RD68031/sim/mame/rd68031_fpu.sv:82-117`.
- **LiteX needs no modification to accept an out-of-tree CPU**: `collect_cpus()`
  (`~/LITEX/litex/litex/soc/cores/cpu/__init__.py:144-194`) also scans `os.getcwd()`
  for `<name>/core.py`, and a target script can set `cpu.CPUS["rd68031"] = RD68031`.
  `CPU_DIRECTORY` (crt0.S, irq.h, system.h, boot-helper.S) is taken from the class's
  file location, so it works out of tree. Big-endian CPUs (lm32, mor1kx) are already
  handled (ROM packing, CRC, picolibc endian, compiler_rt).
- Toolchain: only `m68k-linux-gnu-gcc` 14.2 is installed (no m68k-elf, no picolibc).
  LiteX links with `-nostdlib`, so a linux-gnu triple is acceptable (mor1kx does it);
  picolibc (built by LiteX via meson) already has `machine/m68k`.
- `~/LITEX/litex-venv` is all PEP-660 editable installs (migen is a plain copy):
  a second venv can `pip install -e` the same unmodified repos.
- `litex-boards` has `qmtech_wukong` (platform + target): 50 MHz clk, MMCM sys clk
  (default 100 MHz), DDR3 via A7DDRPHY 1:4, UART E3/F3, SPI flash, GMII, HDMI, SD.

## Key design decision: a pin-level 68030-bus → Wishbone bridge

The core stays untouched (it is in `Inputs/`); the LiteX CPU wrapper instantiates
`rd68031_top` plus a **bridge that acts as a 68030 bus slave and a Wishbone master**.
This is deliberate: the same bridge, with real I/O buffers and synchronisers in
front, is exactly what the FPGA needs later for a **hard MC68030** on the pins. So the
bridge is written to the MC68030 UM §7 protocol, not to RD68031 internals.

Bridge behaviour (v1, simple and correct; optimise later):
- Clocked on the CPU clock; ignores `_oe` (internal FPGA), or `ADDR_HIZ_BETWEEN_CYCLES=0`.
- FC≠7: start a Wishbone cycle on AS; byte-addressed WB (`addressing="byte"`), `sel`
  from SIZ/A1:A0 for a 32-bit port (lane 0 = D31:24 = `sel[3]`, native big-endian, no
  swapping); read data returned on all four lanes.
- Terminate with DSACK=00 (32-bit port) from a rising-edge register (as
  `rd68031_slave.sv` does); WB `err` or a timeout → BERR. Phase 2 optimisation: STERM
  (2-clock cycles) and burst fills (CBREQ/CBACK → WB incrementing burst / LiteDRAM).
- CIIN asserted for addresses in `io_regions` (CSR space), so the CPU's caches never
  hold device registers.
- FC=7 CPU space: IACK → AVEC; coprocessor space → BERR (F-line emulation) until
  RD68884 is added; everything else → BERR. Never forwarded to Wishbone.
- IRQ controller in the wrapper: LiteX `interrupt[31:0]` (level, active high) +
  a mask register → one configurable IPL level (autovectored); pending/mask readable
  for `irq.h`.

## Repository layout (this repo, `litex-68k`)

```
CLAUDE.md / README.md          rules: Inputs/ add-only, ~/LITEX untouched, patches/
Inputs/                        add-only references (RD68031, RD68884, doc)
patches/<project>/NNNN-*.patch + README.md per project (what, why, upstream status)
litex_68k/cpu/rd68031/         out-of-tree LiteX CPU: core.py, crt0.S, boot-helper.S,
                               irq.h, system.h, __init__.py
rtl/                           bridge (m68k_bus_wb.sv or migen), IRQ controller
sim/                           standalone core+bridge testbench (Verilator), programs
targets/                       sim.py (wraps litex_sim), qmtech_wukong.py (wraps board target)
scripts/                       setup_venv.sh, sync_pythondata.sh
```
- `../pythondata-cpu-rd68031`: standard pythondata layout
  (`pythondata_cpu_rd68031/__init__.py` with `data_location`, `verilog/` holding a
  snapshot of `Inputs/RD68031/rtl/` + `rtl/gen/` + `rd68031.vlt`, and the RD68031
  commit it came from). Filled by `scripts/sync_pythondata.sh`; core.py uses
  `get_data_mod("cpu", "rd68031")`.
- CPU registration: target scripts import our class and set
  `litex.soc.cores.cpu.CPUS["rd68031"]` before argument parsing — no LiteX patch.
- Licensing: RD68031 is CERN-OHL-S-2.0; our own files (wrapper, bridges, targets,
  tests) are BSD-2-Clause, as LiteX is (confirmed by the user, 2026-10-20).

## Milestones

**L0 — Infrastructure.** `scripts/setup_venv.sh` creates `~/LITEX-68K/litex-venv`
(Python 3.13, `pip install -e` each `~/LITEX/<repo>`, `pip install ~/LITEX/migen`,
`pip install -e ../pythondata-cpu-rd68031`). Any repo that needs a patch is instead
cloned into `~/LITEX-68K/<repo>`, patched from `patches/<repo>/`, and installed from
there. Write `CLAUDE.md` with the project rules (incl. RD68031's ban on reading
`Inputs/RD68031/Inputs/ref/` when writing RTL, and its 15 GB / one-heavy-job rule).
Populate pythondata-cpu-rd68031. Check `make lint` of RD68031 still passes from the
snapshot under Verilator 5.032.

**L1 — Bridge, standalone.** *Done: `doc/l1-bench.md`; the bridge started from
IIsiFPGA's PDS bridge (`Inputs/IIsiFPGA`).* `rd68031_top` + bridge + Wishbone RAM + a trivial WB
"tty" register in a Verilator testbench (no LiteX). Run RD68031's own bare-metal
programs re-linked for this map (e.g. `sim/programs/arith.c` with an adapted `crt0.S`)
and a dedicated bus test: every SIZ/A1:A0 combination, misaligned operands (dynamic
sizing), RMW (TAS/CAS), BERR on unmapped, IACK/autovector, CIIN on IO. Optionally a
WB protocol checker.

**L2 — LiteX CPU class, sim, no BIOS.** *Done: `doc/l2-litex.md`.* `litex_68k/cpu/rd68031/core.py`:
`family="m68k"`, `endianness="big"`, `data_width=32`,
`gcc_triple=("m68k-elf","m68k-linux-gnu")`, `gcc_flags="-mcpu=68030 -D__rd68031__"`,
`linker_output_format="elf32-m68k"`, `nop="nop"`, `io_regions={0x8000_0000:0x8000_0000}`,
`interrupt=Signal(32)`, one byte-addressed WB master in `periph_buses`, variants
(`standard` = caches on, `minimal` = caches out, later `+fpu`). Memory map keeps
ROM at 0 (the reset vectors live at the start of the BIOS image). `targets/sim.py`
runs `litex_sim` with `--cpu-type rd68031 --integrated-main-ram-size …`; first test:
a hand-written ROM program writing to the UART CSR.

**L3 — LiteX BIOS in sim.** *Done: `doc/l3-bios.md`; no LiteX patch needed.* CPU directory files:
- `crt0.S`: 68k vector table at the top of `.text` (SSP=`_fstack`, PC=`_start`, all
  exceptions → a dumping handler, autovector level N → save regs, `jsr isr`, `rte`),
  copy `.data`, clear `.bss`, enable caches (CACR), `litex_startup_init`, `main`.
- `irq.h`: `irq_getie/setie` via SR interrupt mask; `irq_getmask/setmask/pending`
  via the wrapper's IRQ-controller registers.
- `system.h`: `flush_cpu_icache/dcache` via CACR (CI/CD bits), `busy_wait*`.
- `boot-helper.S`: disable IRQs, jump to address.
Bring up picolibc and compiler_rt builds for m68k (big-endian); any LiteX build fix
needed becomes `patches/litex/`. Goal: BIOS banner, `help`, `mem_test`, `mem_speed`,
CRC check, UART and timer0 interrupts working in `litex_sim`.

**L4 — Bare-metal programs in sim.** *Done: `doc/l4-bare-metal.md`; no LiteX patch needed.* LiteX's `litex_bare_metal_demo` built for m68k,
loaded by `serialboot` through `litex_term` over sim serial2tcp / `--ram-init`;
plus a small 68030-specific test set (MMU on with identity tables, caches, exceptions
→ BIOS dump) reusing ideas from `Inputs/RD68031/sim/programs/`.

**L5 — Wukong FPGA.** *Done, both stages: `doc/l5-wukong.md`.* `targets/qmtech_wukong.py` wraps the board target with our CPU.
Clocking: CPU in its own `cpu` clock domain (~25 MHz, 50 % duty from the MMCM), the
rest of the SoC at sys (≥ 75–100 MHz, needed by A7DDRPHY), Wishbone/LiteDRAM CDC
between them (LiteDRAM ports accept `clock_domain=`; evaluate LiteX's WB CDC options).
Stage 1: integrated ROM+RAM only, UART BIOS. Stage 2: DDR3 main RAM, serialboot. Use
RD68031's `scripts/rd68031.xdc` approach (single clock, false path on reset); expect
the CPU to close near 25–29 MHz. Vivado 2025.2 from `/opt/Xilinx/2025.2`; Vivado runs
are heavy jobs, run alone.

**L6 — Performance & FPU (optional).** STERM termination, burst fills into LiteDRAM,
`memory_buses` direct to LiteDRAM; RD68884 at CPU-space CpID 1 with `COPROCESSOR=1`
(`+fpu` variant), mirroring `sim/mame/rd68031_fpu.sv`.
- *Step 1, done: `doc/l6-fifo-bridge.md`.* The CPU's clock decoupled from sys by a
  FIFO bridge (requests one way, lines of 128 bits and tags the other), so it may be
  any clock, a hard CPU's own later; posted memory writes; burst line fills; main
  RAM on a 128-bit LiteDRAM native port. On the Wukong, memspeed 11.8/9.7 MiB/s
  (L5: 9.2/7.8).
- *Step 2, done: `doc/l6-fpu.md`.* The CPU on an MMCM of its own, at 30 MHz on the
  Wukong (memspeed 14.1/11.7 MiB/s), the bridge reset as one across both domains,
  and RD68884 as the `+fpu` variants (cputest's FPU checks; L3/L4 on the board).
  The FPU-less variants build with `-msoft-float`, on LiteX's compiler-rt.

**L7 — Linux to a shell.** *Done, in litex_sim and on the Wukong: `doc/l7-linux.md`.* Linux 7.2.9 with a new m68k
machine, `MACH_LITEX` (`patches/linux`): bootinfo records for the SoC's cores, the
CPU's interrupt CSRs as a chained controller, timer0 as clock event and clock
source, LiteUART as console. `sw/litexboot` loads vmlinux and an initramfs and
writes the bootinfo. Images go by `litex_term --images` at 1 Mbaud on the board,
and by `--ram-init` in litex_sim. A BusyBox initramfs and `sim/litex/l7_test.py`'s
command set, modelled on RD68031's rd-virt. Two FPU processes preempting each other
froze the machine: an FSAVE that RD68884's microcode never answered
(reduced to `sim/litex/fpswitch`; fixed upstream in RD68884 7a188db).

**L8 — buildroot.** *Done, in litex_sim and on the Wukong: `doc/l8-buildroot.md`.* A br2-external tree (`buildroot/`), buildroot's internal glibc
toolchain for the 68030 with hard float, the kernel from `patches/linux`, a cpio
initramfs; `litex_rd68031_fpu_defconfig`. Gate: boots to a login on the Wukong, and
`l7_test.py` passes against it.

**L9 — the FPU-less variant.** *Done, in litex_sim and on the Wukong: `doc/l9-nofpu.md`.*
RD68031 alone with a soft-float buildroot (`litex_rd68031_nofpu_defconfig`): musl,
gcc defaulting to soft float, libgcc's `long double` made exact (`patches/gcc`),
musl's setjmp without FPU registers (`patches/musl`), the kernel with its FPU
emulator, and a post-build check that no FPU instruction is left.

**L10 — a device tree, Ethernet and the SD card.** *Done, in litex_sim and on the Wukong:
`doc/l10-devicetree.md`.* litexboot passes the device tree LiteX's json2dts makes
(`patches/litex`: its m68k branch) in a bootinfo record; the kernel unflattens it
for the drivers (`patches/linux` 0002), the machine's core staying on bootinfo.
LiteEth and LiteSDCard on the Wukong (`--with-ethernet --with-sdcard`, L2 cache
off), the SD emulator in litex_sim.

**L11-L17 — RD68041, an MC68040.** *Done, in litex_sim and on the Wukong:
`doc/l11-rd68041.md`.* The same ladder for RD68041 (`Inputs/RD68041`; the
MC68LC040 with `FPU = 0`):
- L11: `../pythondata-cpu-rd68041`, and the MC68040 bridge
  (`litex_68k/m68040_bus.py`, from UM section 7, QuadraFPGA as a bus witness)
  on the L1 bench (`sim/bench` `CPU=rd68041`).
- L12-L13: the LiteX CPU class, the BIOS, and cputest040 in litex_sim.
- L14: the Wukong at 25 MHz, both variants.
- L15: Linux with RD68031's userspace, one kernel for the 030 and the 040. It
  found a defect in RD68041's instruction fetch, fixed upstream (RD68041
  2d6eb14). NetBSD then found another one in it (RD68041 4300689).
- L16: buildroot built for the 68040 (`litex_rd68041_{fpu,nofpu}_defconfig`).
- L17: the documentation.

**Later, noted.** Upstreaming `arch/m68k/litex`, `patches/litex`, `patches/gcc`,
`patches/musl` (the RD68884 and RD68041 fixes are upstream: 7a188db,
2d6eb14 and 4300689).

**Later (out of this plan's scope, noted for design choices now):**
- Hard CPU: reuse the bridge as the FPGA-side slave with pin I/O + synchronisers;
  a `category="hardcore"` CPU class with no RTL.

## Verification

- L0: `source ~/LITEX-68K/litex-venv/bin/activate && python -c "import litex, pythondata_cpu_rd68031"`;
  `litex_sim --help` lists `rd68031` when run via `targets/sim.py`.
- L1: Verilator testbench passes the bus test and the re-linked programs; results
  checked against expected console output / exit codes.
- L2–L4: `targets/sim.py` reaches the BIOS prompt, commands answer, interrupts fire;
  serialboot of the demo app prints its banner; automated with `--non-interactive`
  + expected-output scripts.
- L5: Vivado timing met; BIOS prompt over the Wukong UART; `mem_test` on DDR3;
  serialboot the demo.
