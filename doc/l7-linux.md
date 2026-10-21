# L7: Linux on RD68031, to a shell

Linux 7.2.9 on RD68031 and its RD68884 FPU, in a LiteX SoC. The SoC is
unchanged; the kernel gets a new m68k machine for it.

```sh
linux/build.sh                                   # kernel, BusyBox, initramfs -> build/linux
sim/litex/fpswitch_test.py                       # FPU context switches, bare-metal, in litex_sim
sim/litex/l7_test.py                             # in litex_sim (about 35 minutes)
sim/litex/l7_test.py --serial /dev/ttyUSB0 --speed 1000000 \
    --build-dir build/qmtech_wukong_30m_fpu_1m   # on the Wukong, BIOS at its prompt
```

## The pieces

- **`linux/`.**
  - `fetch.sh` fetches Linux 7.2.9 and BusyBox 1.38.0, the versions RD68031's
    rd-virt runs, and checks them against the same sha256 (`MANIFEST`).
  - `build.sh` builds the patched kernel over `allnoconfig` plus
    `litex-rd68031.config`, a static BusyBox for the 68030, and an initramfs
    (`cpio.list`, `init`). `config`, `init`, `cpio.list` and `build.sh` are
    adapted from RD68031's rd-virt and keep its CERN-OHL-S licence.
  - The kernel tree is `~/LITEX-68K/src/linux-7.2.9`, a disposable copy made
    from the tarball and `patches/linux/`.
- **`patches/linux/0001-m68k-add-the-LiteX-machine.patch`**: the m68k
  machine, `MACH_LITEX`. Its `README.md` says what each part does and why.
  In short:
  - **Bootinfo.** Its records say where the SoC's cores are, so no address
    is built into the kernel.
  - **Interrupts.** A chained interrupt controller on the CPU's pending and
    mask CSRs gives LiteX line n Linux IRQ `IRQ_USER + n`.
  - **Time.** Timer0 is the clock event device, one-shot or periodic, and
    its 64-bit uptime counter is the clock source.
  - **Console.** LiteUART is a platform device (no device tree; the machine
    selects OF only so that the driver builds) and also gives the earlycon.
  - **CSR window.** It is mapped through TT1, as virt does for its I/O.
- **`sw/litexboot/`**: the loader, built against the SoC's software like
  cputest.
  - The BIOS loads it with the images and jumps to it.
  - It copies vmlinux's segments to the start of main RAM, writes the
    bootinfo at `_end`, and probes the FPU with FNOP. The FPUTYPE record is
    always written: head.S reads garbage without it (RD68031's
    `doc/m13-mismatches.md` b3).
  - It quiets the SoC, turns the caches off and jumps to the kernel.
  - `images.py` lays out the images for a given SoC (`layout.h`, offsets
    into main RAM) and writes `params.bin`: the image addresses, the command
    line, and, if given, the sys clock to tell the kernel. It also writes
    `images.json`, the one map that both `litex_term --images` and litex_sim's
    `--ram-init` take; each jumps to its last entry, litexboot.
- **`sim/litex/fpswitch/`** and `fpswitch_test.py`: two FPU tasks switched by
  timer0 with the FPU saved and restored as Linux does it, each checking its
  results bit for bit. It prints the FSAVE frames it got, by size (below,
  RD68884's release wait).
- **`sim/litex/l7_test.py`**: the boot, `/init`'s report, then a command set
  modelled on rd-virt's `commands.script`: arithmetic, `uname`,
  `/proc/cpuinfo` (68030, its MMU, the 68881), `md5sum`, a file, awk
  floating point, a pipeline, the command line, and the interrupts counting.

## What it took

- **Ticks in the simulation.** litex_sim runs everything at 1 MHz: at
  HZ=100, a tick every 10,000 CPU clocks, about 2,000 instructions, left the
  CPU no time between ticks. The kernel stopped right after starting its
  clock event device. So in the simulation litexboot tells the kernel
  100 MHz (`params.bin`, `--sys-clk`): ticks are 100 times further apart,
  and the kernel's clock runs slow. The board tells it the truth.
- **The UART's events.** On the board the first boot got to `/init`, then
  "irq 8: nobody cared" and the console's interrupt was disabled. The BIOS
  leaves the UART's events (rx and tx) enabled, so the line is up from the
  moment liteuart unmasks it, before it enables its own events, and it gets
  an interrupt it does not handle. litexboot now leaves every core it knows
  quiet: the UART's and the timer's events off and cleared, the interrupt
  mask 0. The simulation booted through it without the fix; why it did not
  hit it there is not known.
- **FSAVE in RD68884's release wait** (fixed upstream, RD68884 7a188db). On the board,
  Linux froze completely, not even echoing, as soon as two processes using
  the FPU preempted each other. Two `seq` running at once was enough. Integer
  work preempted, and FPU work alone, were fine. litex_sim froze the same way.
  - **The reduction.** `sim/litex/fpswitch/` is two bare-metal FPU tasks,
    switched by timer0 at irregular intervals. The FPU is saved and restored
    as Linux's `resume` does it: FSAVE, then FMOVEM if the frame is not null,
    and FRESTORE last. It froze at its eighth switch. litex_sim's trace,
    turned on by the program itself (`sim_trace`, with `--sim-debug`),
    showed where.
  - **What the trace showed.** FSIN was computing, FMUL was latched behind
    it, and the CPU was polling the response CIR (null, CA=1 IA=1). It took
    the timer's interrupt there. FSIN then finished, and the FPU took FMUL,
    posted its release and waited for the CPU to read it. The handler's
    FSAVE meanwhile got come-again ($0118) forever. Earlier switches had
    landed elsewhere and worked.
  - **The fix.** That wait in RD68884's microcode, register to register and
    FMOVECR, looked only for the response read. RD68031 was right throughout.
    - **Ours, at first:** `patches/RD68884` made the wait a call to the
      memory-source paths' wait, which also answers a save request with an
      idle frame and the instruction pending. That cost register-to-register
      instructions a clock.
    - **Upstream's, which replaced it (7a188db):** the wait ends on either
      event (`COND_RESP_SAVE`), then branches to the same save. The
      register-to-register path costs nothing; FMOVECR takes one clock more.
      It also fixes a frame that the MC68882 build saved before the CU's
      first primitive was read.
  - On the way, both bridges got a coprocessor time-out
    (`coprocessor_timeout`, 1024 clocks). A cycle to the FPU that nothing
    answers now ends in a bus error, as a board's bus watchdog would, instead
    of holding the CPU forever. It was not this freeze: there, every cycle was
    answered, with come-again.
  - Upstream's commit carries the ISS test from our patch, for both models,
    and a test of the sequence on its RTL (`fpu_m10`, section H).
    `scripts/sync_pythondata.sh` packages `Inputs/RD68884` as it is again.
- **`console=liteuart0`**, not `console=liteuart`. `console=` doubles as an
  earlycon alias (`earlycon.c`), and `liteuart` alone matches liteuart's
  earlycon with no address: "Malformed early option".
- **Serial boot at 1 Mbaud.** The board's bitstream for L7 has
  `--uart-baudrate 1000000`. litex_term then uploads at about 25 KB/s,
  limited by the BIOS's serial boot rather than the line: the 2.6 MB of
  stripped vmlinux and 1 MB of initramfs take about 2.5 minutes, against 6 at
  115200.

## Results

| | litex_sim (32 MiB, CPU and sys at 1 MHz) | Wukong (256 MiB, CPU 30 MHz, sys 100 MHz) |
|---|---|---|
| Upload (`litex_term --images`, 1 Mbaud) | none: `--ram-init` | 152 s |
| litexboot to the shell | 793 s | 19 s |
| Command set | pass | pass |
| `/proc/cpuinfo` | 68030, MMU 68030, FPU 68881 | 68030, MMU 68030, FPU 68881, BogoMips 6.88 |
| awk `atan2(1,1)*4` | 3.141593 (the FPU: rd-virt's emulation got it wrong) | 3.141593 |
| Two awk preempting each other, `sqrt` sums | both exact | both exact |
| Stress, from a fresh load: two `seq 1 30000` ×3, two awk `sqrt` sums of 20,000 terms ×5 | | pass, every sum exact (it froze before the fix) |
| `fpswitch` (200 switches) | pass: 200 idle frames, results bit-exact; froze at the 8th switch before the fix | |

With upstream's fix (RD68884 7a188db, bitstream WNS 0.112 ns), each from a fresh
load on the Wukong: L7's command set, L8's (`doc/l8-buildroot.md`), the stress
above, and two Lua processes preempting each other ×3 under buildroot, all pass
with every sum exact. `fpswitch` in litex_sim: 200 idle frames, bit-exact.
