# LiteX patches

Against **litex 111e2948d** (`~/LITEX/litex`, the commit checked out there).
`scripts/patch_litex.sh` clones it to `~/LITEX-68K/litex`, a disposable copy,
and applies these patches in order (`git am`). `scripts/setup_venv.sh` then
puts that copy on the venv's path in place of `~/LITEX/litex`. To change them,
commit in the copy and regenerate:

    git -C ~/LITEX-68K/litex format-patch -o <this directory> 111e2948d..

## 0001-litex_json2dts_linux-add-m68k.patch

**What:** `litex_json2dts_linux` for `cpu_family == "m68k"` (RD68031):

- an `intc0` node, `compatible = "litex,m68k-intc"`, on the CPU's CSRs: every
  SoC interrupt line on one autovectored level, read from a pending (+0x0) and
  a mask (+0x4) register (this project's `core.py`). The other nodes'
  `interrupts` refer to it, as they do to or1k's PIC.
- an `initrd_start` default, as for the other families, which the generator
  needs; litexboot's images ask for none (`initrd="disabled"`), since m68k
  Linux takes its memory, command line and initrd from the bootinfo.

**Why:** L10 (`doc/l10-devicetree.md`). LiteEth and LiteSDCard's drivers need
a device tree, and the generator had no m68k branch: it stopped at the initrd
default (a `KeyError`), and had no interrupt controller to give the devices.
The binding is in `patches/linux` 0002
(`Documentation/devicetree/bindings/interrupt-controller/litex,m68k-intc.yaml`).

**Upstream:** not sent. It goes with the kernel's binding; a sign-off is needed.

## 0002-liblitesdcard-read-the-SCR-in-the-card-s-byte-order-.patch

**What:** `sdcard_decode_scr()` reads the card's SCR as the bytes the DMA wrote,
most significant first, instead of byte-swapping the two words it read.

**Why:** the swap was right only on a little-endian CPU. On RD68031 (big-endian)
the SCR came out scrambled and its 4-bit support bit read 0, so the BIOS's
`sdcard_init` left the card and LiteSDCard's PHY in 1-bit mode. The BIOS's own
reads still worked (slowly), but Linux's `litex_mmc`, taking the card over, puts
it in 4-bit mode and assumes the PHY's reset setting (4-bit): its first data
transfer then failed on CRC, and a root filesystem on the card was never found
(L10, booting from the SD card: `doc/l10-devicetree.md`). Found by reading the
PHY's `settings` CSR from the BIOS: 1 (4-bit) after reset, 0 after `sdcard_init`.

**Upstream:** not sent; a sign-off is needed. On a little-endian CPU the result
is the same as before.
