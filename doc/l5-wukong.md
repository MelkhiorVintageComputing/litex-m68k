# L5: RD68031 on the QMTech Wukong

*Since L6, the default bus bridge is the FIFO one (`doc/l6-fifo-bridge.md`), and the
builds described here are `--bus-bridge sync`, in `build/qmtech_wukong_sync` and
`build/qmtech_wukong_bram_sync`; the same commands without it build L6's.*

Milestone L5 of `doc/plan.md`, on the board: a Wukong V3 (xc7a100t, FGG676),
its JTAG on a Xilinx Platform Cable USB II, its UART on a CH340
(`/dev/ttyUSB0`).

```sh
targets/qmtech_wukong.py --build      # Vivado, 10-15 minutes: run it alone
targets/qmtech_wukong.py --load       # JTAG, into the FPGA: not the flash
litex_term /dev/ttyUSB0
```

`targets/qmtech_wukong.py` is litex-boards' `qmtech_wukong` target with other
defaults: `--cpu-type rd68031 --revision 3 --timer-uptime`, and two
configurations:

- **DDR3** (the default, stage 2): 256 MiB of DDR3 main RAM through LiteDRAM,
  the SoC at 100 MHz (`--sys-clk-freq`), the CPU at 25 MHz
  (`--cpu-clk-freq`) in its own clock domain. Output `build/qmtech_wukong`.
- **Block RAM** (`--integrated-main-ram-size 0x10000`, stage 1): one 25 MHz
  clock for everything. Output `build/qmtech_wukong_bram`.

Ethernet, video and the SD card are litex-boards' and not used yet.
`--load` is ours, through Vivado's hardware manager: litex-boards' own runs
OpenOCD with an FT232 configuration, which is not this cable. A loaded
bitstream lasts until power-off or PROG: the board then loads its flash, which
nothing here writes.

## Stage 1: one 25 MHz clock, integrated RAM

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
25). The tests run against the board with `--serial` and the build's directory:

```sh
sim/litex/l4_test.py   --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong_bram
sim/litex/bios_test.py --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong_bram
```

`l4_test.py` builds the demo and cputest against the board's software, in
`<build-dir>/l4/`, and loads them with `litex_term /dev/ttyUSB0`: full
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

### Opening the serial port while the board talks

When `/dev/ttyUSB0` is opened while the board is printing (its BIOS booting,
say), a fragment of what it printed can come straight back to it as input:
with the port opened 8 s after a load, the BIOS's console read
"ress Q or ESC", from its own "Press Q or ESC to abort boot completely". With
the port open from before the load, never. Where in the host's serial stack
the echo comes from is not settled here; its effect is junk typed into the
BIOS's console, escape sequences included, which left it silent for one run of
`bios_test.py`. With `--serial` the tests now wait for the line to go quiet and
send newlines until a prompt answers one (`simtest.Console.prompt`).

## Stage 2: DDR3, the CPU in its own clock domain

LiteDRAM's Artix-7 PHY runs DDR3 at four times sys, and DDR3 wants 300 MHz or
more: sys at 75 MHz at least, 100 here. RD68031 makes 29.6 MHz. So the CPU has
a clock domain of its own, "cpu", at 25 MHz: one more output of the CRG's MMCM
(`targets/qmtech_wukong.py`), with a 50 % duty cycle, as the core needs.
`RD68031.clock_domain = "cpu"` puts the core and its bridge in it (`core.py`),
and the bus crosses to sys.

- **Timing:** met. The worst slack, 0.38 ns, is on the crossing's paths from
  the CPU's domain to sys (a 10 ns budget: a CPU edge to the next sys edge);
  sys alone 0.48. 16,346 LUTs (26 %).
- **DDR3:** read leveling finds a window on both byte lanes, the BIOS's memory
  test passes, 256 MiB at 0x40000000.
- **Main RAM** at 9.2/7.8 MiB/s (write/read), faster than stage 1's block RAM
  (8.8/7.8): the crossing answers in a CPU clock, LiteDRAM's L2 cache does the
  rest.

### The crossing: synchronous, not LiteX's

The first build used LiteX's `wishbone.ClockDomainCrossing`: asynchronous
FIFOs, two-stage synchronisers each way. It worked, and was slow where it
hurts: the 68030's instruction cache is 256 bytes, so the BIOS runs mostly from
the ROM, every fetch through the crossing. With the BIOS that much slower,
serial boot lost bytes at 115200 baud (14 CRC errors in 9 uploads; none in 40
on the block RAM build), and the console dropped more characters under a
burst (400 of 1000 kept, against 666).

The two clocks are not unrelated, though: both come from one MMCM, 25 MHz is
100 divided by 4, and their edges are aligned. Vivado times paths between such
clocks like any other path. `WishboneSyncCrossing` (`core.py`) uses that: the
request goes to sys as it is, the access starts on the next sys edge, and the
answer, in sys registers, is there for the CPU's next edge: 1 to 2 CPU clocks,
where the asynchronous crossing took 5 or more. Each access is made once, by a
toggle that changes on the first clock of each request; a master that drops
cyc before its answer abandons the access on the sys side as well. Checked
with two clocks in migen's simulator: 300 accesses at random wait states,
back-to-back ones included, and 400 with 18 abandoned, each answered access
made exactly once at the slave.

The asynchronous crossing is still there, `RD68031.clock_synchronous = False`,
for clocks that are not related. The bridge's own time-out is then off, and
`WishboneErrorTimeout` ends an unanswered access on the sys side: the bridge's
would leave the crossing waiting for the answer to an abandoned access, and
LiteX's bus time-out answers with all ones and an ack, not an error, so a read
of nothing would not be a bus error at all (bios_test and cputest saw
`ff ff ff ff` instead of their bus errors, before).

### The reset that never reached the MMCM

`reboot` (the BIOS's, the demo's) writes ctrl's `soc_rst`, a one-sys-clock pulse
that LiteX ORs into the CRG's reset, the MMCM's. LiteX takes the MMCM's reset
through flip-flops clocked by the MMCM's input, 50 MHz here
(`xilinx_common.py`). At 25 MHz sys the pulse is 40 ns and always seen; at 100
it is 10 ns, and a 20 ns clock sees it or not depending on the phase between
the two clocks, which the build fixes: the asynchronous-crossing build saw it,
the next two never did, and `reboot` did nothing.

Stretching it on sys does not work: sys stops while its MMCM is in reset, so a
counter on it never lets the reset go (that build died on its first `reboot`).
The target's CRG now takes the request across as a toggle, which no reset
touches, to the input clock, which never stops, and holds the MMCM's reset for
16 of its clocks there.

### On the board

```sh
sim/litex/l4_test.py   --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong
sim/litex/bios_test.py --serial /dev/ttyUSB0 --build-dir build/qmtech_wukong
```

Each from a freshly loaded bitstream: `l4_test.py` 10 runs out of 10 (the
demo from DDR3, `reboot`, cputest's 74 checks), no CRC error in 20 uploads at
10.9 KB/s with litex_term's full 251-byte frames; `bios_test.py` 4 out of 4;
`reboot` from the BIOS three times in a row, each through DDR3 calibration and
the memory test again.

## For upstream

- `soc_rst` and the 7-series MMCM: the reset request lasts one sys clock, and
  the MMCM's reset is sampled on its input clock (`xilinx_common.py`). With
  sys faster than the input clock, `reboot` works or not depending on the
  build. Stretching it has to happen on a clock that does not stop
  (`targets/qmtech_wukong.py`, `crg()`).
- `wishbone.Timeout` answers a timed-out access with an ack and all ones, not
  with err: an access to nothing is not a bus error unless the CPU's side
  says so.

- `libbase/uart.c`: with interrupts masked, `uart_write` parks what the FIFO
  cannot take in the ring and drains the ring only when the ring is full, so
  an exception handler's last words are lost unless it calls `uart_sync`.
  Writing straight to the FIFO, waiting on `txfull`, when interrupts are
  masked and the ring is empty would avoid it.
- `litex_sim`'s UART (`serial2tcp`, `RS232PHYModel`) has no baud rate on
  either side: the transmit FIFO never fills (above), and received bytes
  arrive faster than the BIOS's serial boot takes them (`doc/l4-bare-metal.md`).
