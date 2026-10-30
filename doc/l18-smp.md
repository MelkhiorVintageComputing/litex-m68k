# L18-L23: two RD68041s on one bus

Two RD68041s share one MC68040 bus. It runs the LiteX BIOS and bare-metal
programs: no m68k Linux does SMP.

The board is RD68041's own two-processor board, `Inputs/RD68041/doc/smp.md`,
in its setup A:
- UPA1-UPA0 wired straight to SC1-SC0;
- UM 7.8.2.1's fairness arbiter, as RD68041's
  `doc/um-7-8-2-1-fairness-arbitration.md` corrects it;
- a gate on MI.

The software policy that goes with it is UM 4.5's: every page both
processors use is written through with U = 01, and copyback only for
private pages, with U = 00.

```sh
make -C sim/unit check                                  # L18: the arbiter, the devices, the MI gate
make -C sim/bench check CPU=rd68041 SMP=1 [FPU=0]       # L19: RD68041's m14_smp.S on two cores
make -C sim/bench smp-negative CPU=rd68041 SMP=1        #      the runs that must fail
sim/litex/smp_test.py --variant standard+smp --sdram    # L20: smptest in litex_sim
targets/qmtech_wukong.py --cpu-type rd68041 --cpu-variant standard+smp \
    --uart-baudrate 1000000 --with-ethernet --with-sdcard \
    --output-dir build/qmtech_wukong_rd68041_smp_25m_1m_io \
    --vivado-place-directive ExtraTimingOpt \
    --vivado-post-place-phys-opt-directive AggressiveExplore \
    --vivado-route-directive Explore --build
```

## The variants

| `--cpu-variant` | The processors |
|---|---|
| `standard+smp` | two MC68LC040s: the one that fits the Wukong |
| `standard+fpu+smp` | two MC68040s: litex_sim only, since they are 112 % of the Wukong's LUTs |

Both build the software with `-D__rd68041_smp__`. The uniprocessor variants are
unchanged.

## The board (`litex_68k/m68040_smp.py`)

Everything is in the processors' clock domain.

- **The bus, resolved.** Nothing inside an FPGA is three-state:
  - a bussed active-low line (TS, TIP, BB, TA, LOCK, LOCKE, CIOUT) is the AND
    of what its drivers drive. A driver whose enable is off counts as the
    board's pull-up.
  - A, the attributes and D are whichever driver enables them. D is the
    bridge's read data when nobody drives it.
  - SC is the master's UPA, straight (setup A), and 00 when nobody drives the
    attributes.
- **The MI gate.**
  - Memory, here the bridge and the board's devices, waits for both MIs to be
    negated (UM 7.9).
  - A processor asserts MI from its reset until its first bus cycle. So each
    processor's MI is ignored from its reset until it first drives TS
    (smp.md item 1).
  - Without the gate, CPU 1 held in reset stops memory for good (the bench's
    `NOMIGATE=1`).
- **`M68040FairArbiter`:**
  - the corrected table, with registered grants and a clock with neither grant
    at every change of ownership;
  - a locked sequence is kept whole, LOCKE ends it a cycle early;
  - `use_locke` and `gapless` (the printed grants) are kept for tests.
- **The bridge's side.** `M68040ToFIFO` takes `mi_n`:
  - a read is requested at once, and its answer waits for MI;
  - a posted write's TA waits for it;
  - an acknowledge or a line write waits in a state of its own before its
    first TA.

  It also takes `ignore`, the devices' window, which it leaves alone. Without
  them, its Verilog is structurally the one before L18.
- **Interrupts:**
  - The board's interprocessor interrupts are level 5, autovectored
    (vector 29).
  - LiteX's lines reach each CPU through a mask of its own: `cpu_irq_mask` for
    CPU 0 and `cpu_irq_mask1` for CPU 1, at level 2 (vector 26).
  - The level-2 request is made in sys, synchronised into the CPUs' domain,
    and combined with the IPI in a register there.

### The registers, at `$FFFF_0000`

A long word each, in the I/O region; the board answers reads with TCI.

| Offset | Register | |
|---|---|---|
| `$00` | WHOAMI | R: the processor making the access (the one the arbiter granted) |
| `$04` | IPI_SET | W: interrupt CPU n (n = bit 0) |
| `$08` | IPI_ACK | W: acknowledge one's own |
| `$0C` | RELEASE | W: non-zero lets CPU 1 out of reset, 0 holds it there; cleared by a reset of the SoC (with BOOT_PC, BOOT_SP and the IPIs) |
| `$10` | BOOT_PC | RW: where crt0 sends CPU 1; 0: it waits |
| `$14` | BOOT_SP | RW: its stack |
| `$18` | NCPUS | R: 2 |
| `$20…$48` | the counters | R; a write to `$20` clears them all (below) |

The first four are those of RD68041's bench (`rd68041_smpdev`), so its
program runs here unchanged.

**The counters**, in order:
- the checks, which must stay 0: contention (two drivers on one line or group),
  turnaround (the address group handed over with no clock between), owner (a
  TS from a processor not granted), atomicity (the other processor's TS inside
  a locked sequence), and intervene (a long word a snooping processor
  terminated, which setup A never does);
- the statistics: the longest wait for a grant, the transfers of CPU 0 and of
  CPU 1, the grants given, the locked sequences, and the clocks a transfer
  waited for MI.

### CPU 1's start

- CPU 1 is held in reset (RSTI) until RELEASE. The BIOS runs on CPU 0 as
  before.
- A reset from the SoC controller (`ctrl`: the BIOS's `reboot`, or a CPU
  reset alone) resets both processors and clears RELEASE, BOOT_PC, BOOT_SP
  and the IPIs: CPU 1 stays in reset until the next program releases it,
  instead of starting again at the old BOOT_PC (L23).
- Released, CPU 1 reads the ROM's reset vectors, and crt0 reads WHOAMI. If it
  is non-zero, crt0 loads BOOT_SP, jumps to BOOT_PC, and touches nothing of the
  BIOS's.
- The program it goes to sets its own VBR, TTRs, MMU and caches.
- `smp.h` has `smp_whoami`, `smp_ipi`, `smp_ipi_ack`, `smp_start_cpu1`,
  `smp_hold_cpu1` and the counters.

## The checks

### L18: `sim/unit`

- **The arbiter against its table**, in Migen's simulator:
  - 20,000 random clocks for each `use_locke` and `gapless`, every arc taken;
  - alternating requests, granted in turn with a clock of no grant between;
  - the printed grants' handover with no clock between.
- **The arbiter against RD68041's `rd68041_fairarb.sv`**, under iverilog:
  - 200,000 random clocks for each option, no mismatch;
  - a negative run (different grants) that must mismatch.

  The SV model is run from `Inputs/` as it is. The Migen code is written from
  the doc's table.
- **The devices and the MI gate**, with two fake processors:
  - WHOAMI for each;
  - the IPIs, RELEASE, BOOT_PC;
  - TCI on reads;
  - no TA while the other's MI is asserted, unless the gate covers it;
  - the counters and their clearing.

### L19: the L1 bench

The bench (`sim/bench`, `SMP=1`) has two `rd68041_top`, the board, and our
bridge in front of the bench's RAM. It runs RD68041's own
`sim/programs/m14_smp.S`, built from `Inputs/`:
- **The program's parameters:** ITERS, UMASK, and WTSHARED = 1 (setup A's
  policy, as RD68041's `straight` run uses).
- **Its tests:**
  - interprocessor interrupts;
  - CAS and TAS spinlocks and lock-free CAS, in cache-inhibited, write-through
    and (here, write-through) shared pages;
  - false sharing, a producer and a consumer, a misaligned word read racing its
    writer, MOVE16;
  - an instruction cache kept coherent by snooping;
  - a TLB shootdown.
- **The verdict:** `tb.sv` checks memory as RD68041's `core_smp_tb` does, and
  the board's counters.

| Run | Result |
|---|---|
| ITERS=6, FPU=1 and FPU=0 | PASS, 122,654 sys clocks; 2,604 transfers by CPU 0, 1,053 by CPU 1; 330 locked sequences |
| ITERS=32 | PASS; 6,090 and 4,076 transfers, 1,913 locked sequences, 37 torn racing reads (allowed; RD68041's own `long` run: 37) |
| no MI gate | hangs, as it must |
| no snooping (UMASK $FFFFFCFF) | hangs, as it must |
| the printed arbiter | its handover with no clock between caught |

Every check in the passing runs is 0: contention, turnaround, owner, atomicity
and snoop interventions.

### L20: litex_sim and smptest

**`sim/litex/smptest`** is a bare-metal program, serial-booted by
`smp_test.py`. CPU 0 builds MMU tables for setup A:

| Pages | Mode |
|---|---|
| the program; shared data; a routine CPU 0 rewrites; a page remapped from one physical page to another | write-through, U 01 |
| locks and results | cache-inhibited, serialised |
| each CPU's private data, and CPU 1's stack | copyback, U 00 |

CPU 0 then starts CPU 1. Both run:
- IPIs both ways;
- CAS and TAS locks and lock-free CAS, in write-through and cache-inhibited
  memory;
- false sharing;
- a producer and a consumer;
- the rewritten routine under CPU 1;
- a copyback buffer handed over with CPUSHA;
- a TLB shootdown by IPI;
- timer0 routed to each CPU by its mask.

CPU 0 reports the board's counters, and the time each takes to read 64 KiB of
its own, alone and both at once.

`--negative` maps the routine's page with U = 00, so CPU 0's rewrite isn't
snooped. It must fail exactly one check, $400: CPU 1 runs the old routine.

**Results in litex_sim, `--sdram`:**

| | `standard+smp` | `standard+fpu+smp` |
|---|---|---|
| smptest (45 checks) | PASS | PASS |
| the bus counters | contention, turnaround, owner, atomicity, intervene: 0 | the same |
| the BIOS (L3), cputest040 on CPU 0 (L4) | | PASS, PASS (97 checks) |
| `--negative` | | fails at $400 alone, as it must |

The uniprocessor L3 and L4 still pass.

In litex_sim, reading 64 KiB takes about as long for each CPU with both
reading as for one alone (190,733 sys clocks alone; 179,357 and 188,594 both at
once): there, the line fills' latency, not the shared bus, sets the pace.

## The Wukong (L21)

| | LUTs | BRAM tiles | WNS at 25 MHz |
|---|---|---|---|
| two MC68040s (`standard+fpu+smp`), synthesised | 70,915 (112 %) | 58.5 | does not fit |
| two MC68LC040s (`standard+smp`), placed | 52,366 (82.6 %) | 58.5 | +0.208 ns |

The two MC68LC040s are `~/LITEX-68K/bitstreams/wukong-rd68041-nofpu-smp-eth-sd-25m-c5ba651`,
with Ethernet and the SD card as the uniprocessor bitstreams have.

**On the board**, each run from `board.py prompt` (cputest and smptest stop
the CPU when they end, so the bitstream is reloaded between runs), the SD card
(NetBSD's) untouched:

```sh
scripts/board.py prompt --bitstream ~/LITEX-68K/bitstreams/wukong-rd68041-nofpu-smp-eth-sd-25m-c5ba651 --speed 1000000
sim/litex/smp_test.py --variant standard+smp --serial /dev/ttyUSB0 --speed 1000000 \
    --build-dir build/qmtech_wukong_rd68041_smp_25m_1m_io [--iters N] [--negative]
```

| Run | Result |
|---|---|
| the BIOS on CPU 0, CPU 1 in reset | memtest OK; memspeed 11.9/10.7 MiB/s (write/read) |
| cputest040 on CPU 0 (`l4_test.py --serial`) | PASS, 81 checks |
| smptest, 16 iterations | PASS, 45 checks; 16,943 and 10,572 transfers, 233 locked sequences, the longest wait for a grant 81 clocks |
| smptest, 256 iterations | PASS, 285 checks; 3,114 locked sequences, the longest wait 78 clocks |
| smptest `--negative` | fails at $400 alone, as it must |

Contention, turnaround, owner, atomicity and intervene stay 0 in every run.

Reading 64 KiB takes CPU 0 589,128 sys clocks alone (about 11 MB/s at
100 MHz); with both reading at once, 560,600 for CPU 0 and 588,680 for CPU 1.
As in litex_sim, the shared bus does not slow either one: each line fill's
latency in the bridge and LiteDRAM leaves the bus idle most of the time, so
two processors read about twice as much as one.

## L23: what NetBSD asked for

NetBSD's SMP port (on the NetBSD side) asked for a clock interrupt per CPU,
and for litexboot to describe the board.

- **`timer1`** in the `+smp` SoCs (`core.py`, `add_soc_components`): LiteX's
  timer, without timer0's uptime counter, on interrupt line 4 (UART 0, timer0
  1, Ethernet 2, the SD card 3). NetBSD routes it to CPU 1 alone
  (`cpu_irq_mask1`), and timer0 and the devices to CPU 0.
- **The board's reset:** RELEASE, BOOT_PC, BOOT_SP and the IPIs are cleared
  by the SoC's reset request, so that after a reset CPU 1 does not start
  again by itself (above). `sim/unit/smp_devices.py` checks it.
- **litexboot,** built with `-D__rd68041_smp__`, adds two records. These tags
  are not in `patches/linux`, whose kernel runs on one processor:

  | Tag | | Word a | Word b |
  |---|---|---|---|
  | `0x8006` | `BI_LITEX_SMP` | `0xFFFF0000` | NCPUS (`$18`) |
  | `0x8007` | `BI_LITEX_TIMER1` | timer1's CSR base | its line |

  It also stops timer1 and clears `cpu_irq_mask1` before the kernel. On the
  uniprocessor SoCs, its binary is unchanged.
- **`sw/litexboot/bidump/`:** a test kernel that prints the bootinfo it was
  given:
  `scripts/board.py boot --kernel bidump.elf --no-dtb --until 'bidump: end'`.
- **smptest** runs both timers at once, as NetBSD routes them: each CPU must
  take only its own timer's interrupts (checks $720, $730).
- The bitstream is `wukong-rd68041-nofpu-smp-timer1-eth-sd-25m-c5ba651`:
  52,458 LUTs (82.7 %), WNS +0.230 ns at 25 MHz.

**Checked:** `sim/unit`; the L1 bench, both variants; smptest in litex_sim
(48 checks). On the Wukong, the SD card untouched:

| Run | Result |
|---|---|
| the BIOS, cputest040 on CPU 0 | memspeed 11.9/10.7 MiB/s; PASS, 81 checks |
| smptest, 16 and 256 iterations | PASS, 48 and 288 checks; timer0 12 ticks on CPU 0 and none on CPU 1, timer1 8 on CPU 1 and none on CPU 0; the bus checks all 0 |
| smptest `--negative` | fails at $400 alone |
| bidump, through litexboot | `8006 000c ffff0000 00000002`, `8007 000c f0005000 00000004`, and the other records as before |

## The software rules (setup A, from RD68041's doc/smp.md)

- **Shared pages:** every page both processors use is write-through with
  U = 01. Copyback only for a private page, with U = 00, and pushed (CPUSH)
  before it passes to the other processor.
- **Before the MMU or a TTR is set,** UPA is 00 and nothing is snooped.
- **Page tables** are write-through or cache-inhibited: table searches are
  never snooped.
- **ATCs are per processor:** a mapping changed needs the other processor to
  flush, by an IPI (smptest's shootdown).
- **Lock words** are in write-through or cache-inhibited memory, naturally
  aligned.
- **Misaligned data:** anything read concurrently without a lock must be
  aligned. A misaligned operand across two lines is never atomic against the
  other processor's writes; the bench counts the torn reads.
