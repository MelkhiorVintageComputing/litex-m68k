#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L1 bench: RD68031 + M68030ToWishbone + Wishbone RAM and test devices, no LiteX SoC.

Generates bench.v, the top that tb.sv instantiates. With --bridge fifo, the
bridge is M68030ToFIFOToSoC instead (litex_68k/m68030_fifo.py), the processor
on a clock of its own ("cpu"), unrelated to sys, where everything else is.
With --cpu rd68041, the processor is RD68041 (an MC68040; --fpu 0, the
MC68LC040) behind M68040ToFIFOToSoC (litex_68k/m68040_bus.py), on its own
clock as with --bridge fifo. With --smp too (L19), two RD68041s on one bus
(litex_68k/m68040_smp.py: the arbiter, the MI gate, the board's devices at
$FFFF_0000) in front of that bridge; the bus counters are the top's
stat_<name> outputs. The map:

    $0000_0000  RAM (--ram-size), loaded at run time from the file named by the
                +ram= plusarg (one 32-bit big-endian word per line, $readmemh)
    $E000_0000  a slave that answers every access with a Wishbone error
    $F000_0000  the test device, below
    elsewhere   nothing: the bridge (or, with the FIFOs, WishboneErrorTimeout)
                times out and asserts BERR

$8000_0000-$FFFF_FFFF is the I/O region, as in the LiteX SoC: the bridge asserts
CIIN there (the 040's: TCI), and the FIFO bridges do not post writes there.

The test device, word registers:

    +$00 TTY     write: the low byte goes to the console
    +$04 EXIT    write: end of the run, the value is the exit status (0: pass)
    +$08 RESULT  write: the value is printed (and checked against +expect=)
    +$0C IPL     read/write: the interrupt level presented to the processor
    +$10 COUNT   read: a counter that increments on every read
    +$14 SCRATCH read/write, with byte enables
"""

import argparse
import os

from migen import *

from litex.gen import *
from litex.gen.fhdl.verilog import convert

from litex.soc.interconnect import wishbone

from litex.build.sim.common import sim_special_overrides

from litex_68k.m68030_bus import m68030_bus_layout, M68030ToWishbone
from litex_68k.m68030_fifo import M68030ToFIFOToSoC
from litex_68k.m68040_bus import m68040_bus_layout, M68040ToFIFOToSoC
from litex_68k.m68040_smp import (M68040SMPBus, m68040_pins_layout, rd68041_smp_params, STATS,
    IPI_LEVEL)
from litex_68k.cpu.rd68031.core import WishboneErrorTimeout

# Test device --------------------------------------------------------------------------------------

class BenchDevice(LiteXModule):
    def __init__(self):
        self.bus        = bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.tty_stb    = Signal()
        self.tty_data   = Signal(8)
        self.exit_stb   = Signal()
        self.exit_code  = Signal(32)
        self.result_stb = Signal()
        self.result     = Signal(32)
        self.ipl        = Signal(3)

        # # #

        count   = Signal(32)
        scratch = Signal(32)
        reg     = bus.adr[0:3]
        access  = bus.cyc & bus.stb & ~bus.ack
        write   = access & bus.we
        self.comb += [
            self.tty_data.eq(bus.dat_w[0:8]),
            self.exit_code.eq(bus.dat_w),
            self.result.eq(bus.dat_w),
        ]
        self.sync += [
            bus.ack.eq(access),
            self.tty_stb.eq(0),
            self.exit_stb.eq(0),
            self.result_stb.eq(0),
            If(access,
                Case(reg, {
                    3: bus.dat_r.eq(self.ipl),
                    4: [bus.dat_r.eq(count), If(~bus.we, count.eq(count + 1))],
                    5: bus.dat_r.eq(scratch),
                    "default": bus.dat_r.eq(0),
                }),
            ),
            If(write,
                Case(reg, {
                    0: self.tty_stb.eq(1),
                    1: self.exit_stb.eq(1),
                    2: self.result_stb.eq(1),
                    3: self.ipl.eq(bus.dat_w[0:3]),
                    5: [If(bus.sel[i], scratch[8*i:8*i+8].eq(bus.dat_w[8*i:8*i+8])) for i in range(4)],
                    "default": [],
                }),
            ),
        ]

class ErrorSlave(LiteXModule):
    def __init__(self):
        self.bus = bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.sync += bus.err.eq(bus.cyc & bus.stb & ~bus.err)

# Bench --------------------------------------------------------------------------------------------

class BenchBase(LiteXModule):
    def add_soc(self, master, ram_size):
        """The RAM, the test devices and the decoder, behind `master` (in sys)."""
        self.dev    = dev = BenchDevice()
        self.err    = ErrorSlave()

        # RAM, initialised at run time from +ram= (tb.sv).
        ram = Memory(32, ram_size//4, name="ram")
        self.specials += ram
        self.ram_bus = ram_bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        port = ram.get_port(write_capable=True, we_granularity=8)
        self.specials += port
        self.comb += [
            port.adr.eq(ram_bus.adr),
            port.dat_w.eq(ram_bus.dat_w),
            port.we.eq(Replicate(ram_bus.cyc & ram_bus.stb & ram_bus.we & ~ram_bus.ack, 4) & ram_bus.sel),
            ram_bus.dat_r.eq(port.dat_r),
        ]
        self.sync += ram_bus.ack.eq(ram_bus.cyc & ram_bus.stb & ~ram_bus.ack)

        self.decoder = wishbone.Decoder(master, [
            (lambda a: a[30-4:30] == 0x0, ram_bus),  # $0xxx_xxxx (the RAM repeats)
            (lambda a: a[30-4:30] == 0xe, self.err.bus),
            (lambda a: a[30-4:30] == 0xf, dev.bus),
        ], register=False)

        # Console and end of run, for tb.sv.
        self.tty_stb    = dev.tty_stb
        self.tty_data   = dev.tty_data
        self.exit_stb   = dev.exit_stb
        self.exit_code  = dev.exit_code
        self.result_stb = dev.result_stb
        self.result     = dev.result


class Bench(BenchBase):
    def __init__(self, ram_size=0x40000, icache=64, dcache=64, timeout=256, bridge="wishbone"):
        self.cd_sys = ClockDomain()
        cd = "sys"
        if bridge == "fifo":
            self.cd_cpu = ClockDomain()
            cd = "cpu"

        # # #

        bus = Record(m68030_bus_layout)
        io_regions = {0x8000_0000: 0x8000_0000}  # As LiteX's, for RD68031.
        if bridge == "fifo":
            wb = wishbone.Interface(data_width=32, address_width=32, addressing="word")
            self.bridge  = M68030ToFIFOToSoC(bus, wb, cd=cd, io_regions=io_regions)
            master = wishbone.Interface(data_width=32, address_width=32, addressing="word")
            self.timeout = WishboneErrorTimeout(wb, master, timeout)
        else:
            self.bridge = M68030ToWishbone(bus, io_regions=io_regions, timeout=timeout)
            master = self.bridge.wishbone
        self.add_soc(master, ram_size)

        # The processor.
        reset_n_oe = Signal()
        self.specials += Instance("rd68031_top",
            p_ICACHE_ENTRIES          = icache,
            p_DCACHE_ENTRIES          = dcache,
            p_COPROCESSOR             = 0,
            p_ADDR_HIZ_BETWEEN_CYCLES = 0,
            i_clk        = ClockSignal(cd),
            i_rst_n      = ~ResetSignal(cd),
            o_fc_o       = bus.fc,       o_fc_oe    = Open(),
            o_a_o        = bus.a,        o_a_oe     = Open(),
            i_d_i        = bus.d_r,
            o_d_o        = bus.d_w,      o_d_oe     = Open(),
            o_siz_o      = bus.siz,      o_siz_oe   = Open(),
            o_ecs_n_o    = Open(),
            o_ocs_n_o    = Open(),
            o_rw_o       = bus.rw,       o_rw_oe    = Open(),
            o_rmc_n_o    = bus.rmc_n,    o_rmc_oe   = Open(),
            o_as_n_o     = bus.as_n,     o_as_oe    = Open(),
            o_ds_n_o     = bus.ds_n,     o_ds_oe    = Open(),
            o_dben_n_o   = Open(),       o_dben_oe  = Open(),
            i_dsack_n_i  = bus.dsack_n,
            i_sterm_n_i  = bus.sterm_n,
            i_ciin_n_i   = bus.ciin_n,
            o_ciout_n_o  = bus.ciout_n,  o_ciout_oe = Open(),
            o_cbreq_n_o  = bus.cbreq_n,  o_cbreq_oe = Open(),
            i_cback_n_i  = bus.cback_n,
            i_ipl_n_i    = ~self.dev.ipl,
            o_ipend_n_o  = Open(),
            i_avec_n_i   = bus.avec_n,
            i_br_n_i     = 1,
            o_bg_n_o     = Open(),
            i_bgack_n_i  = 1,
            i_berr_n_i   = bus.berr_n,
            # The processor's own RESET pulse comes back on the wired pin, as on a
            # board; the core ignores it (doc/pinout.md in RD68031).
            i_reset_n_i  = ~reset_n_oe,
            o_reset_n_o  = Open(),
            o_reset_n_oe = reset_n_oe,
            i_halt_n_i   = bus.halt_n,
            i_cdis_n_i   = 1,
            i_mmudis_n_i = 1,
            o_refill_n_o = Open(),
            o_status_n_o = Open(),
        )

class Bench040(BenchBase):
    """RD68041 behind M68040ToFIFOToSoC, the processor on clock domain "cpu"."""
    def __init__(self, ram_size=0x40000, fpu=1, timeout=256):
        self.cd_sys = ClockDomain()
        self.cd_cpu = ClockDomain()

        # # #

        bus = Record(m68040_bus_layout)
        io_regions = {0x8000_0000: 0x8000_0000}  # As LiteX's.
        wb = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.bridge  = M68040ToFIFOToSoC(bus, wb, cd="cpu", io_regions=io_regions)
        master = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.timeout = WishboneErrorTimeout(wb, master, timeout)
        self.add_soc(master, ram_size)

        # The processor, the only master: the bus granted to it for good, no
        # snooping (SC = 00, UM Table 4-1), and its bidirectional pins' inputs
        # fed from its own outputs while it drives them, as on a board.
        ts_n_o, ts_oe, bb_n_o, bb_oe = Signal(), Signal(), Signal(), Signal()
        self.comb += bus.ts_n.eq(~ts_oe | ts_n_o)
        self.specials += Instance("rd68041_top",
            p_FPU        = fpu,
            i_clk        = ClockSignal("cpu"),
            i_pclk       = 0,
            i_rst_n      = ~ResetSignal("cpu"),
            i_rsti_n_i   = ~ResetSignal("cpu"),
            o_rsto_n_o   = Open(),
            i_a_i        = bus.a,        o_a_o      = bus.a,      o_a_oe     = Open(),
            i_d_i        = bus.d_r,      o_d_o      = bus.d_w,    o_d_oe     = Open(),
            i_tt_i       = bus.tt,       o_tt_o     = bus.tt,
            o_tm_o       = bus.tm,
            o_tln_o      = Open(),
            o_upa_o      = Open(),
            i_rw_i       = bus.rw,       o_rw_o     = bus.rw,
            i_siz_i      = bus.siz,      o_siz_o    = bus.siz,
            o_lock_n_o   = bus.lock_n,
            o_locke_n_o  = Open(),
            o_ciout_n_o  = bus.ciout_n,
            o_attr_oe    = Open(),
            i_ts_n_i     = bus.ts_n,     o_ts_n_o   = ts_n_o,     o_ts_oe    = ts_oe,
            o_tip_n_o    = bus.tip_n,    o_tip_oe   = Open(),
            i_ta_n_i     = bus.ta_n,     o_ta_n_o   = Open(),     o_ta_oe    = Open(),
            i_tea_n_i    = bus.tea_n,
            i_tci_n_i    = bus.tci_n,
            i_tbi_n_i    = bus.tbi_n,
            i_dle_i      = 0,
            i_sc_i       = 0b00,
            o_mi_n_o     = Open(),
            o_br_n_o     = Open(),
            i_bg_n_i     = 0,
            i_bb_n_i     = ~bb_oe | bb_n_o, o_bb_n_o = bb_n_o,    o_bb_oe    = bb_oe,
            i_cdis_n_i   = 1,
            i_mdis_n_i   = 1,
            i_ipl_n_i    = ~self.dev.ipl,
            o_ipend_n_o  = Open(),
            i_avec_n_i   = bus.avec_n,
            o_pst_o      = Open(),
            i_tck_i      = 0,
            i_tms_i      = 1,
            i_tdi_i      = 1,
            i_trst_n_i   = 0,
            o_tdo_o      = Open(),
            o_tdo_oe     = Open(),
        )

class Bench040SMP(BenchBase):
    """Two RD68041s on one MC68040 bus (M68040SMPBus: setup A, the corrected
    fairness arbiter, the MI gate, the board's devices), behind
    M68040ToFIFOToSoC, on clock domain "cpu". CPU 1 stays in reset until the
    devices' RELEASE; each processor's IPL is its interprocessor interrupt.
    `mi_gate` False and `gapless` True break the board, for negative runs."""
    def __init__(self, ram_size=0x40000, fpu=1, timeout=256, mi_gate=True, gapless=False):
        self.cd_sys = ClockDomain()
        self.cd_cpu = ClockDomain()

        # # #

        bus  = Record(m68040_bus_layout)
        cpus = [Record(m68040_pins_layout, name="cpu{}".format(i)) for i in range(2)]
        self.board = board = ClockDomainsRenamer("cpu")(
            M68040SMPBus(cpus, bus, gapless=gapless, mi_gate=mi_gate))
        io_regions = {0x8000_0000: 0x8000_0000}  # As LiteX's.
        wb = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.bridge  = M68040ToFIFOToSoC(bus, wb, cd="cpu", io_regions=io_regions,
                                         mi_n=board.mi_n, ignore=board.ignore)
        master = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        self.timeout = WishboneErrorTimeout(wb, master, timeout)
        self.add_soc(master, ram_size)

        rst = ResetSignal("cpu")
        self.comb += [
            cpus[0].rsti_n.eq(~rst),
            cpus[1].rsti_n.eq(~rst & board.devices.release),
        ]
        for i, pins in enumerate(cpus):
            self.specials += Instance("rd68041_top", **rd68041_smp_params(pins, board, fpu,
                clk=ClockSignal("cpu"), rst_n=~rst,
                ipl_n=~Mux(board.devices.ipi[i], IPI_LEVEL, 0)))

        # The counters, for tb.sv.
        self.stats = {name: Signal(32, name="stat_" + name) for name in STATS}
        self.comb += [self.stats[n].eq(board.stats[n]) for n in STATS]

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate the L1 bench's Verilog.")
    parser.add_argument("--output",   default="build/bench.v", help="Verilog file to write.")
    parser.add_argument("--ram-size", default=0x40000, type=lambda x: int(x, 0), help="RAM size in bytes.")
    parser.add_argument("--icache",   default=64, type=int, help="ICACHE_ENTRIES (0: no instruction cache).")
    parser.add_argument("--dcache",   default=64, type=int, help="DCACHE_ENTRIES (0: no data cache).")
    parser.add_argument("--bridge",   default="wishbone", choices=["wishbone", "fifo"],
                        help="M68030ToWishbone, or M68030ToFIFOToSoC with the CPU on a clock of its own.")
    parser.add_argument("--cpu",      default="rd68031", choices=["rd68031", "rd68041"],
                        help="The processor; rd68041 is always behind its FIFO bridge.")
    parser.add_argument("--fpu",      default=1, type=int, help="RD68041's FPU parameter (0: MC68LC040).")
    parser.add_argument("--smp",      action="store_true", help="Two RD68041s on one bus (L19).")
    parser.add_argument("--no-mi-gate", action="store_true", help="--smp, negative run: no MI gate.")
    parser.add_argument("--gapless",  action="store_true", help="--smp, negative run: the printed arbiter's grants.")
    args = parser.parse_args()

    if args.cpu == "rd68041" and args.smp:
        args.bridge = "fifo"
        bench = Bench040SMP(ram_size=args.ram_size, fpu=args.fpu, mi_gate=not args.no_mi_gate,
                            gapless=args.gapless)
    elif args.cpu == "rd68041":
        args.bridge = "fifo"
        bench = Bench040(ram_size=args.ram_size, fpu=args.fpu)
    else:
        bench = Bench(ram_size=args.ram_size, icache=args.icache, dcache=args.dcache, bridge=args.bridge)
    ios = {
        bench.cd_sys.clk, bench.cd_sys.rst,
        bench.tty_stb, bench.tty_data,
        bench.exit_stb, bench.exit_code,
        bench.result_stb, bench.result,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    if args.bridge == "fifo":
        ios |= {bench.cd_cpu.clk, bench.cd_cpu.rst}
    if args.smp:
        ios |= set(bench.stats.values())
    convert(bench, ios=ios, name="bench", special_overrides=sim_special_overrides).write(args.output)

if __name__ == "__main__":
    main()
