# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""RD68041, an MC68040 (or MC68LC040) soft-core, as a LiteX CPU.

The core (pythondata-cpu-rd68041) keeps the MC68040's own bus; M68040ToFIFOToSoC
(litex_68k/m68040_bus.py) turns it into the one Wishbone master LiteX sees, with
asynchronous FIFOs between the CPU's clock and sys, as RD68031's "fifo" bridge.
Cache lines are bursts both ways (the 040 also writes lines: copyback pushes,
MOVE16), and with a LiteDRAM controller 128 bits wide (add_memory_buses), main
RAM is a line at a time on a native port of its own. Writes outside io_regions
are posted.

Interrupts: as RD68031's (litex_68k/cpu/rd68031/core.py): every LiteX line
raises IRQ_LEVEL, autovectored, through the cpu_irq_pending and cpu_irq_mask
CSRs.

The FPU is in the core: "+fpu" builds the MC68040 (the core's FPU = 1), the
others the MC68LC040 (FPU = 0), whose floating-point instructions all take the
F-line exception; software for it is built with -msoft-float. The caches are
not a build option: CACR enables them (crt0.S, system.h).

Clocking: the core is rising edge only (RD68041 doc/pinout.md); it runs in
`RD68041.clock_domain`, "sys" unless a target says otherwise, and its bridge
crosses to sys. RD68041 makes about 30 MHz on an Artix-7, out of context.

An access nobody answers is a bus error (TEA) after BUS_TIMEOUT clocks, from
WishboneErrorTimeout on the sys side, as RD68031's.
"""

import os

from migen import *

from litex.gen import *

from litex import get_data_mod
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from litex.soc.cores.cpu import CPU

from litex_68k.m68040_bus import m68040_bus_layout, M68040ToFIFOToSoC
from litex_68k.cpu.rd68031.core import IRQ_LEVEL, BUS_TIMEOUT, WishboneErrorTimeout, cpu_domain_reset

# Variants -----------------------------------------------------------------------------------------

# standard: the MC68LC040; +fpu: the MC68040.
CPU_VARIANTS = {
    "standard":     dict(fpu=False),
    "standard+fpu": dict(fpu=True),
}

# RD68041 ------------------------------------------------------------------------------------------

class RD68041(CPU):
    category             = "softcore"
    family               = "m68k"
    name                 = "rd68041"
    human_name           = "RD68041"
    variants             = list(CPU_VARIANTS.keys())
    data_width           = 32
    endianness           = "big"
    gcc_triple           = ("m68k-elf", "m68k-unknown-elf", "m68k-linux-gnu", "m68k-linux")
    linker_output_format = "elf32-m68k"
    nop                  = "nop"
    io_regions           = {0x8000_0000: 0x8000_0000} # Origin, Length: uncached (TCI).
    clock_domain         = "sys"                      # See the module's docstring.

    # Memory Mapping: the reset vectors are read at 0 (UM 8.1), so the ROM is there.
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
        flags =  "-mcpu=68040 "
        flags += "-D__rd68041__ "
        # The MC68LC040 has no FPU: software floating point, from LiteX's own
        # compiler-rt, as for RD68031 without RD68884 (rd68031/core.py).
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

        # The MC68040 bus, and the bridge, from the CPU's clock domain to sys.
        cd   = self.clock_domain
        fpu  = CPU_VARIANTS[variant]["fpu"]
        m68k = Record(m68040_bus_layout)
        sys_bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.bridge  = M68040ToFIFOToSoC(m68k, sys_bus, cd=cd, io_regions=self.io_regions)
        self.timeout = WishboneErrorTimeout(sys_bus, self.bus, BUS_TIMEOUT)

        # Interrupts: every unmasked line raises IRQ_LEVEL. IPL is asynchronous
        # to the core, as on the chip (UM 5.8.1), which synchronises it itself,
        # so it may come from sys, from a register so that it does not glitch.
        self.irq_pending = CSRStatus(32, description="Interrupt lines, as they are.")
        self.irq_mask    = CSRStorage(32, description="Interrupt lines that may interrupt the CPU.")
        ipl = Signal(3)
        self.comb += self.irq_pending.status.eq(self.interrupt)
        self.sync += ipl.eq(Mux((self.interrupt & self.irq_mask.storage) != 0, IRQ_LEVEL, 0))

        reset = cpu_domain_reset(self, self.reset, cd)

        # The processor is the only master: the bus granted to it for good, no
        # snooping (SC = 00, UM Table 4-1), and its bidirectional pins' inputs
        # fed from its own outputs while it drives them, as on a board. The
        # output enables are of no use inside the FPGA.
        ts_n_o, ts_oe, bb_n_o, bb_oe = Signal(), Signal(), Signal(), Signal()
        self.comb += m68k.ts_n.eq(~ts_oe | ts_n_o)
        self.cpu_params = dict(
            p_FPU        = int(fpu),

            # Clk / Rst. rst_n is the core's asynchronous initialisation and
            # RSTI its architectural reset (RD68041 doc/pinout.md); both come
            # from the core's own clock domain. PCLK is not used.
            i_clk        = ClockSignal(cd),
            i_pclk       = 0,
            i_rst_n      = ~(ResetSignal(cd) | reset),
            i_rsti_n_i   = ~(ResetSignal(cd) | reset),
            o_rsto_n_o   = Open(),

            # Bus.
            i_a_i        = m68k.a,        o_a_o      = m68k.a,      o_a_oe     = Open(),
            i_d_i        = m68k.d_r,      o_d_o      = m68k.d_w,    o_d_oe     = Open(),
            i_tt_i       = m68k.tt,       o_tt_o     = m68k.tt,
            o_tm_o       = m68k.tm,
            o_tln_o      = Open(),
            o_upa_o      = Open(),
            i_rw_i       = m68k.rw,       o_rw_o     = m68k.rw,
            i_siz_i      = m68k.siz,      o_siz_o    = m68k.siz,
            o_lock_n_o   = m68k.lock_n,
            o_locke_n_o  = Open(),
            o_ciout_n_o  = m68k.ciout_n,
            o_attr_oe    = Open(),
            i_ts_n_i     = m68k.ts_n,     o_ts_n_o   = ts_n_o,      o_ts_oe    = ts_oe,
            o_tip_n_o    = m68k.tip_n,    o_tip_oe   = Open(),
            i_ta_n_i     = m68k.ta_n,     o_ta_n_o   = Open(),      o_ta_oe    = Open(),
            i_tea_n_i    = m68k.tea_n,
            i_tci_n_i    = m68k.tci_n,
            i_tbi_n_i    = m68k.tbi_n,
            i_dle_i      = 0,

            # No other bus master, no snooping.
            i_sc_i       = 0b00,
            o_mi_n_o     = Open(),
            o_br_n_o     = Open(),
            i_bg_n_i     = 0,
            i_bb_n_i     = ~bb_oe | bb_n_o, o_bb_n_o = bb_n_o,      o_bb_oe    = bb_oe,

            # Caches and MMU enabled.
            i_cdis_n_i   = 1,
            i_mdis_n_i   = 1,

            # Interrupts.
            i_ipl_n_i    = ~ipl,
            o_ipend_n_o  = Open(),
            i_avec_n_i   = m68k.avec_n,

            # Status and test: not built in the core.
            o_pst_o      = Open(),
            i_tck_i      = 0,
            i_tms_i      = 1,
            i_tdi_i      = 1,
            i_trst_n_i   = 0,
            o_tdo_o      = Open(),
            o_tdo_oe     = Open(),
        )

        # Add Verilog sources.
        self.add_sources(platform)

    def add_soc_components(self, soc):
        # Main RAM's region, once add_sdram has made it (add_memory_buses).
        self.soc_regions = soc.bus.regions

    def add_memory_buses(self, address_width, data_width):
        """Main RAM on a LiteDRAM native port of the bridge's own, when the
        controller is a line wide (the Wukong's DDR3); otherwise through the
        SoC's bus, as for any other memory."""
        if data_width != 128:
            return
        from litedram.common import LiteDRAMNativePort
        region = self.soc_regions["main_ram"]
        port   = LiteDRAMNativePort(mode="both", address_width=address_width - 4, data_width=data_width)
        self.bridge.add_native_port(port, region.origin, region.size)
        self.memory_buses.append(port)

    def set_reset_address(self, reset_address):
        # The MC68040 always reads its reset vectors at 0 (UM 8.1).
        assert reset_address == 0, "RD68041 resets from address 0: put the ROM there."
        self.reset_address = reset_address

    @staticmethod
    def add_sources(platform):
        from litex.build.sim import SimPlatform
        rd68041 = get_data_mod("cpu", "rd68041")
        # The Verilator waivers first (for Verilator only), then the RTL in
        # compile order (packages first).
        if isinstance(platform, SimPlatform):
            platform.add_source(os.path.join(rd68041.data_location, rd68041.verilator_config))
        for f in rd68041.sources:
            platform.add_source(os.path.join(rd68041.data_location, f), "systemverilog")

    def do_finalize(self):
        self.specials += Instance("rd68041_top", **self.cpu_params)
        # litex_sim, debugging only: RD68041_TRACE=1 at build time adds
        # sim/litex/trace/rd68041_trace.sv, a trace of user mode on stdout.
        from litex.build.sim import SimPlatform
        if os.environ.get("RD68041_TRACE") and isinstance(self.platform, SimPlatform):
            top = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            self.platform.add_source(os.path.join(top, "sim", "litex", "trace", "rd68041_trace.sv"), "systemverilog")
            self.specials += Instance("rd68041_trace", i_clk=ClockSignal(self.clock_domain))
