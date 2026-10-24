# Porting a BSD operating system to LiteX-68K

What a BSD kernel needs in order to run on RD68031 (an MC68030) in a LiteX SoC,
as this project builds it: the boot code that exists, the drivers a port must
write, and a specification for each one. It is written for someone who has not
seen this project. Read it beside the MC68030 user's manual and LiteX's
generated headers for the SoC you build.

## The clean-room rules of this document

The goal is a port free of GPL-licensed code. Every fact here comes from one of
these sources, and each section says which:

- **[LiteX]**: LiteX, LiteEth and LiteSDCard (gateware and the BIOS's software
  libraries), BSD-2-Clause, Enjoy-Digital and the LiteX developers. Code is
  quoted from them, with attribution.
- **[this project]**: this repository's own code, BSD-2-Clause: the CPU class
  (`litex_68k/cpu/rd68031/core.py`), the bus bridges, `sw/litexboot/`, the
  tests. Code may be quoted.
- **[observed]**: behaviour measured on the hardware or in litex_sim while
  bringing Linux up (`doc/l7-linux.md` to `doc/l10-devicetree.md`). These are
  facts about the hardware, written as requirements, with no code.
- **[Motorola]**: the MC68030 and MC68881 manuals in `Inputs/doc/`.

**Nothing here is taken from Linux source.** This project's Linux port
(`patches/linux`) is GPL, and so is the rest of the kernel. A port must not
read them for implementation. Requirements that were learned by running Linux
are stated as hardware behaviour. Two interface facts are not code: the
bootinfo record format, which litexboot writes, and the device-tree binding
this project wrote (`litex,m68k-intc`). Both are restated here from this
project's BSD sources. The `linux-on-litex-vexriscv` repository's kernel
patches are GPL too, and are out of bounds for the same reason.

**A BSD starting point.** NetBSD has m68k ports booted the way Linux/m68k is:
a kernel ELF plus a list of bootinfo records after it. Its `virt68k` port, for
QEMU's `virt` machine, is the closest relative. That port parses Linux-style
bootinfo, and QEMU's machine is shaped like this SoC: a 68030/68040 with MMIO
devices and one interrupt controller. Check the port exists in the NetBSD tree
you use. Its code is BSD-licensed and fine to start from; QEMU's own source is
GPL and is not.

---

## 1. The platform

[this project, LiteX]

| | |
|---|---|
| CPU | RD68031: MC68030, with its MMU (TC, CRP, SRP, TT0/TT1), 256-byte instruction and data caches, burst fills. Built to run what an MC68030 runs, and verified against NetBSD and SunOS on a Sun-3/80 model (`Inputs/RD68031/README.md`). Variants: `standard` (caches), `minimal` (none), `+fpu` (RD68884, an MC68881, on coprocessor ID 1) |
| Clocks | CPU 30 MHz on the Wukong V3 (its own MMCM); the SoC bus, CSRs and every LiteX core at **sys = 100 MHz**. litex_sim runs everything at 1 MHz |
| Byte order | big-endian throughout. The bridge puts byte 0 on bus bits 31..24; nothing is swapped |
| Bus | one Wishbone master (the bridge); main RAM on a 128-bit LiteDRAM port of the CPU's own when the board has DDR3 (L6) |
| Interrupts | every SoC interrupt line on **IPL 2, autovectored (vector 26)**; a pending and a mask register in CSR space (section 6.1) |

### Memory map

These are the Wukong builds'; read your own from the build's `csr.csv` or
`software/include/generated/{mem,csr,soc}.h`.

| Physical | Size | What | Cached |
|---|---|---|---|
| `0x0000_0000` | 128 KiB | ROM: the LiteX BIOS; the reset vectors are read at 0 (UM 8.1.1) | yes |
| `0x0100_0000` | 8 KiB | SRAM: the BIOS's stack and data | yes |
| `0x4000_0000` | 256 MiB (Wukong DDR3; 32 MiB in litex_sim) | main RAM | yes |
| `0x8000_0000` | 8 KiB | LiteEth's packet buffers (`ethmac`), when built with Ethernet | **no** |
| `0xF000_0000` | 64 KiB | CSRs: every core's registers | **no** |

- **`0x8000_0000` and up is the I/O region.** The bridge asserts CIIN there,
  so the CPU never caches it, whatever the MMU says.
- **Writes outside it are posted**: the bridge acknowledges them before they
  reach memory. Requests are still carried out one at a time, in program
  order.
- **An access nobody answers is a bus error** after `BUS_TIMEOUT` sys clocks
  (65536, 655 µs; `core.py`). Probing an address with a bus-error handler
  therefore works, if slowly.
- **The FPU has its own time-out** (`coprocessor_timeout`, 1024 clocks). A
  coprocessor cycle no FPU answers is a bus error, which is how line F
  arrives on a CPU without one.
- **LiteX's own Wishbone time-out is not used.** It would answer with all
  ones and an ack, and the CPU would never see an error.

### What runs before the OS

1. **The LiteX BIOS in ROM** initialises the DDR3. Its boot sequence then
   tries serial boot (`litex_term`), then the SD card, then the network
   (priorities 0, 30, 50); otherwise it waits at its prompt.
2. **The BIOS loads a set of images** and jumps to the last one,
   **litexboot** (section 3).
3. **litexboot** loads the kernel's ELF, writes the bootinfo, quiets the SoC
   and jumps to the kernel.

The DDR3 is calibrated by the BIOS. The OS must never touch the `ddrphy` or
`sdram` CSRs.

---

## 2. What a port needs

| Piece | Kind | Status | Section |
|---|---|---|---|
| LiteX BIOS | boot ROM | exists (LiteX); needs `patches/litex` 0002 to boot from the SD card on a big-endian CPU | 3.4 |
| litexboot | second-stage loader | exists (this project, BSD); usable as is for an ELF kernel linked like Linux/m68k, or adapted (3.3) | 3 |
| Kernel bootstrap (locore, MMU, bootinfo parsing) | MD | to write; NetBSD/virt68k is the model | 3, 5 |
| CPU interrupt controller | MD, **required** | to write | 6.1 |
| Timer0: clock interrupt and time counter | MD, **required** | to write | 6.2 |
| LiteUART: console and tty | driver, **required** | to write | 6.3 |
| SoC controller: reset | MD, **required** for reboot | to write (one register) | 6.4 |
| FPU (RD68884 = MC68881) or its absence | MD, **required** | standard m68k code; FPU-less needs an emulator or a soft-float userland | 6.5 |
| LiteSDCard: block device (sdmmc host) | driver, for a root on SD | to write | 6.6 |
| LiteEth: network interface | driver, optional | to write | 6.7 |
| MDIO (Ethernet PHY management) | optional | to write (bit-banged) | 6.7.4 |
| LEDs | optional | one register | 6.8 |
| Device tree (FDT) support | optional | a DTB is available from litexboot | 7 |
| **Not needed** | | `ddrphy`, `sdram` (BIOS-owned), `identifier_mem` (an ID string, optional) | |

---

## 3. Booting

[this project: `sw/litexboot/`; LiteX: the BIOS]

### 3.1 The images and their layout

`sw/litexboot/images.py` places the images in main RAM. Offsets are from main
RAM's base B, for a main RAM of size S (`layout.h`, BSD):

| Address | What |
|---|---|
| B + S/2 | the kernel ELF, as a file (stripped) |
| B + 3S/4 | the initramfs, if any |
| B + S − 2 MiB | `params.bin`: struct params, below |
| B + S − 1.5 MiB | the flattened device tree (`soc.dtb`), if any |
| B + S − 1 MiB | litexboot, and its stack above it |

```c
/* sw/litexboot/layout.h (this project, BSD-2-Clause), big-endian, written by images.py */
#define PARAMS_MAGIC    0x4c584250  /* "LXBP" */
#define PARAMS_VERSION  2
#define PARAMS_CMDLINE  256
struct params {
	unsigned long magic, version;
	unsigned long elf_addr, elf_size;
	unsigned long initrd_addr, initrd_size;   /* initrd_size 0: none */
	unsigned long sys_clk;                    /* Hz told to the kernel; 0: the SoC's */
	unsigned long dtb_addr, dtb_size;         /* dtb_size 0: none */
	char cmdline[PARAMS_CMDLINE];
};
```

`images.json` maps each file to its address; litexboot comes last. It is
handed to `litex_term --images` for a serial upload, to litex_sim's
`--ram-init`, and, renamed `boot.json`, to the BIOS's SD card boot.

### 3.2 What litexboot does, and the state at the kernel's entry

1. **Loads the kernel.** Each `PT_LOAD` segment goes to **B + p_paddr**,
   with its BSS cleared. The kernel is expected to be linked as Linux/m68k
   kernels are: physical addresses from 0, the start of main RAM. The kernel
   then runs where virtual 0 is the start of its first memory chunk.
2. **Probes the FPU.** It runs FNOP with the line-F vector pointed at a
   catcher, and decides 68881 or none.
3. **Writes the bootinfo** right after the kernel image: at B + max(p_paddr +
   p_memsz), section 3.5.
4. **Quiets the SoC**:
   - the interrupt mask to 0;
   - the UART's and timer0's events off and their pending bits cleared;
   - timer0 stopped;
   - LiteSDCard's PHY data width set back to 4-bit (6.6.3).
5. **Jumps to B + e_entry** in this state:
   - supervisor mode, SR = `0x2700` (IPL 7);
   - MMU off (it never was on);
   - both caches cleared and disabled (CACR = `0x0808`: CI and CD, EI and
     ED clear);
   - VBR still the BIOS's, and the stack litexboot's: **the kernel sets its
     own VBR and stack**;
   - no register carries an argument: **the kernel finds the bootinfo after
     its own end**.

```asm
| sw/litexboot/probe.S (this project, BSD-2-Clause)
jump_kernel:
        move.l  4(%sp), %a0
        move.w  #0x2700, %sr
        move.l  #0x0808, %d0
        movec   %d0, %cacr
        jmp     (%a0)
```

### 3.3 Booting a BSD kernel with it

- **As is:** if the kernel's ELF has its `p_paddr` counted from 0 (the
  start of main RAM) and it reads Linux-style bootinfo at its end,
  litexboot boots it unchanged. QEMU boots NetBSD/virt68k with Linux-style
  bootinfo; check how its kernel is linked and where it looks for the
  records before relying on this.
- **Otherwise, adapt litexboot**, which is BSD, or write a loader of your
  own. Typical changes:
  - accept absolute physical addresses (`p_paddr` ≥ `MAIN_RAM_BASE`);
  - pass a pointer to the bootinfo in a register;
  - add records for the cores litexboot does not describe yet (LiteEth,
    LiteSDCard: take the next free tags, `0x8006` and up, and keep them in
    step with this project's Linux patch so both stay compatible).
- litexboot is built against the SoC's generated headers (`sw/litexboot/Makefile`
  includes the SoC's `variables.mak` and LiteX's `common.mak`).

### 3.4 Boot sources

- **Serial:**
  - `litex_term --images images.json --speed 1000000 /dev/ttyUSB0`, with
    the BIOS at its prompt or at its serial-boot step;
  - about 25 KB/s at 1 Mbaud, limited by the BIOS: a 4 MB kernel takes
    about 3.5 minutes;
  - its fast mode reports serial frame errors at 1 Mbaud, then falls back
    to 64-byte frames; that is expected.
- **SD card:**
  - the BIOS reads `boot.json` from the first FAT partition and loads every
    file it names at its address. It jumps to the last image, or to `"addr"`
    if given;
  - this needs `patches/litex` 0002 on a big-endian CPU (6.6.3);
  - with a `boot.json` on the card, the BIOS boots from it on every FPGA
    load. Press Q at "Press Q or ESC" to get its prompt.
- **litex_sim:** `--ram-init images.json` preloads main RAM, and the BIOS
  jumps to litexboot.

### 3.5 The bootinfo

[this project: `sw/litexboot/bootinfo.h`, `main.c`]

The bootinfo is a list of records, each starting on a 4-byte boundary,
big-endian:

    u16 tag; u16 size;   /* size: of the whole record, header included, a multiple of 4 */
    u8  data[size - 4];

The list ends with a record of tag 0 (`BI_LAST`).

| Tag | Name | Data |
|---|---|---|
| `0x0001` | `BI_MACHTYPE` | u32 15 (LiteX; a number of this project's, not assigned upstream) |
| `0x0002` | `BI_CPUTYPE` | u32 `0x2` (68030) |
| `0x0003` | `BI_FPUTYPE` | u32 `0x1` (68881) or 0 (none): **always present** |
| `0x0004` | `BI_MMUTYPE` | u32 `0x2` (68030) |
| `0x0005` | `BI_MEMCHUNK` | u32 physical address, u32 size: main RAM |
| `0x0006` | `BI_RAMDISK` | u32 physical address, u32 size: the initramfs (absent if none) |
| `0x0007` | `BI_COMMAND_LINE` | a NUL-terminated string |
| `0x8000` | `BI_LITEX_SYS_CLK` | u32 Hz: the frequency to time with (in litex_sim, 100 MHz although it runs at 1 MHz, so that ticks leave the CPU time) |
| `0x8001` | `BI_LITEX_IRQCTRL` | u32 base of the CPU's interrupt CSRs, u32 IPL (2) |
| `0x8002` | `BI_LITEX_CTRL` | u32 base of the SoC controller, u32 0 |
| `0x8003` | `BI_LITEX_UART` | u32 base, u32 interrupt line |
| `0x8004` | `BI_LITEX_TIMER0` | u32 base, u32 interrupt line |
| `0x8005` | `BI_LITEX_DTB` | u32 physical address, u32 size of a flattened device tree (section 7). It must be kept reserved if the kernel uses it |

Interrupt lines and CSR bases differ from one SoC build to the next: take
them from the bootinfo (or the DTB), never from constants.

---

## 4. CSRs: how to access them

[LiteX; this SoC's `config_csr_*` constants]

- **Width and alignment:** each CSR is one or more **32-bit words, 4-byte
  aligned** (`csr_data_width` 32, `csr_alignment` 32). Access them with
  32-bit moves. Only a register's own bits are meaningful; write the others
  as 0.
- **Multi-word CSRs, "big" ordering** (`config_csr_ordering_big`): the
  **first word holds the most significant 32 bits**. Examples:
  - `timer0_uptime_cycles`, 64 bits: high word at +0, low at +4;
  - the SD DMAs' `base`, 64 bits: high at +0, low at +4;
  - `sdcard_core_cmd_response`, 128 bits: bits 127..96 at +0, bits 31..0
    at +0x0C.
- **Mapping:** the CSRs must be mapped **cache-inhibited**, supervisor only.
  The bridge makes the CPU see them uncached anyway (CIIN).
  - This project's Linux maps 0xF000_0000-0xFFFF_FFFF with a transparent
    translation register (TT1), above the kernel's virtual map.
  - Page tables with CI set work as well.
- **Event managers.** Most cores raise interrupts through an event manager:
  three CSRs, `ev_status` (the sources' current state), `ev_pending` and
  `ev_enable`, one bit per source. A core's interrupt line is `pending &
  enable`, **as a level**. A source is one of:
  - **pulse or edge** (`EventSourcePulse`, `EventSourceProcess`): `pending`
    latches; **write 1 to clear it**;
  - **level** (`EventSourceLevel`): `pending` *is* the condition, and
    writing 1 does not clear it. What writing does depends on the core: for
    the UART's RX and LiteEth's receive it **consumes an entry** (6.3, 6.7).
    Otherwise, **disable the source in `ev_enable`** to stop its interrupt.

  Each source's bit position is in `csr.h` (`CSR_<CORE>_EV_PENDING_<SOURCE>_OFFSET`).
  Positions change between core versions: take them from the headers of the
  SoC you build.

---

## 5. CPU-level requirements

[Motorola; this project; observed]

- **Caches.**
  - The data cache is write-through (UM 6.1.2), with logical tags, and
    direct-mapped by address bits A7-A4. Two virtual aliases of one page at
    the same offset share a line, and a context switch needs the usual
    68030 care.
  - To clear a cache, set CACR's CI or CD: those bits read as 0, so OR-ing
    one into the current CACR clears the cache and changes nothing else.
  - With the instruction cache on, code written as data runs stale until
    CI is set (a trampoline, a module loader).
- **MMU.** The 68030 PMMU (UM 9).
  - cputest checks TC `0x80c08480` (4 KiB pages, TIA 8, TIB 4, TIC 8),
    early-termination descriptors, write-protect, invalid pages, the U and M
    bits, and PTEST.
  - A BSD m68k pmap for the 68030 should run unchanged.
- **Exceptions** follow UM 8, including bus and address error frames
  (formats $A and $B) with the SSW and the fault address. Bus errors on
  unanswered addresses carry the FC and the data-fault bits as a real 68030
  does (cputest).
- **Interrupts.**
  - All devices share IPL 2 (vector 26). So, as seen by the CPU, every
    device `spl` level is IPL 2: `splclock`, `splbio`, `splnet` and `spltty`
    all raise the SR to at least 2.
  - A finer software priority, if wanted, is done with the mask CSR
    (6.1).
  - Levels 1 and 3-7 never occur, except NMI-style level 7 if a target
    adds one.
- **FPU.** Section 6.5.

---

## 6. Device specifications

### 6.1 The CPU's interrupt controller

[this project: `core.py`; DT binding written by this project]

Two CSRs at the `cpu` CSR base (`BI_LITEX_IRQCTRL`):

| Offset | Name | Access | Meaning |
|---|---|---|---|
| +0x0 | `irq_pending` | RO | bit n: line n is up now (lines are levels, active high, one per core) |
| +0x4 | `irq_mask` | RW | bit n: line n may raise IPL 2; reset value 0 |

Any line with `pending & mask` set holds IPL 2 up. The CPU takes autovector 26.

**Driver requirements:**

- **Handler:** read `pending & mask`, then for each set bit, lowest first,
  call that line's handler. Repeat until nothing is left, or return and let
  the level re-interrupt.
- **Acknowledgement is at the device:** a line stays up until its core's
  event is cleared or disabled, as 4 says. Nothing is acknowledged in the
  controller.
- **Spurious interrupts:** a level-2 interrupt with nothing pending can
  happen (a line that dropped). Ignore it.
- **Masking:** a line is masked or unmasked by a read-modify-write of
  `irq_mask`, interrupts off around it. Keep a software copy; reading it
  back works too.
- **Start with everything masked, and the cores quiet** (litexboot leaves it
  so).
  - [observed] The BIOS leaves the UART's RX and TX events enabled.
  - A driver that unmasks a line before it has set up its core's events gets
    an interrupt it does not handle, and an OS that disables an interrupt
    nobody claimed then loses it for good.
  - Unmask a line only after its driver has set its events.
- **Numbering:** this project's Linux gives line n IRQ number `base + n`, and
  that number is what `/proc/interrupts` shows. A BSD port is free to number
  as it likes.

### 6.2 Timer0: the clock

[LiteX: `litex/soc/cores/timer.py`, its own documentation]

A 32-bit down-counter on sys_clk, and a 64-bit uptime counter (the SoC is built
with `--timer-uptime`).

| Offset | Name | Access | Meaning |
|---|---|---|---|
| +0x00 | `load` | RW | value loaded when `en` goes from 0 to 1 (one-shot duration) |
| +0x04 | `reload` | RW | value loaded when the count reaches 0 (period); 0: stop at 0 |
| +0x08 | `en` | RW | 1: count; 0: hold at `load` |
| +0x0C | `update_value` | W | writing latches the current count into `value` |
| +0x10 | `value` | RO | the latched count |
| +0x14 | `ev_status` | RO | bit 0 `zero`: count is 0 |
| +0x18 | `ev_pending` | RW | bit 0: count reached 0 (rising edge, latched); write 1 to clear |
| +0x1C | `ev_enable` | RW | bit 0 |
| +0x20 | `uptime_latch` | W | writing latches the uptime counter |
| +0x24 | `uptime_cycles` | RO | 64 bits, sys clocks since power-up: high word +0x24, low +0x28 |

**Driver requirements:**

- **Periodic tick at HZ:**
  1. `en = 0`, `load = 0`, `reload = sys_clk/HZ - 1` (the count runs from
     `reload` down to 0, `reload + 1` clocks a period);
  2. `ev_pending = 1`, `ev_enable = 1`, `en = 1`, then unmask the line.
  3. Each interrupt: `ev_pending = 1`, then hardclock.
- **One-shot (tickless):**
  1. `en = 0`, `reload = 0`, `load = n`, `en = 1`;
  2. the event fires when the count reaches 0, and the count then stays at 0.
- **Time counter:**
  1. write 1 to `uptime_latch`;
  2. read the high word, then the low word. Both come from the one latched
     value, so they are consistent.

  It runs at `BI_LITEX_SYS_CLK` (100 MHz) and is 64 bits wide, so it never
  wraps. A good timecounter.
- **sys_clk comes from `BI_LITEX_SYS_CLK`.**
  - [observed] In litex_sim everything runs at 1 MHz, and a tick every
    10,000 CPU clocks left the kernel no time to run.
  - litexboot tells the kernel 100 MHz there (`--sys-clk`), and time runs
    slow.

### 6.3 LiteUART: console and tty

[LiteX: `litex/soc/cores/uart.py`, `libbase/uart.c`]

16-byte RX and TX FIFOs; the baud rate is fixed in the gateware (the Wukong
builds: 1,000,000; litex_sim: none). There are no line settings to make.

| Offset | Name | Access | Meaning |
|---|---|---|---|
| +0x00 | `rxtx` | RW | write: queue a byte for transmission; read: the byte at the RX FIFO's head (**the read does not consume it**) |
| +0x04 | `txfull` | RO | 1: TX FIFO full |
| +0x08 | `rxempty` | RO | 1: RX FIFO empty |
| +0x0C | `ev_status` | RO | bit 0 `tx`: TX FIFO not full; bit 1 `rx`: RX FIFO not empty |
| +0x10 | `ev_pending` | RW | same bits (both are level sources); **writing 1 to bit 1 consumes the RX byte at the head** |
| +0x14 | `ev_enable` | RW | bit 0 TX, bit 1 RX |
| +0x18 | `txempty` | RO | 1: TX FIFO empty (every byte gone to the line) |
| +0x1C | `rxfull` | RO | 1: RX FIFO full |

**Driver requirements:**

- **Polled console output:** wait while `txfull`, then write `rxtx`. It needs
  no interrupt, and works from the first instruction (the CSRs are at their
  physical address if the MMU is off or a TT maps them). This is the early
  console.
- **Polled input:**
  1. if `!rxempty`, read `rxtx`;
  2. then write `ev_pending = 2` to advance the FIFO.
- **Interrupt-driven:**
  - **RX:** enable bit 1. In the handler, while `!rxempty`: read `rxtx`,
    write `ev_pending = 2`, and pass the byte up.
  - **TX:** TX is a level source, "FIFO not full", which is true almost all
    the time.
    - Enable bit 0 only while there is output queued, and disable it when
      the queue empties.
    - In the handler, write bytes while `!txfull`.
- LiteX's own BSD driver does exactly this:

```c
/* litex/soc/software/libbase/uart.c (LiteX, BSD-2-Clause, Enjoy-Digital & LiteX developers) */
	stat = uart_ev_pending_read() & uart_ev_enable_read();
	if(stat & UART_EV_RX) {
		while(!uart_rxempty_read()) {
			c = uart_rxtx_read();
			/* ... store c ... */
			uart_ev_pending_write(UART_EV_RX);
		}
	}
	if(stat & UART_EV_TX) {
		while((tx_consume != tx_produce) && !uart_txfull_read()) {
			uart_rxtx_write(tx_buf[tx_consume]);
			tx_consume = (tx_consume + 1) & UART_RINGBUFFER_MASK_TX;
		}
		if(tx_consume == tx_produce)
			uart_ev_enable_write(UART_EV_RX); /* Disable TX interrupt */
	}
```

### 6.4 The SoC controller: reset

[LiteX: `SoCController`]

| Offset | Name | Meaning |
|---|---|---|
| +0x0 | `reset` | bit 0 `soc_rst`: write 1 to reset the whole SoC (a pulse: CPU, cores, then the BIOS runs again); bit 1 `cpu_rst`: hold the CPU in reset |
| +0x4 | `scratch` | read-write, resets to `0x12345678`: a bus check |
| +0x8 | `bus_errors` | count of Wishbone bus time-outs |

**Reboot:** interrupts off, write 1 to `reset`, then loop. There is no power-off
device: halt in a loop.

### 6.5 The FPU

[Motorola MC68881UM; this project; observed]

- **Detection.**
  - `BI_FPUTYPE` says 68881 or 0, from litexboot's FNOP probe. Trust it, or
    probe the same way: FNOP with the line-F vector caught.
  - On a CPU without the FPU, an FPU instruction is line F (vector 11), as on
    a real 68030 with none.
- **With RD68884:** an MC68881 on coprocessor ID 1.
  - FSAVE/FRESTORE frames and the context switch follow the MC68881's
    manual.
  - The usual switch works: FSAVE, then FMOVEM of the registers if the frame
    is not null, and FRESTORE last.
  - **Use RD68884 from 7a188db or later.** Before it, an FSAVE that hit the
    FPU in one particular window (a register-to-register instruction latched
    behind a long one, while the CPU waited) got "come again" forever, and
    the machine froze as soon as two processes using the FPU preempted each
    other (`doc/l7-linux.md`).
  - **And from 7ec30da or later.** Before it, a conditional (FBcc, FScc,
    FTRAPcc, FDBcc) waiting on a long instruction lost its answer if the
    CPU took an interrupt during the wait and the handler's FSAVE came after
    the FPU had answered: the frame was idle, and after FRESTORE the branch
    went the wrong way. [observed] Under NetBSD, two processes looping on
    libm's `log`, `exp` and `fmod` got NaN, 0 or inf now and then.
  - **And from 7c668f4 or later.** Before it, an instruction saved busy in
    the middle of its operand transfer (a page fault on the operand's
    second long word) rounded with the FPCR of whatever ran between FSAVE
    and FRESTORE: another process, or a signal handler. [observed] Under
    NetBSD, an FDIV.D faulting so while a signal handler rounded to zero
    was rounded to zero every time.
- **Without an FPU:** the kernel must not run FSAVE/FRESTORE or any FPU
  instruction.
  - [observed] A kernel built for an FPU ran its context switch's FSAVE
    unconditionally and stopped at boot.
  - Either the kernel emulates the FPU on line F (NetBSD has a BSD-licensed
    m68k FPU emulator, `fpe`), or the userland is soft-float.
  - For the latter, this project's soft-float toolchain work is in
    `doc/l9-nofpu.md`: gcc and musl patches, both GPL/MIT projects
    themselves.

### 6.6 LiteSDCard: the SD card

[LiteX/LiteSDCard: `litesdcard/{phy,core}.py`, `litex/soc/integration/soc.py`,
`liblitesdcard/sdcard.c`; observed]

The SD card is in native mode, 1-bit or 4-bit (8-bit for eMMC), with hardware
CRC and a DMA in each direction. Five register groups, contiguous
(`csr.csv`, `sdcard_*`); offsets from the `sdcard` base on this build:

| Offset | Name | Access | Meaning |
|---|---|---|---|
| **PHY** | | | |
| +0x00 | `phy_card_detect` | RO | the card-detect pin: **0 = a card is present** (always 0 on a board without the pin); its event fires on every change |
| +0x04 | `phy_clocker_divider` | RW | 9 bits, reset 256; card clock = sys_clk / ((divider + 1) & ~1); keep it in 2..256 |
| +0x08 | `phy_init_initialize` | W | writing 1 sends the 80 idle clocks a card needs before CMD0 |
| +0x0C | `phy_cmdr_timeout` | RW | command-response time-out, sys clocks |
| +0x10 | `phy_dataw_status` | RO | after a write: bit 0 accepted, bit 1 CRC error, bit 2 write error |
| +0x14 | `phy_datar_timeout` | RW | data-read time-out, sys clocks |
| +0x18 | `phy_settings` | RW | bits 1..0 `data_width`: 0 = 1-bit, **1 = 4-bit (reset value)**, 2 = 8-bit |
| **Core** | | | |
| +0x1C | `core_cmd_argument` | RW | the command's 32-bit argument |
| +0x20 | `core_cmd_command` | RW | bits 1..0 response type (0 none, 1 short, 2 long, 3 short with busy); bit 2 check the response's CRC7; bits 6..5 data (0 none, 1 read, 2 write); bits 13..8 the command index |
| +0x24 | `core_cmd_send` | W | write 1: run the command (and its data) |
| +0x28 | `core_cmd_response` | RO | 128 bits, +0x28..+0x34; **a short response's 32 bits are the last word, +0x34**; a long (R2) response is bits 127..0, most significant at +0x28 |
| +0x38 | `core_cmd_event` | RO | bit 0 done, bit 1 error, bit 2 time-out, bit 3 CRC error |
| +0x3C | `core_data_event` | RO | the same bits, for the data phase |
| +0x40 | `core_block_length` | RW | bytes per block (10 bits) |
| +0x44 | `core_block_count` | RW | blocks |
| **Block → memory DMA** (reads) | | | |
| +0x48 | `block2mem_dma_base` | RW | 64 bits (high +0x48, low +0x4C): the physical buffer address |
| +0x50 | `block2mem_dma_length` | RW | bytes |
| +0x54 | `block2mem_dma_enable` | RW | 0 then 1 to arm |
| +0x58 | `block2mem_dma_done` | RO | bit 0 |
| +0x5C, +0x60, +0x64 | `loop`, `offset`, `error` | | `error`: base or length not word-aligned (cleared by disabling) |
| **Memory → block DMA** (writes) | | | |
| +0x68..+0x84 | `mem2block_dma_*` | | as above |
| **Events** | | | |
| +0x88/+0x8C/+0x90 | `ev_status/pending/enable` | | bit 0 card detect (pulse), 1 block2mem DMA done (pulse), 2 mem2block DMA done (pulse), 3 data done (level), 4 command done (level) |

#### 6.6.1 Commands and transfers

A command:
1. write the argument;
2. write `(index << 8) | (data << 5) | crc_check << 2 | response_type`;
3. write 1 to `cmd_send`;
4. wait for `cmd_event` bit 0, polling or by the `cmd_done` event;
5. check bits 2 (time-out) and 3 (CRC).

A data transfer:
1. **before the command:** set `block_length`, `block_count` and the DMA
   (`enable = 0`, `base`, `length`, `enable = 1`);
2. after the command, wait for `data_event` bit 0 and check it;
3. then wait for the DMA's `done`.

For writes, `phy_dataw_status` holds the card's answer. LiteX's BIOS:

```c
/* litex/soc/software/liblitesdcard/sdcard.c (LiteX, BSD; Copyright Florent Kermarrec,
   Gabriel L. Somlo, Kees Jongenburger, bunnie, Antmicro) */
static inline int sdcard_send_command(uint32_t arg, uint8_t cmd, uint8_t rsp) {
	sdcard_core_cmd_argument_write(arg);
	sdcard_core_cmd_command_write((cmd << 8) | rsp);
	sdcard_core_cmd_send_write(1);
	return sdcard_wait_cmd_done();      /* polls cmd_event: bit 0 done, 0x4 timeout, 0x8 CRC */
}

int sdcard_read_single_block(unsigned int blockaddr) {
	sdcard_core_block_length_write(512);
	sdcard_core_block_count_write(1);
	if (sdcard_send_command_retry(blockaddr, 17,
	    (SDCARD_CTRL_DATA_TRANSFER_READ << 5) |
	    SDCARD_CTRL_RESPONSE_SHORT | SDCARD_CTRL_RESPONSE_CRC) != SD_OK)
		return SD_TIMEOUT;
	return sdcard_wait_data_done();
}
/* in sdcard_read(), around it: */
		sdcard_block2mem_dma_enable_write(0);
		sdcard_block2mem_dma_base_write((uint64_t)(uintptr_t) buf);
		sdcard_block2mem_dma_length_write(512*nblocks);
		sdcard_block2mem_dma_enable_write(1);
		/* ... the command ... then wait for sdcard_block2mem_dma_done_read() & 1 */
```

- **Multi-block transfers:** CMD18 or CMD25 with `block_count` n, then CMD12
  (short with busy), unless CMD23 was used. Single blocks are CMD17 and
  CMD24.
- **Addressing:**
  - SDHC/SDXC cards (CCS = 1 in the OCR) take block numbers;
  - standard-capacity cards take byte addresses.

#### 6.6.2 Initialisation

The sequence the BIOS uses (`sdcard_init`, BSD), and which the hardware
accepts:
1. Set the PHY to 1-bit, the clock to 400 kHz, then `init_initialize = 1`.
2. CMD0, CMD8 (`0x1AA`).
3. At the working clock (25 MHz: divider 4), repeat CMD55 + ACMD41 (HCS)
   until the OCR's busy bit is set.
4. CMD2, CMD3 (the RCA is the response's upper 16 bits), CMD9, CMD7.
5. Optionally CMD6 for high speed: SDR25, then 50 MHz, divider 2.
6. ACMD51 (SCR, 8 bytes by DMA): if 4-bit is supported, ACMD6 (4-bit), then
   `phy_settings = 1`.
7. CMD16 (512).

An sdmmc framework does the same through its host callbacks. The host
controller can offer 1-bit and 4-bit, since the PHY switches between them:
- **set `phy_settings` to match every bus-width change the card is given**;
- set `phy_clocker_divider` on every clock change;
- write `phy_init_initialize` before the first CMD0.

#### 6.6.3 Pitfalls found on this platform

[observed]

- **Set the PHY's data width explicitly; never assume it.**
  - The BIOS's SCR decode byte-swapped the SCR on a big-endian CPU, decided
    the card had no 4-bit mode, and left the PHY at 1-bit. `patches/litex`
    0002 fixes the BIOS.
  - A kernel driver that put the card in 4-bit mode but left the PHY alone
    then failed its first data transfer, reading the SCR, with a data CRC
    error, every time.
  - litexboot now resets the PHY to 4-bit before jumping, but a driver should
    not depend on that either.
- **The SCR, and any other data the DMA brings in, arrive in the card's byte
  order**, most significant byte first, at increasing addresses: on this
  CPU, the natural order of a `u32` load.
- **DMA and caches.**
  - The DMAs master the SoC's bus and reach main RAM through LiteDRAM's
    Wishbone port. The CPU reaches it through a port of its own.
  - **The L2 cache must be off** (`--l2-size 0`, the Wukong target's default
    with `--with-sdcard`); otherwise DMA and CPU see different data.
  - **After a block→memory DMA, clear the data cache** (CACR CD), or reach
    the buffer through an uncached mapping, before reading it.
  - **Before a memory→block DMA**, nothing is needed: the data cache is
    write-through. The CPU's posted writes are carried out in program order,
    ahead of the CSR write that starts the DMA.
  - Buffers: word-aligned base and length (`dma_error` otherwise), physical
    addresses.
- **Bus time-out.**
  - [observed] With the L2 cache off, a DMA reader can hold the shared bus
    for 16 DDR3 reads.
  - A CPU access to a CSR waits that long, which exceeded the bridge's first
    bus time-out (1024 clocks) and gave a bus error on a plain CSR write.
  - `BUS_TIMEOUT` is now 65536; use a bitstream built after that change.
- **Register maps change between LiteSDCard versions.**
  - This one has `cmdr_timeout`, `dataw_status`, `datar_timeout` and
    `settings` in the PHY, and five events.
  - Older versions had fewer, at other offsets, and four events.
  - Take offsets and event bits from the SoC's `csr.h`/`csr.json`, or from
    the device tree's `reg` ranges plus the generated header.
- **Rates measured (Linux, 4-bit at 25 MHz):** raw sequential read about
  440 KB/s, limited by the CPU. The BIOS loads 4 MB from the card in 11 s.

### 6.7 LiteEth: Ethernet

[LiteX/LiteEth: `liteeth/mac/sram.py`, `liteeth/mac/core.py`,
`libliteeth/udp.c`, `libliteeth/mdio.c`]

A MAC with packet buffers in on-chip SRAM. There is no DMA: the CPU copies
frames in and out, through an uncached window. The Wukong's PHY is an
RTL8211E on GMII (`LiteEthPHYGMII`): **1000 Mb/s only**, so the link partner
must do 1000BASE-T.

#### 6.7.1 Buffers

The `ethmac` memory region (`0x8000_0000` here, uncached I/O):
- `rx_slots` receive slots, then `tx_slots` transmit slots, each `slot_size`
  bytes (2, 2 and 2048 here; constants `ethmac_rx_slots`, `ethmac_tx_slots`,
  `ethmac_slot_size`);
- receive slot n is at `base + n·slot_size`; transmit slot n at
  `base + (rx_slots + n)·slot_size`;
- the bytes are in wire order at increasing addresses (`endianness = "big"`,
  the CPU's);
- byte, word and long accesses all work.

**The hardware adds the preamble and FCS on transmit, pads short frames to
the minimum, and checks and strips the FCS on receive.** `preamble_crc` reads
1 when it does. So frames are given to and taken from the slots as the
destination address through to the end of the payload, with no FCS.

#### 6.7.2 Registers

Offsets from the `ethmac` CSR base:

| Offset | Name | Access | Meaning |
|---|---|---|---|
| +0x00 | `sram_writer_slot` | RO | receive: the slot of the oldest received frame |
| +0x04 | `sram_writer_length` | RO | its length in bytes |
| +0x08 | `sram_writer_errors` | RO | count of frames dropped (no free slot, or errors) |
| +0x0C/+0x10/+0x14 | `sram_writer_ev_status/pending/enable` | | bit 0 `available`: a received frame is waiting (**level**; writing 1 to pending releases the oldest frame's slot) |
| +0x18 | `sram_reader_start` | W | write 1: send the frame in `slot`, `length` bytes |
| +0x1C | `sram_reader_ready` | RO | 1: the transmit command FIFO has room |
| +0x20 | `sram_reader_level` | RO | commands queued |
| +0x24 | `sram_reader_slot` | RW | transmit slot to send |
| +0x28 | `sram_reader_length` | RW | its length |
| +0x2C/+0x30/+0x34 | `sram_reader_ev_status/pending/enable` | | bit 0: a frame has been sent (pulse; write 1 to clear) |
| +0x38 | `preamble_crc` | RO | 1: the MAC handles preamble and FCS |
| +0x3C | `rx_datapath_preamble_errors` | RO | counter |
| +0x40 | `rx_datapath_crc_errors` | RO | counter |

#### 6.7.3 Driver requirements

- **Receive** (interrupt on `available`):
  1. while `writer_ev_pending` bit 0 is set: read `slot` and `length`;
  2. copy `length` bytes out of receive slot `slot` (uncached, so 32-bit
     reads are best);
  3. **then** write 1 to `writer_ev_pending`, which frees the slot and
     presents the next frame.

  Frames that arrive while every slot is full are dropped and counted.
- **Transmit:**
  1. if `reader_ready` is 0, stop the queue (and resume on the `done` event);
  2. copy the frame into the next transmit slot, round-robin;
  3. write `reader_slot`, `reader_length` and `reader_start = 1`.

  The slot may be reused once its `done` event has fired, or once fewer
  commands are queued than there are slots.
- **MAC address:** the hardware has none and does no address filtering
  beyond what the OS does.
  - Use the device tree's `local-mac-address` when it has one (from LiteX's
    `macaddr` constants);
  - otherwise configure one, or make a random locally administered one.
- **Link:** there is no link-state register in the MAC. Read the PHY over
  MDIO (6.7.4) for the link, or report the link as always up.
- **PHY reset:** `ethphy_crg_reset` (+0x0 of `ethphy`): write 1, wait, write
  0, as LiteX's `eth_init` does with 200 ms waits.
- LiteX's BSD driver, `libliteeth/udp.c`, shows the whole cycle:

```c
/* litex/soc/software/libliteeth/udp.c (LiteX, BSD; Copyright Werner Almesberger,
   Sebastien Bourdeauducq, Florent Kermarrec, Jean-François Nguyen, Robert Jordens) */
	if(ethmac_sram_writer_ev_pending_read() & ETHMAC_EV_SRAM_WRITER) {
		rxslot = ethmac_sram_writer_slot_read();
		rxbuffer = (ethernet_buffer *)(ETHMAC_BASE + ETHMAC_SLOT_SIZE * rxslot);
		rxlen = ethmac_sram_writer_length_read();
		process_frame();
		ethmac_sram_writer_ev_pending_write(ETHMAC_EV_SRAM_WRITER);
	}
/* sending: */
	while (!ethmac_sram_reader_ready_read()) ;   /* with a time-out */
	ethmac_sram_reader_slot_write(txslot);
	ethmac_sram_reader_length_write(txlen);
	ethmac_sram_reader_start_write(1);
	txslot = (txslot+1)%ETHMAC_TX_SLOTS;
	txbuffer = (ethernet_buffer *)(ETHMAC_BASE + ETHMAC_SLOT_SIZE * (ETHMAC_RX_SLOTS + txslot));
```

- **Measured (Linux, the CPU copying):** a 4 MiB HTTP download at about
  110 KB/s; DHCP and ping work.

#### 6.7.4 MDIO

`ethphy_mdio_w` (+0x4): bit 0 MDC, bit 1 output enable, bit 2 data out.
`ethphy_mdio_r` (+0x8): bit 0 data in. It is bit-banged IEEE 802.3 clause 22:
32 preamble ones, start `01`, op `10` read or `01` write, PHY address, register,
turnaround, 16 data bits. LiteX's `libliteeth/mdio.c` (BSD) implements
`mdio_read`/`mdio_write`.

### 6.8 LEDs

`leds_out` (+0x0 of `leds`): one bit per LED. Optional.

---

## 7. The device tree (optional)

[LiteX: `litex_json2dts_linux` with `patches/litex` 0001; this project's
binding]

`images.py --dtc` makes `soc.dtb` from the SoC's `csr.json` with LiteX's
generator, and litexboot passes it as `BI_LITEX_DTB`. For a BSD port it is
optional: everything in it is also in the generated headers, and the bootinfo
covers the machine's core.

- **What it has:**
  - `/` compatible `"litex,<platform>", "litex,soc"`;
  - a `fixed-clock` for sys_clk;
  - `/memory`;
  - `/soc`, a `simple-bus` whose `interrupt-parent` is `intc0`.
- **`/soc` nodes:**
  - `intc0`, `"litex,m68k-intc"`, below;
  - `"litex,soc-controller"`;
  - `"litex,liteuart"`;
  - `"litex,liteeth"` with `reg` = mac, mdio and buffer ranges, and
    `litex,rx-slots`, `litex,tx-slots`, `litex,slot-size`;
  - `"litex,mmc"` with five `reg` ranges (phy, core, block2mem, mem2block,
    irq), `clocks` and `vmmc-supply`;
  - `"litex,gpio"` for the LEDs.
- **`/chosen` is not meant for this platform.** litexboot generates it with
  no initrd, and the memory, command line and initrd come from the bootinfo.
- **The binding `litex,m68k-intc`** (written by this project):
  - `reg` = the CPU's pending (+0) and mask (+4) CSRs, 8 bytes;
  - `interrupt-controller`;
  - `#interrupt-cells = <1>`, the cell being the line, 0-31;
  - the lines are levels, and all are delivered on one autovectored level.
- **Interrupt specifiers** are line numbers: a node's `interrupts = <n>` is
  bit n of `irq_pending`.

---

## 8. Bring-up order and tests

1. **Early console** (6.3, polled), from locore, before the MMU.
2. **Bootinfo parsing; main RAM** from `BI_MEMCHUNK`; the MMU on, with the
   CSR window mapped cache-inhibited (a transparent translation register, or page
   tables: `TT1 = 0xF00F8543` maps 0xF000_0000-0xFFFF_FFFF transparently,
   cache-inhibited, supervisor only: base `F0`, mask `0F`, E, CI, RWM, FC
   base 4, FC mask 3; UM Figure 9-37). Print the bootinfo.
3. **The interrupt controller and timer0**: the clock ticking, and
   `uptime_cycles` as the timecounter.
4. **LiteUART as a tty, interrupt-driven**, its line unmasked only after its
   events are set.
5. **The FPU**: context switches with two FPU-heavy processes preempting each
   other. This is the test that found RD68884's FSAVE freeze; this project's
   `sim/litex/fpswitch/` is a bare-metal reduction of it (BSD).
6. **A root filesystem**: an initramfs or memory disk first (`BI_RAMDISK`),
   then the SD card (6.6), then the network (6.7).

The board: `doc/board.md` (where the bitstream is, `scripts/board.py` to load
it and boot a kernel, interactively or unattended).

Test harness (this project, BSD):
- `sim/litex/l7_test.py` boots images in litex_sim or on the board, logs in
  and runs a command set. It can be pointed at any kernel and root image that
  litexboot loads.
- `targets/qmtech_wukong.py --load` loads a bitstream over JTAG.
- litex_sim takes about an hour to boot a kernel. The board takes about
  4 minutes to upload by serial, or 11 s from the SD card.

Bitstreams to use:

| What | Command (see `doc/l10-devicetree.md`) |
|---|---|
| FPU, Ethernet, SD | `targets/qmtech_wukong.py --cpu-variant standard+fpu --uart-baudrate 1000000 --with-ethernet --with-sdcard ...` |
| FPU, serial only | `... --cpu-variant standard+fpu --uart-baudrate 1000000` |
| no FPU | `... --cpu-variant standard --uart-baudrate 1000000` |

## 9. Sources

- LiteX (BSD-2-Clause): `litex/soc/cores/{timer,uart}.py`,
  `litex/soc/integration/soc.py` (`SoCController`, `add_sdcard`,
  `add_ethernet`), `litex/soc/software/{libbase,liblitesdcard,libliteeth}/`,
  `litex/tools/litex_json2dts_linux.py`; LiteEth `liteeth/mac/`; LiteSDCard
  `litesdcard/`. With `patches/litex`.
- This project (BSD-2-Clause): `litex_68k/cpu/rd68031/core.py`,
  `litex_68k/m68030_fifo.py`, `sw/litexboot/`, `sim/litex/`, and the
  documents `doc/l2-litex.md` to `doc/l10-devicetree.md`. They are prose
  about the Linux port as well as the hardware; read them for behaviour,
  not for kernel code.
- Motorola: MC68030 user's manual and MC68881/MC68882 user's manual
  (`Inputs/doc/`).
- Not to be read for a BSD port: `patches/linux`, the Linux kernel, QEMU, the
  `linux-on-litex-vexriscv` kernel patches, and RD68031's reference models
  (`Inputs/RD68031/Inputs/ref/`), all GPL or otherwise restricted.
