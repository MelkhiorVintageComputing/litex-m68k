# L1: the bus bridge, on its own

Milestone L1 of `doc/plan.md`: RD68031 and `M68030ToWishbone`
(`litex_68k/m68030_bus.py`), with Wishbone RAM and a test device, no LiteX SoC.

```sh
make -C sim/bench check              # every program, both caches built in
make -C sim/bench check CACHES=0     # the core with no caches
make -C sim/bench run PROG=bustest PLUSARGS=+vcd=bustest.vcd
```

## The bridge

A slave on the MC68030 bus and a Wishbone master, written from UM section 7
so that it can later sit behind real pins with a real MC68030 (`doc/plan.md`).
It started from IIsiFPGA's PDS bridge
(`Inputs/IIsiFPGA/IIsi-to-ztex-gateware/mc68030_fsm.py`), which works against
a real MC68030; what it keeps from there:

- a posedge-only state machine in the processor's clock domain, which sees AS at
  the start of S2;
- a 32-bit port terminated by STERM, one clock wide (UM 7.3.4: negated within
  two), with the read data driven with it and held;
- write data taken one clock after AS, since the processor drives it from S2;
- reads of the whole long word (`sel = 0xF`), since the caches load whole long
  words (UM Table 7-4); writes with the lanes of UM Table 7-5.

What it adds, because on the Macintosh the logic board did it: CPU space (IACK
autovectored with AVEC; everything else, breakpoint acknowledges and coprocessor
cycles included, answered with BERR), BERR for a Wishbone error and for a
timeout, and CIIN for the I/O regions. What it differs in: no byte swapping (the
Macintosh bridge reversed the data to match a little-endian SoC; LiteX handles
big-endian CPUs natively, byte 0 on `dat[31:24]`/`sel[3]`), and no posted writes
(a write is acknowledged only once Wishbone has, so that its bus error reaches
the processor).

Every output towards the processor comes from a register. A cycle with
zero-wait-state Wishbone slaves takes 5 clocks (AS seen, Wishbone request, ack,
STERM, AS gone); a write one more. Not there yet (L6): STERM driven from the
Wishbone ack directly, burst fills (CBACK), posted writes, and keeping the
Wishbone cycle locked across a read-modify-write (RMC) when there are other
masters.

## The bench

`sim/bench/bench.py` builds the top (`bench.v`); `sim/bench/tb.sv` runs it with
Verilator: a 10 ns clock, 50 % duty (RD68031 uses both edges), the RAM image
from `+ram=`, the console, and the verdict. The map is in `bench.py`'s header.

| Program | What it checks | Reference |
|---|---|---|
| `bustest` | itself, 47 checks: every size at every offset of a 32-bit port with the data cache off, byte enables on a device register, TAS and CAS, bus errors (Wishbone error, timeout, breakpoint acknowledge), autovectored interrupts and masking, CIIN on device registers | returns 0, or the number of the first failed check |
| `arith` | `Inputs/RD68031/sim/programs/arith.c`, unmodified: integer code chosen by GCC | `main()` = `0x688cea90` |

`arith`'s reference comes from the same source built for 32-bit x86 (`gcc -m32
-ffreestanding -nostdlib`, a `_start` that prints `main()`'s value and exits by
system call: the host has no 32-bit libc). No m68k is in that loop, so it checks
the core and the bridge together against an independent compiler and processor.

Each failure mode `bustest` is meant to catch was checked by breaking the bridge
on purpose: byte enables in little-endian order (fails at check 1), no CIIN
(check 70), IACK answered with BERR instead of AVEC (the spurious interrupt,
vector 24, ends the run).
