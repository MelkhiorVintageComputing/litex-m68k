# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""RD68031, an MC68030 soft-core, as a LiteX CPU.

The core (pythondata-cpu-rd68031) keeps the MC68030's own bus; M68030ToWishbone
(litex_68k/m68030_bus.py) turns it into the one Wishbone master LiteX sees.

Interrupts: LiteX's interrupt lines are level, active high, one per source. The
MC68030 has an interrupt priority level instead (UM 5.8.1). Here every source
shares one level, IRQ_LEVEL, autovectored (vector 24 + IRQ_LEVEL), and two CSRs
stand where other CPUs have internal registers:

    cpu_irq_pending  (read)   the interrupt lines as they are
    cpu_irq_mask     (r/w)    which of them may raise IRQ_LEVEL

so irq_getmask/irq_setmask/irq_pending in irq.h are CSR accesses, and
irq_getie/irq_setie move the status register's interrupt mask.

Clocking: the core and its bridge run in `RD68031.clock_domain`, "sys" unless a
target says otherwise. RD68031 makes about 30 MHz on an Artix-7, and LiteDRAM's
Artix-7 PHY wants sys at 75 MHz or more: a board target with DDR3 sets
clock_domain = "cpu" and provides that clock domain (50 % duty cycle: the core
uses both edges). The bus then crosses to sys, by `RD68031.bus_bridge`:

- "fifo" (the default): M68030ToFIFOToSoC (litex_68k/m68030_fifo.py), requests
  and answers through asynchronous FIFOs, so the CPU's clock may be any clock.
  Cache line fills are bursts, and with a LiteDRAM controller 128 bits wide
  (add_memory_buses), main RAM is read a line at a time on a native port of
  its own. Writes outside io_regions are posted.
- "sync": M68030ToWishbone, a Wishbone cycle per bus cycle, and, if the CPU has
  a clock domain of its own, WishboneSyncCrossing, which needs the CPU's clock
  to be sys's divided by an integer, edges aligned (two outputs of one MMCM).

An access nobody answers is a bus error after BUS_TIMEOUT clocks: the bridge's
own time-out with "sync", WishboneErrorTimeout on the sys side with "fifo".
LiteX's own bus time-out does not do: it answers with all ones and an ack, not
an error.
"""

import os

from migen import *

from litex.gen import *

from migen.genlib.cdc import MultiReg

from litex import get_data_mod
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from litex.soc.cores.cpu import CPU

from litex_68k.m68030_bus import m68030_bus_layout, m68030_coprocessor_cycle, M68030ToWishbone
from litex_68k.m68030_fifo import M68030ToFIFOToSoC

# Variants -----------------------------------------------------------------------------------------

# standard: both caches, 64 entries each (RD68031's default); minimal: no caches.
# +fpu: and RD68884, an MC68881, as coprocessor FPU_CPID.
CPU_VARIANTS = {}
for _name, _caches in [("standard", 64), ("minimal", 0)]:
    for _fpu in (False, True):
        CPU_VARIANTS[_name + ("+fpu" if _fpu else "")] = dict(icache=_caches, dcache=_caches, fpu=_fpu)

# The FPU's coprocessor ID: 1, as Motorola's software expects it (UM 10.1).
FPU_CPID = 1

# The one interrupt level every LiteX interrupt line raises (irq.h knows it too).
IRQ_LEVEL = 2

# Clocks (of the bus's domain) before an access nobody answers is a bus error.
# They include the wait for the bus: with the "fifo" bridge, the time-out is
# before the SoC's arbiter, and a DMA master keeps the bus for as many reads
# as its FIFO takes (LiteSDCard's, 16, each a DDR3 access with no L2 cache:
# 1024 clocks were not enough, L10).
BUS_TIMEOUT = 65536

# Wishbone crossing between synchronous clocks ----------------------------------------------------

class WishboneSyncCrossing(LiteXModule):
    """A Wishbone master in clock domain `cd`, whose clock is sys's divided by an
    integer and aligned with it (two outputs of one MMCM), to a slave in sys.

    Vivado times the paths between such clocks like any other, so the request
    goes from the master to the slave as it is, with no synchroniser: the
    access starts on the first sys edge after the master's, and its answer,
    kept in sys registers, is there for the master's next edge.

    Each access is made once. A toggle, kept in the master's domain, changes on
    the first clock of each request: of a cycle, or right after an answer when
    the master goes on with cyc and stb high. The sys side makes an access when
    the toggle differs from the last one it answered, and answers until it
    changes again. A master dropping cyc before its answer abandons the access
    on the slave too, and leaves nothing pending."""
    def __init__(self, master, slave, cd):
        want   = Signal()   # The master's request (cd).
        want_q = Signal()   # cd: want, one master clock ago.
        ack_q  = Signal()   # cd: the master was answered on its last edge.
        req_q  = Signal()   # cd: the toggle, as of the master's last edge.
        req    = Signal()   # The toggle: req_q, changed on a request's first clock.
        served = Signal()   # sys: the toggle the answer below is for.
        err    = Signal()
        dat_r  = Signal(len(master.dat_r))
        answered = Signal()
        self.comb += [
            want.eq(master.cyc & master.stb),
            req.eq(req_q ^ (want & (~want_q | ack_q))),
            answered.eq(want & (req == served)),
            master.connect(slave, omit={"cyc", "stb", "ack", "err", "dat_r"}),
            slave.cyc.eq(want & ~answered),
            slave.stb.eq(want & ~answered),
            master.ack.eq(answered & ~err),
            master.err.eq(answered &  err),
            master.dat_r.eq(dat_r),
        ]
        getattr(self.sync, cd).__iadd__([
            want_q.eq(want),
            ack_q.eq(master.ack | master.err),
            req_q.eq(req),
        ])
        self.sync += [
            If(~want,
                served.eq(req),     # Nothing asked: nothing pending either.
            ).Elif(slave.cyc & slave.stb & (slave.ack | slave.err),
                served.eq(req),
                err.eq(slave.err),
                dat_r.eq(slave.dat_r),
            ),
        ]

# Wishbone time-out as an error -------------------------------------------------------------------

class WishboneErrorTimeout(LiteXModule):
    """master to slave, unchanged, except that an access still unanswered after
    `cycles` clocks ends with err to the master, and cyc and stb dropped to the
    slave (a Wishbone master may abandon a cycle)."""
    def __init__(self, master, slave, cycles):
        count     = Signal(max=cycles + 1)
        timed_out = Signal()
        active    = Signal()
        self.comb += [
            master.connect(slave, omit={"cyc", "stb"}),
            active.eq(master.cyc & master.stb),
            timed_out.eq(count == cycles - 1),
            slave.cyc.eq(master.cyc & ~timed_out),
            slave.stb.eq(master.stb & ~timed_out),
            If(timed_out,
                master.ack.eq(0),
                master.err.eq(1),
            ),
        ]
        self.sync += If(active & ~slave.ack & ~slave.err & ~timed_out,
            count.eq(count + 1)
        ).Else(
            count.eq(0)
        )

# The reset request in the CPU's clock domain ------------------------------------------------------

def cpu_domain_reset(module, request, cd):
    """The reset request `request` (sys) as clock domain `cd` sees it, added to
    `module`. A request may be one sys clock long (ctrl's soc_rst), which a
    slower CPU clock would miss: its start crosses as a toggle, and holds the
    CPU in reset for RESET_CLOCKS of its own clocks; a longer request (cpu_rst)
    crosses as it is too."""
    reset = Signal()
    if cd == "sys":
        module.comb += reset.eq(request)
        return reset
    RESET_CLOCKS = 8
    reset_q  = Signal()
    toggle   = Signal(reset_less=True)
    level    = Signal()
    seen     = Signal()
    seen_q   = Signal()
    count    = Signal(max=RESET_CLOCKS + 1)
    module.sync += [
        reset_q.eq(request),
        If(request & ~reset_q, toggle.eq(~toggle)),
    ]
    module.specials += [
        MultiReg(request, level, cd),
        MultiReg(toggle,  seen,  cd),
    ]
    getattr(module.sync, cd).__iadd__([
        seen_q.eq(seen),
        If(seen != seen_q,
            count.eq(RESET_CLOCKS)
        ).Elif(count != 0,
            count.eq(count - 1)
        ),
    ])
    module.comb += reset.eq(level | (count != 0))
    return reset

# RD68031 ------------------------------------------------------------------------------------------

class RD68031(CPU):
    category             = "softcore"
    family               = "m68k"
    name                 = "rd68031"
    human_name           = "RD68031"
    variants             = list(CPU_VARIANTS.keys())
    data_width           = 32
    endianness           = "big"
    gcc_triple           = ("m68k-elf", "m68k-unknown-elf", "m68k-linux-gnu", "m68k-linux")
    linker_output_format = "elf32-m68k"
    nop                  = "nop"
    io_regions           = {0x8000_0000: 0x8000_0000} # Origin, Length: uncached (CIIN).
    clock_domain         = "sys"                      # See the module's docstring.
    bus_bridge           = "fifo"                     # Or "sync" (idem).

    # Memory Mapping: the reset vectors are read at 0 (UM 8.1.1), so the ROM is there.
    @property
    def mem_map(self):
        return {
            "rom"      : 0x0000_0000,
            "sram"     : 0x0100_0000,
            "main_ram" : 0x4000_0000,
            "csr"      : 0xf000_0000,
        }

    # GCC Flags.
    @property
    def gcc_flags(self):
        flags =  "-mcpu=68030 "
        flags += "-D__rd68031__ "
        # m68k-linux-gnu-gcc's default is -m68881: C doubles are FPU
        # instructions. Without an FPU, software floating point, from LiteX's
        # own compiler-rt (soft-float, built with these flags; this
        # toolchain's libgcc is not: its conversions use the FPU, and return in
        # fp0). Every program built against the SoC follows these flags.
        if not CPU_VARIANTS[self.variant]["fpu"]:
            flags += "-msoft-float "
        return flags

    def __init__(self, platform, variant="standard"):
        self.platform     = platform
        self.variant      = variant
        self.reset        = Signal()
        self.interrupt    = Signal(32)
        self.bus          = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.periph_buses = [self.bus] # Peripheral buses (Connected to main SoC's bus).
        self.memory_buses = []         # Memory buses (Connected directly to LiteDRAM).

        # # #

        # The MC68030 bus, and the bridge to Wishbone, in the CPU's clock domain.
        cd   = self.clock_domain
        fpu  = CPU_VARIANTS[variant]["fpu"]
        copr = (FPU_CPID,) if fpu else ()
        m68k = Record(m68030_bus_layout)
        if self.bus_bridge == "fifo":
            sys_bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
            self.bridge  = M68030ToFIFOToSoC(m68k, sys_bus, cd=cd, io_regions=self.io_regions,
                coprocessors=copr)
            self.timeout = WishboneErrorTimeout(sys_bus, self.bus, BUS_TIMEOUT)
        elif cd == "sys":
            self.bridge = M68030ToWishbone(m68k, self.bus, io_regions=self.io_regions,
                coprocessors=copr)
        else:
            cpu_bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
            self.bridge = ClockDomainsRenamer(cd)(
                M68030ToWishbone(m68k, cpu_bus, io_regions=self.io_regions, coprocessors=copr))
            self.cdc = WishboneSyncCrossing(cpu_bus, self.bus, cd)

        # Interrupts: every unmasked line raises IRQ_LEVEL.
        self.irq_pending = CSRStatus(32, description="Interrupt lines, as they are.")
        self.irq_mask    = CSRStorage(32, description="Interrupt lines that may interrupt the CPU.")
        # IPL is asynchronous to the core, as on the chip (UM 5.8.1): it
        # synchronises it itself, so it may come from sys, from a register so
        # that it does not glitch.
        ipl = Signal(3)
        self.comb += self.irq_pending.status.eq(self.interrupt)
        self.sync += ipl.eq(Mux((self.interrupt & self.irq_mask.storage) != 0, IRQ_LEVEL, 0))

        reset = cpu_domain_reset(self, self.reset, cd)

        # The bus as the core sees it: the bridge's, and with the FPU, the FPU's
        # data lanes when it drives them and its DSACK, wired-OR (active low).
        core_d     = Signal(32)
        core_dsack = Signal(2)
        reset_n_o  = Signal()
        reset_n_oe = Signal()
        if fpu:
            self.fpu_d     = fpu_d     = Signal(32)
            self.fpu_d_oe  = fpu_d_oe  = Signal(4)
            fpu_dsack_n    = Signal(2)
            fpu_dsack_oe   = Signal()
            self.comb += [
                core_d.eq(Cat(*[Mux(fpu_d_oe[l], fpu_d[8*l:8*(l + 1)], m68k.d_r[8*l:8*(l + 1)])
                                for l in range(4)])),
                core_dsack.eq(m68k.dsack_n & Mux(fpu_dsack_oe, fpu_dsack_n, 0b11)),
            ]
            # RD68884 on the CPU's own bus and clock (BUS_SYNC: its same-clock
            # bus unit), a 32-bit port (SIZE and A0 strapped, FPU table 9-2),
            # selected by CpID FPU_CPID's CPU space cycles (UM 10.1). RESET
            # reaches it from the RESET instruction too, as on a board.
            self.fpu_params = dict(
                p_BUS_SYNC      = 1,
                p_BUS_SYNC_WAIT = 0,
                p_MODEL         = 68881,
                i_clk           = ClockSignal(cd),
                i_rst_n         = ~(ResetSignal(cd) | reset),
                i_reset_n_i     = ~(reset_n_oe & ~reset_n_o),
                i_cs_n_i        = ~m68030_coprocessor_cycle(m68k, copr),
                i_as_n_i        = m68k.as_n,
                i_ds_n_i        = m68k.ds_n,
                i_rw_i          = m68k.rw,
                i_size_n_i      = 1,
                i_a_i           = Cat(Constant(1, 1), m68k.a[1:5]),
                i_d_i           = m68k.d_w,
                o_d_o           = fpu_d,
                o_d_oe          = fpu_d_oe,
                o_dsack_n_o     = fpu_dsack_n,
                o_dsack_oe      = fpu_dsack_oe,
            )
        else:
            self.comb += [
                core_d.eq(m68k.d_r),
                core_dsack.eq(m68k.dsack_n),
            ]
        self.cpu_params = dict(
            p_ICACHE_ENTRIES          = CPU_VARIANTS[variant]["icache"],
            p_DCACHE_ENTRIES          = CPU_VARIANTS[variant]["dcache"],
            p_COPROCESSOR             = int(fpu),
            # Inside the FPGA nothing else drives the address bus.
            p_ADDR_HIZ_BETWEEN_CYCLES = 0,

            # Clk / Rst.
            # rst_n is an asynchronous reset in the core: what releases it
            # comes from the core's own clock domain.
            i_clk        = ClockSignal(cd),
            i_rst_n      = ~(ResetSignal(cd) | reset),

            # Bus. The output enables are of no use inside the FPGA.
            o_fc_o       = m68k.fc,       o_fc_oe    = Open(),
            o_a_o        = m68k.a,        o_a_oe     = Open(),
            i_d_i        = core_d,
            o_d_o        = m68k.d_w,      o_d_oe     = Open(),
            o_siz_o      = m68k.siz,      o_siz_oe   = Open(),
            o_ecs_n_o    = Open(),
            o_ocs_n_o    = Open(),
            o_rw_o       = m68k.rw,       o_rw_oe    = Open(),
            o_rmc_n_o    = m68k.rmc_n,    o_rmc_oe   = Open(),
            o_as_n_o     = m68k.as_n,     o_as_oe    = Open(),
            o_ds_n_o     = m68k.ds_n,     o_ds_oe    = Open(),
            o_dben_n_o   = Open(),        o_dben_oe  = Open(),
            i_dsack_n_i  = core_dsack,
            i_sterm_n_i  = m68k.sterm_n,
            i_ciin_n_i   = m68k.ciin_n,
            o_ciout_n_o  = m68k.ciout_n,  o_ciout_oe = Open(),
            o_cbreq_n_o  = m68k.cbreq_n,  o_cbreq_oe = Open(),
            i_cback_n_i  = m68k.cback_n,
            i_berr_n_i   = m68k.berr_n,
            i_halt_n_i   = m68k.halt_n,

            # Interrupts.
            i_ipl_n_i    = ~ipl,
            o_ipend_n_o  = Open(),
            i_avec_n_i   = m68k.avec_n,

            # No other bus master.
            i_br_n_i     = 1,
            o_bg_n_o     = Open(),
            i_bgack_n_i  = 1,

            # The RESET instruction's pulse comes back on the wired pin, which the
            # core ignores (RD68031 doc/pinout.md).
            i_reset_n_i  = ~(reset_n_oe & ~reset_n_o),
            o_reset_n_o  = reset_n_o,
            o_reset_n_oe = reset_n_oe,

            # Emulator support: caches and MMU enabled.
            i_cdis_n_i   = 1,
            i_mmudis_n_i = 1,
            o_refill_n_o = Open(),
            o_status_n_o = Open(),
        )

        # Add Verilog sources.
        self.add_sources(platform, fpu=fpu)

    def add_soc_components(self, soc):
        # Main RAM's region, once add_sdram has made it (add_memory_buses).
        self.soc_regions = soc.bus.regions

    def add_memory_buses(self, address_width, data_width):
        """Main RAM on a LiteDRAM native port of the bridge's own, when the
        controller is a line wide (the Wukong's DDR3); otherwise through the
        SoC's bus, as for any other memory."""
        if self.bus_bridge != "fifo" or data_width != 128:
            return
        from litedram.common import LiteDRAMNativePort
        region = self.soc_regions["main_ram"]
        port   = LiteDRAMNativePort(mode="both", address_width=address_width - 4, data_width=data_width)
        self.bridge.add_native_port(port, region.origin, region.size)
        self.memory_buses.append(port)

    def set_reset_address(self, reset_address):
        # The MC68030 always reads its reset vectors at 0 (UM 8.1.1).
        assert reset_address == 0, "RD68031 resets from address 0: put the ROM there."
        self.reset_address = reset_address

    @staticmethod
    def add_sources(platform, fpu=False):
        from litex.build.sim import SimPlatform
        rd68031 = get_data_mod("cpu", "rd68031")
        # The Verilator waivers first (for Verilator only), then the RTL in
        # compile order (packages first).
        configs = [rd68031.verilator_config] + ([rd68031.fpu_verilator_config] if fpu else [])
        sources = rd68031.sources + (rd68031.fpu_sources if fpu else [])
        if isinstance(platform, SimPlatform):
            for f in configs:
                platform.add_source(os.path.join(rd68031.data_location, f))
        for f in sources:
            platform.add_source(os.path.join(rd68031.data_location, f), "systemverilog")

    def do_finalize(self):
        self.specials += Instance("rd68031_top", **self.cpu_params)
        if hasattr(self, "fpu_params"):
            self.specials += Instance("rd68884_top", **self.fpu_params)
