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
"""

import os

from migen import *

from litex.gen import *

from litex import get_data_mod
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from litex.soc.cores.cpu import CPU

from litex_68k.m68030_bus import m68030_bus_layout, M68030ToWishbone

# Variants -----------------------------------------------------------------------------------------

# standard: both caches, 64 entries each (RD68031's default); minimal: no caches.
CPU_VARIANTS = {
    "standard" : dict(icache=64, dcache=64),
    "minimal"  : dict(icache=0,  dcache=0),
}

# The one interrupt level every LiteX interrupt line raises (irq.h knows it too).
IRQ_LEVEL = 2

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

        # The MC68030 bus, and the bridge to Wishbone.
        m68k = Record(m68030_bus_layout)
        self.bridge = M68030ToWishbone(m68k, self.bus, io_regions=self.io_regions)

        # Interrupts: every unmasked line raises IRQ_LEVEL.
        self.irq_pending = CSRStatus(32, description="Interrupt lines, as they are.")
        self.irq_mask    = CSRStorage(32, description="Interrupt lines that may interrupt the CPU.")
        ipl = Signal(3)
        self.comb += [
            self.irq_pending.status.eq(self.interrupt),
            ipl.eq(Mux((self.interrupt & self.irq_mask.storage) != 0, IRQ_LEVEL, 0)),
        ]

        reset_n_oe = Signal()
        self.cpu_params = dict(
            p_ICACHE_ENTRIES          = CPU_VARIANTS[variant]["icache"],
            p_DCACHE_ENTRIES          = CPU_VARIANTS[variant]["dcache"],
            p_COPROCESSOR             = 0,
            # Inside the FPGA nothing else drives the address bus.
            p_ADDR_HIZ_BETWEEN_CYCLES = 0,

            # Clk / Rst.
            i_clk        = ClockSignal("sys"),
            i_rst_n      = ~(ResetSignal("sys") | self.reset),

            # Bus. The output enables are of no use inside the FPGA.
            o_fc_o       = m68k.fc,       o_fc_oe    = Open(),
            o_a_o        = m68k.a,        o_a_oe     = Open(),
            i_d_i        = m68k.d_r,
            o_d_o        = m68k.d_w,      o_d_oe     = Open(),
            o_siz_o      = m68k.siz,      o_siz_oe   = Open(),
            o_ecs_n_o    = Open(),
            o_ocs_n_o    = Open(),
            o_rw_o       = m68k.rw,       o_rw_oe    = Open(),
            o_rmc_n_o    = m68k.rmc_n,    o_rmc_oe   = Open(),
            o_as_n_o     = m68k.as_n,     o_as_oe    = Open(),
            o_ds_n_o     = m68k.ds_n,     o_ds_oe    = Open(),
            o_dben_n_o   = Open(),        o_dben_oe  = Open(),
            i_dsack_n_i  = m68k.dsack_n,
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
            i_reset_n_i  = ~reset_n_oe,
            o_reset_n_o  = Open(),
            o_reset_n_oe = reset_n_oe,

            # Emulator support: caches and MMU enabled.
            i_cdis_n_i   = 1,
            i_mmudis_n_i = 1,
            o_refill_n_o = Open(),
            o_status_n_o = Open(),
        )

        # Add Verilog sources.
        self.add_sources(platform)

    def set_reset_address(self, reset_address):
        # The MC68030 always reads its reset vectors at 0 (UM 8.1.1).
        assert reset_address == 0, "RD68031 resets from address 0: put the ROM there."
        self.reset_address = reset_address

    @staticmethod
    def add_sources(platform):
        rd68031 = get_data_mod("cpu", "rd68031")
        # The Verilator waivers first, then the RTL in compile order (packages first).
        platform.add_source(os.path.join(rd68031.data_location, rd68031.verilator_config))
        for f in rd68031.sources:
            platform.add_source(os.path.join(rd68031.data_location, f), "systemverilog")

    def do_finalize(self):
        self.specials += Instance("rd68031_top", **self.cpu_params)
