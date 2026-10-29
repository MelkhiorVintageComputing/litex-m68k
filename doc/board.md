# The board, for software work

How to put the current LiteX-68K SoC on the QMTech Wukong V3 and boot your own
kernel on it, with no FPGA tools to learn: `scripts/board.py` does the Vivado
part. For what a kernel must do once it runs, see `doc/porting-bsd.md`.

## Setup, once

```sh
source ~/LITEX-68K/litex-venv/bin/activate     # every session: board.py and litex_term need it
```

- **The board:**
  - powered;
  - its JTAG on the Xilinx Platform Cable;
  - its UART on `/dev/ttyUSB0` (USB serial, 1,000,000 baud, 8N1);
  - Ethernet cabled to the LAN (DHCP works), and a micro-SD card in its slot
    if wanted.
- **The bitstream lives in the FPGA's RAM:** a power cycle or the PROG key
  erases it, and then it has to be loaded again (`board.py load`). Nothing
  here writes the board's flash.
- **Vivado (2025.2, `/opt/Xilinx/2025.2`)** is only used through `board.py`.
  Loading takes about 20 s.
- **One program at a time on the serial port:** `board.py`, `litex_term`,
  `screen`... Close the others first.

## The current bitstream

`~/LITEX-68K/bitstreams/wukong-fpu-eth-sd-7c668f4/`, a snapshot that later
builds do not overwrite. Its `SNAPSHOT` file names the commits it was built
from and its timing. It has RD68884 7c668f4. The older snapshots are kept,
but their FPUs have bugs (`doc/porting-bsd.md`):
`~/LITEX-68K/bitstreams/wukong-fpu-eth-sd-7ec30da/` can round an instruction
saved busy as the work run in between did, and
`~/LITEX-68K/bitstreams/wukong-fpu-eth-sd/` (RD68884 7a188db) can also take a
conditional branch the wrong way after a context switch.
`~/LITEX-68K/bitstreams/wukong-nofpu-eth-sd-7c668f4/` is the same SoC without
the FPU (`--bitstream` selects it), for software built for soft float.

**RD68041**, an MC68040, at 25 MHz (`doc/l11-rd68041.md`), with the same
devices, map, interrupts and BIOS:
- `~/LITEX-68K/bitstreams/wukong-rd68041-fpu-eth-sd-25m-c5ba651/`: the
  MC68040, FPU included.
- `~/LITEX-68K/bitstreams/wukong-rd68041-nofpu-eth-sd-25m-c5ba651/`: the
  MC68LC040, no FPU.

`-c5ba651` is the RD68041 commit they are built from. It is M15, faster
reads and writes, and it has both of the fixes to the instruction fetch that
Linux and NetBSD needed (`doc/l11-rd68041.md`). The older snapshots:
- `-4300689` has both fixes, without M15.
- `-p1` has the first fix only. NetBSD's install ramdisk froze it.
- `wukong-rd68041-fpu-eth-sd-25m/` has neither: it runs the BIOS and bare
  metal, but Linux with glibc dies at init.

On the `smp` branch, `wukong-rd68041-nofpu-smp-eth-sd-25m-c5ba651` has two
MC68LC040s on one bus, for the BIOS and bare metal (`doc/l18-smp.md`): the
BIOS runs on CPU 0, and a program starts CPU 1 (`smp.h`). Two MC68040s do not
fit. On the board, cputest040 and smptest (`sim/litex/smp_test.py --serial`)
pass.

A kernel for them must know the 68040: its caches are copyback-capable and
pushed with CPUSH, I/O wants a TTR or noncachable pages, and the FPU leaves
some instructions to the M68040FPSP (`doc/porting-bsd.md`).

| | |
|---|---|
| CPU | RD68031 (MC68030, MMU, caches) at 30 MHz, with RD68884 (MC68881 FPU) |
| SoC | LiteX, sys clock 100 MHz; 256 MiB DDR3 at `0x4000_0000`; L2 cache off |
| Devices | LiteUART (1 Mbaud), Timer0 with 64-bit uptime, LiteEth (GMII, 1 Gb/s only), LiteSDCard, LEDs |
| BIOS | LiteX's, with `patches/litex` (SD card boot works on this big-endian CPU) |
| Interrupt lines | UART 0, Timer0 1, Ethernet 2, SD card 3; also in the bootinfo and the device tree |

What is in the directory:

- `gateware/qmtech_wukong.bit`: the bitstream (`gateware/qmtech_wukong_timing.rpt`, its timing);
- `csr.csv`, `csr.json`: every register's address, the interrupt lines, the memory map;
- `software/include/generated/` (`csr.h`, `mem.h`, `soc.h`, ...) and
  `software/lib*/`: the headers and libraries to build bare-metal code or a
  loader against. litexboot is built against them.

Making another one, from a `targets/qmtech_wukong.py` build:
`scripts/snapshot_bitstream.sh NAME build/<output dir>`. `--bitstream DIR`
then selects it. An FPU-less SoC, for instance, is
`targets/qmtech_wukong.py --cpu-variant standard --uart-baudrate 1000000 --with-ethernet --with-sdcard --build`.
A Vivado build takes about 12 minutes; see `doc/l10-devicetree.md` for the
options that meet timing.

## Commands

### Interactive

```sh
scripts/board.py boot --kernel netbsd.elf          # load, upload litexboot + kernel, boot, terminal
scripts/board.py boot --kernel k.elf --initrd rd.img --cmdline "console=liteuart0 -s"
scripts/board.py console                           # just a terminal (the board as it is)
scripts/board.py prompt                            # load, stop the BIOS at its "litex>" prompt
scripts/board.py sdboot                            # load, let the BIOS boot from the SD card
scripts/board.py load                              # load only; the BIOS then boots as it likes
```

Once a command hands over to the terminal, Ctrl-C quits.

**`boot`** does all of this:
1. builds litexboot against the snapshot;
2. lays out the images (`sw/litexboot/images.py`) into `~/LITEX-68K/board-work/`;
3. starts `litex_term`, then loads the bitstream;
4. the BIOS's serial boot finds litex_term and uploads everything, at about
   25 KB/s (a 4 MB kernel takes about 3.5 minutes);
5. litexboot loads the kernel and jumps to it.

Expect a few `serial frame error` lines from litex_term at the start: it
falls back to a slower upload mode by itself.

**What litexboot gives the kernel** is in `doc/porting-bsd.md`, section 3:
- the ELF's `PT_LOAD` segments at main RAM + `p_paddr`;
- the entry at main RAM + `e_entry`, supervisor mode, MMU and caches off,
  IPL 7;
- Linux-style bootinfo records after the kernel's end: memory, command line,
  ramdisk, FPU, and where the UART, timer and interrupt controller are;
- a device tree, unless `--no-dtb` is given.

A kernel that wants something else needs litexboot adapted
(`sw/litexboot/`, BSD-licensed).

### Unattended (scripts, CI, an AI agent)

`boot` and `sdboot` take `--until REGEX`:
- they print the console to stdout, and to `--log FILE`;
- they wait for REGEX, then type each `--send` line, waiting for `--prompt`
  after each one;
- the exit status is 0 on success, 1 on a time-out (`--timeout` seconds for
  each step).

```sh
scripts/board.py boot --kernel ~/LITEX-68K/build/buildroot-fpu/images/vmlinux \
    --initrd ~/LITEX-68K/build/buildroot-fpu/images/rootfs.cpio.gz \
    --until 'login: ' --send root --send 'uname -a' --log boot.log
scripts/board.py boot --kernel netbsd.elf --until 'NetBSD' --timeout 900
```

For longer scripted sessions, `sim/litex/l7_test.py` is a fuller example of
driving the console: logging in, running commands, checking answers.

## The SD card

- **Its current contents:** NetBSD for RD68041, set up by hand (two
  partitions, p1 and p2), to check RD68041's fixes. Keep it. The Linux
  installation that `l7_test.py --sd-install` made earlier
  (`doc/l11-rd68041.md`) is gone.
- **Tests that keep the card:** `l7_test.py --io --no-sd` (Ethernet only).
  Plain `--io` and `--sd-install` overwrite it.
- **While p1 holds a `boot.json`, every load boots from the card**,
  unless litex_term answers first (`boot` does). To get the BIOS prompt, use
  `board.py prompt`, which presses Q at "Press Q or ESC".
- **Booting your own kernel from the card:** put it on p1 with its
  `params.bin`, `litexboot.bin` and `boot.json`. The files `boot` leaves in
  `~/LITEX-68K/board-work/images/` will do: rename `images.json` to
  `boot.json`.
  - The BIOS reads FAT, loads each file at the address `boot.json` gives,
    and jumps to the last one: about 11 s for a 4 MB kernel.
  - The card can be written from a PC, or from a running system on the board.

## At the BIOS prompt

`board.py prompt`, then `board.py console`, gives `litex>`. Useful commands:

| Command | |
|---|---|
| `help` | all of them |
| `mem_read 0xf0000000 32` | dump registers or memory (`mem_write ADDR VALUE`) |
| `serialboot` | wait for `litex_term --images ...` |
| `sdcardboot` | boot from the SD card's `boot.json` |
| `sdcard_init`, `sdcard_read` | the card by hand |
| `reboot` | reset the SoC (the BIOS starts again) |

## When something goes wrong

- **`board.py` cannot open the port:** another program has it (`fuser
  /dev/ttyUSB0`).
- **The load fails:** look in `~/LITEX-68K/board-work/vivado-load.log`.
  - Is the JTAG cable connected and the board powered?
  - "No hardware target" usually means the cable or the power.
  - Vivado's `hw_server` sometimes stays running after an error: `pkill hw_server`.
- **No BIOS output after a load:** check the baud rate (1,000,000) and the
  port. Press the PROG key, or power-cycle, then load again.
- **The kernel hangs silently:**
  - the early console is the UART's `rxtx` register, polled, at the
    address the bootinfo gives (`BI_LITEX_UART`, `0xF000_5000` on this
    bitstream);
  - `doc/porting-bsd.md`, section 8, has a bring-up order.
- **The board needs a clean start:** `board.py load` resets everything but
  the SD card.
