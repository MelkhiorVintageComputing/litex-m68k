# Linux patches

Against **Linux 7.2.9**, the release tarball (`linux/MANIFEST`, the same as
RD68031's rd-virt), not a git commit. `linux/build.sh` unpacks it into
`~/LITEX-68K/src/linux-7.2.9`, a disposable tree, and applies these patches in
order. To change them, edit that tree (a git repository, its first commit the
pristine release) and regenerate:

    cd ~/LITEX-68K/src/linux-7.2.9 && git add -A && git commit -m ... &&
    git format-patch -o <this directory> <first commit>..

## 0001-m68k-add-the-LiteX-machine.patch

**What:** a new m68k machine, `MACH_LITEX` (15, `CONFIG_M68K_LITEX`), for a
68030-class CPU in a SoC built with LiteX (here RD68031, an MC68030 soft core,
and its RD68884 FPU, on a Wukong V3 or in litex_sim).

- `arch/m68k/litex/`:
  - `config.c`: the bootinfo records, the earlycon on LiteUART, and reset through
    the SoC controller.
  - `ints.c`: the CPU's interrupt controller. Every SoC line is on one
    autovectored level, read from two CSRs, pending and mask (this project's
    `core.py`); a chained handler gives line n Linux IRQ `IRQ_USER + n`.
  - `timer.c`: LiteX's Timer as the clock event device, one-shot and periodic,
    and its 64-bit uptime counter as the clock source.
  - `platform.c`: LiteUART as a platform device.
- `uapi/asm/bootinfo-litex.h`: the records that say where the SoC's cores are.
  The boot loader (this project's `sw/litexboot`) writes them from the SoC's
  generated headers, so that no address is built into the kernel.
- `head.S`: the CSR window (0xf0000000 up, above the KMAP area) mapped
  transparently through TT1, uncached, as `virt` does for 0xff000000.
  `kmap.c`: `ioremap` there returns the physical address.
- The usual places where a machine is listed: `bootinfo.h`, `setup.h`,
  `config.h`, `setup_mm.c`, `irq.h` (`NR_IRQS`), `Kbuild`, `Kconfig.machine`.

The machine selects `OF` so that `SERIAL_LITEUART` (which `depends on OF`)
can be built; without a device tree (0002), LiteUART is probed as a plain
platform device, by name.

**Why:** m68k Linux finds its devices through bootinfo records, per machine.
LiteX's own Linux support is device tree based (RISC-V, or1k). Its DTS
generator has no m68k branch, and mainline has no driver for LiteX's Timer or
for this CPU's interrupt CSRs.

**Upstream:** not sent. Written so that it could be: the SoC's layout comes
from the boot loader, and nothing is specific to RD68031.

## 0002-m68k-litex-the-SoC-s-devices-from-a-device-tree.patch

**What:** the SoC's devices from a flattened device tree that the boot loader
passes (L10, `doc/l10-devicetree.md`).

- `BI_LITEX_DTB` (0x8005): the blob's physical address and size. `config.c`
  reserves it for good (the unflattened tree points into it) and, after
  `paging_init`, `litex_init_dt()` verifies and unflattens it; the OF core then
  creates the devices. `/memory` and `/chosen` are not read: the memory, the
  command line and the initrd stay the bootinfo's.
- `drivers/of/Kconfig`: `OF_EARLY_FLATTREE`, which holds
  `unflatten_device_tree()`, excluded every m68k; now every m68k but this
  machine.
- `ints.c`: the tree's `"litex,m68k-intc"` node gets a legacy domain, so a
  device's interrupt n is line n, Linux IRQ `IRQ_USER + n` as before. New
  binding: `Documentation/devicetree/bindings/interrupt-controller/litex,m68k-intc.yaml`.
- `timer.c`: `of_clk_init()`, for the tree's fixed clocks (LiteSDCard asks for
  its clock); the machine selects `COMMON_CLK` and `IRQ_DOMAIN`.
- `platform.c`: LiteUART from its bootinfo record only when the tree has no
  node for it.

Without `BI_LITEX_DTB`, nothing changes (L7's kernel and litexboot images).

**Why:** LiteEth reads its buffer layout from its node's properties, and
LiteSDCard its clock and regulator: they need a device tree. LiteX's
`litex_json2dts_linux` makes one (with `patches/litex`'s m68k branch).

**Upstream:** not sent; goes with 0001. A sign-off is needed.
