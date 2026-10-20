# L2: RD68031 as a LiteX CPU

Milestone L2 of `doc/plan.md`: RD68031 in a LiteX SoC under `litex_sim`, before
the BIOS.

```sh
make -C sim/litex check                    # caches built in (variant standard)
make -C sim/litex check VARIANT=minimal    # no caches
targets/sim.py <litex_sim options>         # litex_sim with --cpu-type=rd68031
```

Host prerequisites for any LiteX simulation: `libevent-dev` and `libjson-c-dev`
(LiteX's simulator modules are all built, whichever are used).

## The CPU class

`litex_68k/cpu/rd68031/core.py`. LiteX is not modified: `litex_68k.cpu.register()`
adds the class to `litex.soc.cores.cpu.CPUS` before LiteX parses its arguments,
which is all `targets/sim.py` does before handing over to `litex_sim`. The software
files LiteX expects from a CPU (`system.h`, `irq.h`, and from L3 `crt0.S` and
`boot-helper.S`) sit next to `core.py`, which is where LiteX looks for them.

| | |
|---|---|
| family, triple | `m68k`; `m68k-elf`, else the Debian `m68k-linux-gnu` (LiteX links with `-nostdlib`) |
| flags | `-mcpu=68030 -D__rd68031__` |
| endianness | big; the bridge puts byte 0 on `dat[31:24]` and nothing is swapped |
| variants | `standard`: both caches, 64 entries; `minimal`: no caches |
| map | ROM at 0 (the reset vectors are read there, UM 8.1.1), SRAM `0x0100_0000`, main RAM `0x4000_0000`, CSRs `0xF000_0000` |
| I/O region | `0x8000_0000`-`0xFFFF_FFFF`: the bridge asserts CIIN there |
| bus | one Wishbone master, the bridge (`litex_68k/m68030_bus.py`) |

**Interrupts.** LiteX's interrupt lines are level and one per source; the MC68030
has a priority level. Every line raises IPL 2, autovectored (vector 26), and two
CSRs of the CPU stand for the registers other CPUs have inside:
`cpu_irq_pending` (the lines) and `cpu_irq_mask` (which may raise IPL 2). In
`irq.h`, `irq_getmask`/`irq_setmask`/`irq_pending` are those CSRs, and
`irq_getie`/`irq_setie` set the status register's interrupt mask to 7 or 0.

**Caches.** `system.h`'s `flush_cpu_icache`/`flush_cpu_dcache` set CACR's CI/CD.
Those bits always read as zero (UM 6.3.1.8 and after), so OR-ing one into the
current CACR clears the cache and changes nothing else. The data cache is
write-through (UM 6.1.2): clearing it never loses a write.

## The smoke test

`sim/litex/hello.c`, run from the ROM (`--rom-init`) with a minimal runtime
(`crt0_rom.S`, `rom.ld`), built against LiteX's generated headers and the CPU's
`irq.h`: the UART, a CTRL CSR, the interrupt CSRs, the interrupt enable, and
three timer0 interrupts taken through the mask CSR, IPL 2 and the autovector. It
ends the simulation through `sim_finish` (`--sim-debug`). Both variants print
`L2 PASS`.
