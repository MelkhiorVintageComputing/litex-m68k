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
