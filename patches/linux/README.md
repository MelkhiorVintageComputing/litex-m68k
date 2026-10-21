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

The machine selects `OF` without using a device tree. That is only so that
`SERIAL_LITEUART` (which `depends on OF`) can be built. LiteUART is then
probed as a plain platform device, by name.

**Why:** m68k Linux finds its devices through bootinfo records, per machine.
LiteX's own Linux support is device tree based (RISC-V, or1k). Its DTS
generator has no m68k branch, and mainline has no driver for LiteX's Timer or
for this CPU's interrupt CSRs.

**Upstream:** not sent. Written so that it could be: the SoC's layout comes
from the boot loader, and nothing is specific to RD68031.
