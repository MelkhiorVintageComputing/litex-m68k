# L5: RD68031 on the QMTech Wukong

Milestone L5 of `doc/plan.md`, on the board: a Wukong V3 (xc7a100t, FGG676),
its JTAG on a Xilinx Platform Cable USB II, its UART on a CH340
(`/dev/ttyUSB0`).

## Stage 1: one 25 MHz clock, integrated RAM

```sh
targets/qmtech_wukong.py --build      # Vivado, about 10 minutes: run it alone
targets/qmtech_wukong.py --load       # JTAG, into the FPGA: not the flash
litex_term /dev/ttyUSB0
```

`targets/qmtech_wukong.py` is litex-boards' `qmtech_wukong` target with other
defaults: `--cpu-type rd68031 --revision 3 --sys-clk-freq 25e6
--integrated-main-ram-size 0x10000 --timer-uptime --output-dir build/qmtech_wukong`.
Everything else (DDR3, Ethernet, video) is litex-boards' and not used yet.
`--load` is ours, through Vivado's hardware manager: litex-boards' own runs
OpenOCD with an FT232 configuration, which is not this cable. A loaded
bitstream lasts until power-off or PROG: the board then loads its flash, which
nothing here writes.

- **Timing:** met at 25 MHz, WNS 1.2 ns (Artix-7 -1: JTAG does not tell the
  speed grade, -1 is the safe assumption and litex-boards' default).
  RD68031 alone makes 29.6 MHz on an Artix-7 (its `doc/critical-path.md`);
  its limit is half a period, between its falling-edge and rising-edge
  registers, so the clock's duty cycle must be 50 %, which an MMCM output is.
- **Size:** 14,072 LUTs (22 %), 5,678 registers, 32 block RAM tiles, the
  CPU most of it.
- **The CPU's Verilator waivers** (`rd68031.vlt`) go to simulation platforms
  only (`core.py`); Vivado would take them for a source.

### On the board

The BIOS boots: its CRC check, the memory test, and main RAM at 8.8/7.8 MiB/s
(write/read; 446/325 KiB/s at the simulation's 1 MHz scale to 11/8 MiB/s at
25). The tests run against the board with `--serial`:

```sh
sim/litex/l4_test.py   --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong
sim/litex/bios_test.py --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong
```

`l4_test.py` builds the demo and cputest against the board's software, in
`build/qmtech_wukong/l4/`, and loads them with `litex_term /dev/ttyUSB0`: full
251-byte frames, 10.9 KB/s. The demo runs, its `reboot` resets the SoC, and
cputest passes its 74 checks, as in simulation: 25 runs in a row, each from a
freshly loaded bitstream. `bios_test.py` passes too (`uptime` included).

The first run on the board stopped after the BIOS had printed half of
"Executing booted program", and the board answered nothing after that. It did
not come back in the 25 runs since, nor with the paced simulation below. The
BIOS of that build had crt0's old exception handler, which lost its own report
(below): an exception in the BIOS then looked exactly like that. If it happens
again, the handler now says what it was.

## What simulation did not show: the UART's transmit side

`litex_sim`'s UART takes a byte every clock, so its transmit FIFO never fills.
On the board, at 115200 baud, it fills all the time, and LiteX's UART driver
(`libbase/uart.c`) then keeps what does not fit in a 128-byte ring, emptied
by the TX interrupt. With interrupts masked, `uart_write` empties the ring only
when the ring is full: what is left in it when a program stops for good never
goes out. crt0's exception handler masks interrupts, prints, and stops, so its
report, and the end of whatever was printed before it, was lost: the board
seemed to hang mid-line. It now calls `uart_sync` before stopping.

`targets/sim.py --uart-tx-clocks N` paces the simulated UART's transmit side,
one byte every N clocks (115200 baud at 25 MHz is about 2170), and the tests
take `--uart-tx-clocks` to pass it on. With it the simulation shows the same
truncated output the board did.

## For upstream

- `libbase/uart.c`: with interrupts masked, `uart_write` parks what the FIFO
  cannot take in the ring and drains the ring only when the ring is full, so
  an exception handler's last words are lost unless it calls `uart_sync`.
  Writing straight to the FIFO, waiting on `txfull`, when interrupts are
  masked and the ring is empty would avoid it.
- `litex_sim`'s UART (`serial2tcp`, `RS232PHYModel`) has no baud rate on
  either side: the transmit FIFO never fills (above), and received bytes
  arrive faster than the BIOS's serial boot takes them (`doc/l4-bare-metal.md`).
