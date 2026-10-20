# L6, step 2: a CPU clock of its own, and the FPU

After step 1 (`doc/l6-fifo-bridge.md`), the CPU's clock no longer needs to be
related to sys. Step 2 uses that freedom: the CPU's own MMCM, a faster clock.
It also adds RD68884, an MC68881, as the `+fpu` variants.

## The CPU's clock

`targets/qmtech_wukong.py --cpu-clk-freq F`, with the FIFO bridge:

- **Its own MMCM**, from the board's 50 MHz, with the main MMCM's reset (the
  stretched one, `crg()`). The main MMCM keeps exactly litex-boards' outputs:
  sys at 100 MHz, the DDR3's 400 MHz. Asking that MMCM for a 29 MHz output too
  had moved sys to 99.48 MHz, where LiteDRAM missed timing.
- **Asynchronous to Vivado**: `add_false_path_constraints(sys, cpu)`. Every
  path between the two goes through the bridge's FIFOs and MultiRegs, or is
  IPL, which the core synchronises itself. Without this, Vivado timed them as
  paths between two outputs of one MMCM, and at a ratio like 29:100 the
  nearest edges are very close (-1.97 ns).

With `--bus-bridge sync`, the CPU's clock still comes from the main MMCM, a
whole fraction of sys, as WishboneSyncCrossing needs.

### The bridge's reset, again

On the board at 30 MHz, the first build hung on `reboot`: one junk byte on the
UART, then nothing until a reload. The two clock domains now come out of reset
at different times, one MMCM each, and LiteX's FIFOs reset their pointers
synchronously, each side only once its clock runs. The side out of reset first
read the other side's pointer from before the reset, and carried out the stale
entries as requests (the junk byte was a write to the UART). Then the pointers
disagreed for good. With one MMCM, both domains left reset on the same edge,
which hid this.

Now the whole bridge, both halves and both FIFOs, is held in reset while either
domain is (`M68030ToFIFOToSoC`). The other domain's reset is released
asynchronously to this side's clock, but everything is idle then (pointers at
0, the processor in reset), so whichever edge it lands on, nothing moves. This
is what `stream.ClockDomainCrossing(with_common_rst=True)` is for. It uses
AsyncResetSynchronizers, which litex_sim gets wrong ("For upstream" in
`doc/l6-fifo-bridge.md`).

### Results, the Wukong at 30 MHz

| | 25 MHz (step 1) | 30 MHz |
|---|---|---|
| Timing, worst slack | 0.304 ns | 0.375 ns (CPU domain 0.546) |
| LUTs | 16,993 | 17,009 (27 %) |
| L3 | pass | pass |
| L4, fresh loads | 5/5 | 5/5 |
| BIOS memspeed, write | 11.8 MiB/s | 14.1 MiB/s |
| BIOS memspeed, read | 9.7 MiB/s | 11.7 MiB/s |
| `reboot`, five in a row | | the banner each time |

30 MHz is about RD68031's own limit on an Artix-7 (29.6 MHz out of context,
its `doc/critical-path.md`); here it closes with 0.546 ns in the CPU's domain.
30 MHz is now the default with the FIFO bridge (25 MHz with `--bus-bridge
sync`). A 0xFD byte still comes out right after `reboot`,
before the banner. It is most likely the UART's line while the clocks are in
reset, and the BIOS does not mind.

## RD68884, the FPU

Variants `standard+fpu` and `minimal+fpu` (`core.py`). They are built as
RD68031's MAME harness builds the pair (`Inputs/RD68031/sim/mame/rd68031_fpu.sv`):

- **The core** is built with `COPROCESSOR = 1`.
- **RD68884** (`rd68884_top`, the MC68881 build, `MODEL = 68881`) sits on the
  CPU's own bus and clock (`BUS_SYNC = 1`: its same-clock bus unit, no wait
  state), as a 32-bit port (SIZE and A0 strapped, FPU table 9-2).
- **Its chip select** decodes CpID 1's CPU space cycles: FC = 7, A19-A16 = 2,
  A15-A13 = 1 (UM 10.1).
- **Its DSACK** is wired-OR with the bridge's. The core reads its data lanes
  when it drives them, and the bridge's otherwise.
- **RESET** reaches it from the RESET instruction too.

The bridges (both) leave CpID 1's cycles alone (`coprocessors=`): no BERR, which
without an FPU is what makes them line F exceptions.

The RTL comes from `Inputs/RD68884` (the commit RD68031 itself pins,
0595f34), copied by `scripts/sync_pythondata.sh` into the same pythondata
package, `verilog/rd68884/` (`fpu_sources`, `fpu_verilator_config`).

**Floating point in software.** m68k-linux-gnu-gcc defaults to `-m68881`: C
doubles are FPU instructions. The `+fpu` variants keep that. The others build
with `-msoft-float` (`gcc_flags`), so that every program built against the SoC
(the BIOS, picolibc, libbase, the demo, cputest) computes in software, from
LiteX's own compiler-rt: its build, with the CPU's flags, has every routine GCC
calls (add, subtract, multiply, divide, compare in both precisions, extend,
truncate, the int conversions). LiteX links with `-nostdlib -nodefaultlibs`,
so this toolchain's libgcc stays out. It would be wrong here: Debian built it
for the default, `-m68881`. Its arithmetic routines are integer code, but its
conversions (`__floatsidf`, `__fixdfsi`, `__extendsfdf2`, `__truncdfsf2`,
`__fixunsdfsi`, ...) use FPU instructions and return in fp0, and there is no
soft-float multilib.

Checked: no FPU instruction in the soft-float BIOS, demo or cputest (but FNOP,
written as words, which is how cputest finds out), and cputest's double checks
on every variant: multiply, divide, add, the int conversions, comparisons, a
float divide, an infinity. They are compiled as FPU instructions on `+fpu` and
as compiler-rt calls elsewhere, with the same bits expected. Its instruction-level
FPU checks (inline assembly, and `t_fpdz` in `traps.S`, which the assembler
refuses under `-msoft-float`) are built for `+fpu` only (`__HAVE_68881__`).

Two test fixes came with it:

- **cputest's instruction cache check** ran its generated code twice, expecting
  the second run to hit the stale line. The cache is 16 lines, direct-mapped by
  A7-A4, and with compiler-rt linked in, the code run between the two runs
  shared that line and evicted it: "coherent" on `standard`. The generated code
  now freezes the cache itself (CACR FI, UM 6.3.1), from its own line, before
  returning. It also exercises FI.
- **`l4_test.py`** now builds the demo and cputest from scratch each time.
  Their Makefiles track sources, not the SoC's flags, and objects built with
  the old flags had stayed.

### Results

| | Simulation | Wukong, 30 MHz |
|---|---|---|
| L3, `standard+fpu` | pass | pass |
| L4, `standard+fpu` (cputest: 88 checks) | pass; also CPU at 0.3125 MHz with DDR3 | 5/5 |
| L4, `minimal+fpu` | pass | |
| L4, FPU-less variants ("fpu: absent"; soft float: 82 checks) | pass, `standard` and `minimal` | 5/5, then 3/3 with soft float |
| L4, `+fpu` variants (92 checks, with the double checks) | pass, `standard+fpu` and `minimal+fpu` | |
| Timing, worst slack | | 0.034 ns (sys); CPU domain, FPU included, 0.328 ns |
| Size | | 21,431 LUTs (34 %; the FPU about 4,400), 10 DSPs |

The worst path is in sys, at 100 MHz, the SoC's side: the FPU's 4,400 LUTs
make placement tighter there, not on the CPU's clock, which still has 0.328 ns.
