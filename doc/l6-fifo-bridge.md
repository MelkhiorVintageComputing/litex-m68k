# L6, step 1: the FIFO bridge

The CPU's clock decoupled from the SoC's: RD68031's bus reaches the SoC through
two asynchronous FIFOs, so the CPU's clock may be any clock, including, later, a
real MC68030's own oscillator on a pin (as IIsiFPGA has it). Cache line fills
are bursts, and main RAM is read a line at a time on a LiteDRAM native port as
wide as a line. `litex_68k/m68030_fifo.py`; the default, `RD68031.bus_bridge =
"fifo"` (`"sync"` is L5's M68030ToWishbone and WishboneSyncCrossing).

```
 cpu clock domain                 |               sys
 MC68030 bus -- M68030ToFIFO --> request FIFO --> FIFOToSoC --+-- Wishbone (ROM, SRAM, CSRs, ...)
                             <-- answer FIFO  <--             +-- LiteDRAM native port, 128 bits (main RAM)
```

## The two FIFOs

| | Request, CPU to SoC | Answer, SoC to CPU |
|---|---|---|
| Fields | read/write, word address, write data and byte enables, *line* (a read for a whole line), *answer* (a write that wants one), tag (3 bits) | a line (128 bits: word k, at byte offset 4k, in bits 32k+31..32k), error, tag |
| Depth | 8 | 4 |

- **Reads** always wait for their answer. A read for a line gets four words,
  and a read for one word gets that word in all four slots, so the CPU's side
  always picks its word by A3-A2, and the answer needs no byte enables.
- **Writes outside `io_regions`** (memory) are posted: the CPU's cycle ends
  (STERM) as soon as the request is in the FIFO. A write the SoC cannot do
  (nothing there) is then lost, since the CPU has moved on: a bus error on it
  could only be reported against some later instruction.
- **Writes in `io_regions`** (CSRs, 0x8000_0000 and up) wait for their answer:
  the register is written before the next instruction runs, and a write to
  nothing there is a bus error on that write (UM 7.5.1), which cputest checks.
- **Order:** the SoC's side carries the requests out in order, one at a time,
  so a read sees every write before it, wherever each goes.
- **The tag** tells the CPU's side that an answer is for the request it is
  waiting for. One is outstanding at a time (the 68030 never has two bus
  cycles going); an answer with another tag is dropped, which would be one
  left over from a cycle the CPU's side gave up.

## The CPU's side: STERM and bursts

Every cycle ends with STERM (a 32-bit port, UM 7.3.4), all signals towards the
processor from rising-edge registers, as in L1's bridge. A cachable read with
CBREQ asserted (UM 7.3.7) gets a line, and goes out as a burst, one long word
per clock. The processor samples STERM on a rising edge and latches the data on
the falling edge after, so in a burst each next long word goes on the bus on
the edge where the STERM of the one before it is sampled:

| Edge | E0 | E1 | E2 | E3 | E4 |
|---|---|---|---|---|---|
| STERM | on | on | on | on | off |
| CBACK | on | on | on | off | off |
| D | word 0 | word 0 | word 1 | word 2 | word 3 |
| Sampled | | STERM 0 | STERM 1 | STERM 2 | STERM 3 |

The words follow the one asked for, wrapping in the line (A3-A2 "incremented by
the system hardware"). CBACK is off with the fourth STERM, so the processor
expects nothing more. On the bench, with the processor's real CBREQ, a burst
looks like UM figure 7-38 without its wait states.

## The SoC's side

`FIFOToSoC` takes each request from its FIFO at once, into registers. It then
carries the request out, and answers if an answer is wanted:

- **Wishbone:** a line is four accesses, a word one. Only a burst read asks for
  a line: without CBREQ (caches off, CIOUT, a read-modify-write), the three
  other words would be read for nothing. An access nobody answers ends
  through `WishboneErrorTimeout` (L5), with an error.
- **LiteDRAM native port**, for main RAM when the controller is 128 bits wide
  (`RD68031.add_memory_buses`, which LiteX's `add_sdram` calls). On the
  Wukong, DDR3 16 bits at 1:4 gives exactly a line. Every read reads the line.
  A write writes its long word, with byte enables `sel << 4*word`. Word k is
  in bits 32k+31..32k, as LiteDRAM's own Wishbone bridge puts it, so the SoC
  bus's view of main RAM (still connected, for other masters) is the same.
  Narrower controllers (litex_sim's default SDR) go through Wishbone instead.

Taking the request at once matters when there are no FIFOs: with the CPU in
sys, `stream.ClockDomainCrossing` is a plain connection. The first version only
took the request once its answer had been taken, while the CPU's side only
waits for the answer once its request has been taken: a deadlock.

## Resets

- **The bridge**, both halves and both FIFOs, is held in reset while either
  clock domain is (step 2, below: as first written, each FIFO side had its own
  domain's reset, which only works when both come out of reset together).
- **A reset request one sys clock long** (ctrl's `soc_rst`, the BIOS's
  `reboot`) is shorter than a slower CPU clock. In L5's synchronous design the
  board's stretched MMCM reset hid this. In litex_sim, where the CRG has only
  a power-on reset, the CPU missed it. Its start now crosses as a toggle, and
  holds the CPU in reset for 8 of its own clocks. A longer request (`cpu_rst`)
  crosses as a level too (`core.py`).

## Results

### L1 bench (`make -C sim/bench BRIDGE=fifo check`)

Here the processor is on a clock of its own (`+cpu_half=<ns>`; sys's half period is 5).
The bench's I/O region is now LiteX's (0x8000_0000 and up), so that check 52, a
write to nothing, is a non-posted write there as on the SoC.

| CPU clock (half period) | bustest | arith | arith, CPU clocks |
|---|---|---|---|
| 2.3 ns (217 MHz, faster than sys) | pass | pass | |
| 5 ns (sys's frequency) | pass | pass | 95,112 |
| 12.5 ns (40 MHz, 2.5:1) | pass | pass | |
| 17.3 ns (28.9 MHz, unrelated) | pass | pass | about 80,800 |
| 20.03 ns | pass | pass | |
| 17.3 ns, no caches | pass | pass | |

M68030ToWishbone, everything at one clock and the RAM answering in one, takes
79,524 for arith. The FIFOs' round trip costs about six CPU clocks per miss,
and the bursts win most of it back.

### litex_sim

| | CPU in sys | CPU at 0.3125 MHz, sys at 1 MHz | ... and DDR3 (MT41K128M16, native port) |
|---|---|---|---|
| L2 | pass | | |
| L3 | pass | pass | pass |
| L4 standard | pass | pass | pass |
| L4 minimal | pass | | pass |

New test options (`sim/litex/simtest.py`): `--cpu-clk-freq F` (targets/sim.py:
a second clocker and a "cpu" clock domain; litex_sim needs half its period to be
a whole number of picoseconds, hence 0.3125e6 rather than 0.29e6), and
`--sdram`. Each gives its build directory a name of its own.

### Wukong V3

The board runs DDR3, the SoC at 100 MHz and the CPU at 25 MHz, as in L5
(`targets/qmtech_wukong.py --build --load`; `--bus-bridge sync` builds L5's).

| | L5 (sync) | L6 step 1 (FIFO) |
|---|---|---|
| Timing, worst slack | 0.378 ns | 0.304 ns |
| LUTs | 16,346 | 16,993 (27 %) |
| L3 | pass | pass |
| L4, fresh loads | 10/10 | 5/5 |
| BIOS memspeed, write | 9.2 MiB/s | 11.8 MiB/s |
| BIOS memspeed, read | 7.8 MiB/s | 9.7 MiB/s |
| serialboot | 10.9 KB/s, no CRC error | 10.9 KB/s (the UART's limit), no CRC error |

The CPU's clock is still 25 MHz. It no longer has to divide 100 MHz, so a
faster one, up to RD68031's 29.6 MHz on an Artix-7, is only a matter of
`--cpu-clk-freq` and timing.

## For upstream

- **`SimAsyncResetSynchronizer`** (`litex/build/sim/common.py`): each instance
  makes a clock domain named `resync`, and migen merges same-named domains.
  With two or more in a design, every one of them is clocked by one of their
  clocks. `stream.ClockDomainCrossing(with_common_rst=True)` makes two per
  crossing, so under litex_sim a FIFO's write side was held in reset by
  another clock (found on the L1 bench, which uses the same lowering). The
  fix would be a name of its own per instance.
