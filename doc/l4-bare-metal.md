# L4: bare-metal programs on RD68031

Milestone L4 of `doc/plan.md`: programs loaded by the LiteX BIOS over the UART,
the way a board is used, under `litex_sim`.

```sh
make -C sim/litex l4                       # caches built in
make -C sim/litex l4 VARIANT=minimal       # no caches
```

## Loading programs: litex_term on the simulation's UART

`litex_term` opens its port with pyserial's `serial_for_url`, so the
simulation's UART, on a TCP port with `--uart-tcp`, is just
`socket://127.0.0.1:<port>`. Interactively:

```sh
targets/sim.py --integrated-main-ram-size=0x10000 --uart-tcp --uart-tcp-port 2431 &
litex_term socket://127.0.0.1:2431 --kernel demo.bin --kernel-adr 0x40000000
```

`serial2tcp` serves one client at a time. LiteX needed no change.

**Use `--safe` (64-byte frames) in simulation.** The simulated UART has no baud
rate: `serial2tcp` hands the UART a byte per clock whenever its FIFO has room.
The BIOS's UART interrupt handler (`libbase/uart.c`) moves bytes into a
128-byte ring and silently drops what does not fit, and its serial boot loop
empties that ring more slowly than the handler fills it. A frame of more than
about 100 bytes sent at once loses bytes, and the BIOS answers `E` after its
inter-byte time-out. Measured with hand-made frames: up to 100 bytes, `K`;
from 120, `E`; 251 bytes a byte at a time, `K`. `litex_term` without `--safe`
gets there too, after three failed attempts (251 bytes with windows 8, 4, 1),
by falling back to 64-byte frames. On a board, the baud rate spaces the bytes
out (115200 baud is about 2000 clocks a byte at 25 MHz). Upload speed in
simulation: 0.9 KiB/s.


## The programs

- **LiteX's demo** (`litex_bare_metal_demo`, `litex/soc/software/demo`) builds
  for the 68030 unchanged, 6 KiB, with our `crt0.S` (the vector table first, a
  `BRA.W` to `_start` as its first word: `doc/l3-bios.md`).
- **cputest** (`sim/litex/cputest/`), the MC68030 as a program sees it:
  - **exceptions**: every exception a program can cause on purpose, each
    checked for frame format, vector, stacked PC and, for format $2 frames, the
    instruction address (UM 8.1, Table 8-6):

    | Exception | Format/vector | PC |
    |---|---|---|
    | illegal | `0010` | the instruction |
    | zero divide | `2014` | next instruction (instruction address: the DIVU) |
    | CHK | `2018` | next |
    | TRAPV | `201c` | next |
    | TRAP #5 | `0094` | next |
    | line A | `0028` | the instruction |
    | line F (coprocessor 3, absent) | `002c` | the instruction |
    | privilege violation, from user mode | `0020` | the instruction; stacked SR has S clear |
    | trace | `2024` | after the traced NOP (instruction address: the NOP) |
    | address error, jump to an odd address | `a00c`/`b00c` | |
    | bus error, read and write of an unanswered address | `a008`/`b008` | SSW: DF, RW, FC 5; fault address |

    One handler takes them all: `trap_run` (`traps.S`) calls the test with
    every vector on `trap_catch`, which copies the frame's start and returns to
    `trap_run`'s caller without RTE, so any frame format will do.
  - **instruction cache**: code written as data runs stale until CACR's CI
    (UM 6.1.1); with no instruction cache (`minimal`), it runs as written. The
    generated code freezes the cache (CACR FI) between its two runs, so that
    nothing else evicts its line (L6, `doc/l6-fpu.md`).
  - **MMU** (UM 9): TC `80c08480` (4 KiB pages, TIA 8, TIB 4, TIC 8). Level A
    maps every 16 MiB block onto itself with early termination page
    descriptors (CI from `0x80000000` up), except `0x50xxxxxx`, which goes
    through levels B and C to four pages: two aliases of one RAM page, a
    write-protected alias of it, and an invalid page. Checked: everything
    still runs with translation on; the aliases read the page; writes through
    the write-protected alias, and anything on the invalid page, are bus errors
    from the MMU with the logical fault address; the U and M bits the
    processor writes into the page descriptors; PTEST's MMUSR (levels, W, I,
    M).
  - **data cache**: its tags are logical, and it is direct-mapped by A7-A4
    (UM 6.1.2), so two aliases at the same page offset share a line. With
    write allocation (CACR WA, which crt0 sets) a write through one alias takes
    the line over and the other alias reads memory: coherent, on both variants,
    as UM 6.1.2.1 says WA is for. Without WA, the write misses and leaves the
    line alone: the other alias reads stale data until CD with the data cache,
    and fresh data without it. (The first version of this test expected stale
    data with WA set; RD68031 was right, the test was not.)
  - **FPU** (added in L6, `doc/l6-fpu.md`): FNOP takes the line F exception
    with no FPU ("fpu: absent"). C doubles, bit for bit against values computed
    on the host, on every variant: compiler-rt's soft float without an FPU,
    the FPU's instructions with one. With RD68884 ("fpu: present"), also FSQRT,
    FMOVECR's pi, FSIN of pi/6, an infinity and FPSR's accrued DZ from a divide
    by zero, the MC68881's idle FSAVE frame ($1F18), and the divide by zero
    again with its exception enabled: taken on the next FPU instruction, a
    format $0 frame with vector 50 and that instruction's PC (UM 10.4.18).
  - Last, an illegal instruction with VBR back on crt0's table: crt0 reports it
    with its PC, and stops.

## The test

`sim/litex/l4_test.py`:

1. Builds the SoC's software (`--no-compile-gateware`), then the demo (LiteX's
   `demo.py`) and cputest against it.
2. Runs the simulation and `litex_term` on its UART, inside a pseudo-terminal
   (it sets its standard input to raw mode). `litex_term` loads `boot.bin`,
   the demo at first. It reads the file only when the BIOS asks, so the test
   changes what the next boot loads by copying another image there.
3. The demo: banner, `help`, `helloc`. Then `boot.bin` becomes cputest, and
   the demo's `reboot` resets the SoC: the BIOS starts again and loads it.
4. cputest's result, its cache lines and its FPU line ("fpu: present" on the
   `+fpu` variants only) against the variant, and crt0's report
   of the last illegal instruction at the address cputest printed.

`bios_test.py` (L3) and `l4_test.py` share `simtest.py`: the simulation's
command line, starting and stopping it, and the console.
