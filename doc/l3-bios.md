# L3: the LiteX BIOS on RD68031

Milestone L3 of `doc/plan.md`: LiteX's own BIOS, with picolibc and compiler_rt,
built for the 68030 and run under `litex_sim`.

```sh
make -C sim/litex bios                     # caches built in
make -C sim/litex bios VARIANT=minimal     # no caches
targets/sim.py --integrated-main-ram-size=0x10000    # the BIOS, interactively
```

Nothing in LiteX needed changing: the BIOS, picolibc (`machine/m68k`, big-endian)
and compiler_rt build with the Debian `m68k-linux-gnu` toolchain and
`-mcpu=68030` as they are. The BIOS is 20 KiB.

## The startup code

`litex_68k/cpu/rd68031/crt0.S`, used by the BIOS and by every program built
against LiteX's software (the demo, programs loaded by serialboot):

- **The vector table comes first.** LiteX's linker scripts put crt0's `.text` at
  the start of the image; at 0, in the ROM, that is where the processor reads
  its reset SSP and PC (UM 8.1.1).
- **Entering a RAM image at its first byte works.** The BIOS jumps to a booted
  program's load address. The table's first long word, which only a reset reads
  (as the initial SSP), is a `BRA.W _start`. At reset it gives a meaningless SSP
  that nothing uses: `_start` loads the stack pointer first.
- `_start` points **VBR** at the image's own table, so a booted program takes
  its exceptions in its own handlers, not the BIOS's. It then clears and enables
  both caches, copies `.data`, clears `.bss`, and calls `litex_startup_init` and
  `main`.
- **Vector 26**, the level 2 autovector, saves the scratch registers and calls
  LiteX's `isr()`. **Any other vector** raises the interrupt mask to 7 (so the
  UART driver polls) and prints the SR, PC and format/vector word, then stops:

  ```
  *** RD68031 exception: format/vector b008, PC 00000974, SR ....
  ```

`boot-helper.S` jumps to the image with `r1`-`r3` in `d1`-`d3`.

## The test

`sim/litex/bios_test.py` builds the SoC with the BIOS (and `--timer-uptime`),
runs it with the UART on a TCP port, waits for the prompt and types commands.
Each answer is checked against something worked out without the CPU:

| Command | Checks |
|---|---|
| boot | the BIOS's own CRC check of its image, the memory test and speed test of main RAM, interrupt-driven UART output |
| `mem_read` of the identifier memory | one character per 32-bit CSR word, in its low byte: `00 00 00 4c ...` ("L") on a big-endian bus |
| `mem_write`/`mem_read` | a long word reads back MSB first |
| `mem_test` | the BIOS's memory test |
| `crc 0x0 0x400` | the BIOS's CRC32 of its ROM against Python's `zlib.crc32` of `bios.bin` |
| `uptime`, twice | the timer moves |
| `flush_cpu_dcache` | CACR's CD, and memory still reads right |
| `mem_read 0x90000000` | nothing there: the bridge times out, BERR, a long bus fault frame (format `$B`, vector 2) reported by crt0 |

Both variants pass. Main RAM throughput at the simulated 1 MHz, from the BIOS's
`mem_speed`: 446/325 KiB/s (write/read) with the caches, 186/186 without.

## For upstream

`ident` prints `Ident: -` on this CPU, and on any CPU whose BIOS mapping
(`CPU.bios_map`) is the identity: `cmd_bios.c` reads the identifier only under
`#ifdef CSR_IDENTIFIER_MEM_BASE_VA`, and `export.py` defines the `_VA` names only
when the mapped address differs from the physical one. Not something this
project patches; the test reads the identifier memory directly instead.
