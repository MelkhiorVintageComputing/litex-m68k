# L10: a device tree, Ethernet and the SD card

L7's kernel finds the SoC through bootinfo records that litexboot writes:
enough for the machine itself (timer, interrupts, UART), not for LiteX's other
drivers. LiteEth reads its buffer layout from its device-tree node, and
LiteSDCard its clock and regulator. So litexboot now passes the device tree
LiteX makes for the SoC as well, and the kernel creates the drivers' devices
from it.

```sh
targets/qmtech_wukong.py --cpu-variant standard+fpu --uart-baudrate 1000000 \
    --with-ethernet --with-sdcard --output-dir build/qmtech_wukong_30m_fpu_1m_io \
    --vivado-place-directive ExtraTimingOpt \
    --vivado-post-place-phys-opt-directive AggressiveExplore \
    --vivado-route-directive Explore --build            # then --load, without --build
sim/litex/l7_test.py --login --io --serial /dev/ttyUSB0 --speed 1000000 \
    --linux-dir ~/LITEX-68K/build/buildroot-fpu/images \
    --build-dir build/qmtech_wukong_30m_fpu_1m_io       # the SD card is overwritten
sim/litex/l7_test.py --login --sdcard \
    --linux-dir ~/LITEX-68K/build/buildroot-fpu/images \
    --build-dir sim/litex/build/standard+fpu/l10        # litex_sim, the SD emulator
```

`scripts/patch_litex.sh` and `scripts/setup_venv.sh` must have been run once,
for the patched `litex_json2dts_linux`.

## The design

- **What stays on bootinfo:** the memory, the command line, the initrd, and
  the machine's own cores (timer0, the SoC controller's reset, the CPU's
  interrupt controller, LiteUART's early console). This is every m68k
  machine's way, and a kernel given no device tree boots as in L7.
- **What the device tree gives:** the devices drivers probe: LiteUART,
  LiteEth, LiteSDCard, their fixed clock and regulator.
- **Who makes it:** LiteX's own `litex_json2dts_linux`, from the build's
  `csr.json`, with an m68k branch (`patches/litex`):
  - an interrupt controller node, `"litex,m68k-intc"`, on the CPU's pending
    and mask CSRs, which the devices' `interrupts` refer to;
  - an initrd default, which the generator needs. litexboot asks it for no
    initrd: `/chosen` is not read.
- **How it gets there:**
  - `images.py --dtc` compiles it (buildroot's `host-dtc`; l7_test.py finds it
    beside `--linux-dir`) and places it 1.5 MiB below the top of main RAM.
  - litexboot passes it in a new bootinfo record, `BI_LITEX_DTB` (address,
    size).
- **In the kernel** (`patches/linux` 0002):
  - the blob is reserved, then unflattened after `paging_init`
    (`litex_init_dt`); the OF core creates the devices;
  - `OF_EARLY_FLATTREE`, which holds `unflatten_device_tree()`, no longer
    excludes this m68k machine;
  - the interrupt controller gets a legacy domain on its node: a device's
    interrupt n stays Linux IRQ `IRQ_USER + n`, so `/proc/interrupts` now
    shows the line too;
  - `of_clk_init()` registers the fixed clocks (`COMMON_CLK`);
  - LiteUART is made from its bootinfo record only when the tree has no node
    for it.

## What the hardware needed

- **The L2 cache off with the SD card** (`--l2-size 0`, the Wukong target's
  default with `--with-sdcard`):
  - The CPU reaches the DDR3 on a LiteDRAM port of its own (L6).
  - LiteSDCard's DMA goes through the SoC bus, where the L2 cache would
    hold its writes away from the CPU and hand it stale lines.
- **A longer bus time-out** (`core.py`, `BUS_TIMEOUT` 1024 to 65536 sys
  clocks):
  - The first write to the card stopped the kernel with a bus error, on a
    write to one of LiteSDCard's CSRs.
  - The time-out (L5) counts from the CPU's request, before the SoC bus's
    arbiter. LiteSDCard's DMA reader keeps the bus while its 16-word FIFO
    has room, each read a DDR3 access with no L2 cache, and the CPU's CSR
    write waited past 1024 clocks.
  - 65536 clocks (655 µs at 100 MHz) is still a prompt bus error for an
    address nobody answers.
- **Timing:** LiteEth's MAC at 100 MHz missed by 0.43 ns with Vivado's
  default directives (7 logic levels in its SRAM writer). The directives
  above meet it (WNS +0.076 ns).
- **The kernel configuration** (`linux-buildroot.config`, both buildroot
  variants): IPv4, packet sockets, LiteEth, MMC, LiteSDCard, VFAT. BusyBox
  gets `mkfs.ext2`, `mkfs.vfat` and `nc`.

## The tests

- **With a device tree** (both settings): the root's compatible is
  `litex,<platform> litex,soc`, and LiteUART's device is its node's
  (e.g. `f0005000.serial`), not the bootinfo's (`liteuart`).
- **`--sdcard`, litex_sim:** litesdcard's emulator, one 512-byte block
  whatever the address. The kernel must find `mmcblk0` and read the block
  (words 0 to 127).
- **`--io`, the board** (`io_checks`):
  - Ethernet:
    - a DHCP lease (`udhcpc`);
    - `ping` of the host;
    - 4 MiB of random data by HTTP from the host (`python3 -m http.server`),
      with a matching md5.
  - SD card:
    - a 64 MiB ext2 filesystem;
    - 4 MiB of random data written;
    - the card unmounted, the caches dropped, mounted again;
    - the file's md5 checked, then the raw read rate measured.

## Results

| | litex_sim (fpu, SD emulator) | Wukong V3 (fpu, Ethernet, SD card) |
|---|---|---|
| device tree | 1623 bytes | 2169 bytes |
| upload | preloaded | 217 s |
| litexboot to the login | 3186 s (L8: 2615 s) | 57 s |
| Ethernet | — | DHCP lease, ping, 4 MiB by HTTP at 111 KB/s, md5 right |
| SD card | `mmcblk0` (125 MiB), the block read back right | 14.9 GiB card: ext2, 4 MiB written and read back right; raw read 8 MiB in 18.5 s (444 KB/s) |
| `l7_test.py` | every command right (see below) | PASS (527 s) |

- **The simulation's run** ended "L7 FAIL" on its very last check, the
  `/proc/interrupts` counts.
  - The interrupt domain adds the line after the chip's name, which
    `l7_test.py`'s regular expression did not expect then; it has been fixed
    since, and the board's run, with the fix, passed.
  - Every command was right before that.
  - Both `/proc/interrupts` snapshots in the log show the counts rising:
    timer0 1480 to 2496, liteuart 171 to 2720.
- **The board's Vivado build:** WNS +0.076 ns.
- **The bus time-out change:** L4 (`l4_test.py --variant standard+fpu`,
  cputest's bus errors at an unmapped address included) passes in litex_sim
  with it.
- **The FPU-less variant** (L9) builds with the same kernel fragment and BusyBox
  (its soft-float check passes), but was not run on L10's SoCs: the Wukong's
  Ethernet and SD bitstream is the FPU one.
- **litex_term's upload** first tries its fast mode, which reports serial
  frame errors at 1 Mbaud, then falls back. It was so before L10 too (L8:
  220 s).
