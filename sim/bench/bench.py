#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""L1 bench: RD68031 + M68030ToWishbone + Wishbone RAM and test devices, no LiteX SoC.

Generates bench.v, the top that tb.sv instantiates. The map:

    $0000_0000  RAM (--ram-size), loaded at run time from the file named by the
                +ram= plusarg (one 32-bit big-endian word per line, $readmemh)
    $E000_0000  a slave that answers every access with a Wishbone error
    $F000_0000  the test device, below
    elsewhere   nothing: the bridge times out and asserts BERR

$E000_0000-$FFFF_FFFF is the I/O region: the bridge asserts CIIN there.

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

from litex_68k.m68030_bus import m68030_bus_layout, M68030ToWishbone

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

class Bench(LiteXModule):
    def __init__(self, ram_size=0x40000, icache=64, dcache=64, timeout=256):
        self.cd_sys = ClockDomain()

        # # #

        bus = Record(m68030_bus_layout)
        self.bridge = M68030ToWishbone(bus, io_regions={0xE000_0000: 0x2000_0000}, timeout=timeout)
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

        self.decoder = wishbone.Decoder(self.bridge.wishbone, [
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

        # The processor.
        reset_n_oe = Signal()
        self.specials += Instance("rd68031_top",
            p_ICACHE_ENTRIES          = icache,
            p_DCACHE_ENTRIES          = dcache,
            p_COPROCESSOR             = 0,
            p_ADDR_HIZ_BETWEEN_CYCLES = 0,
            i_clk        = ClockSignal("sys"),
            i_rst_n      = ~ResetSignal("sys"),
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
            i_ipl_n_i    = ~dev.ipl,
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

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate the L1 bench's Verilog.")
    parser.add_argument("--output",   default="build/bench.v", help="Verilog file to write.")
    parser.add_argument("--ram-size", default=0x40000, type=lambda x: int(x, 0), help="RAM size in bytes.")
    parser.add_argument("--icache",   default=64, type=int, help="ICACHE_ENTRIES (0: no instruction cache).")
    parser.add_argument("--dcache",   default=64, type=int, help="DCACHE_ENTRIES (0: no data cache).")
    args = parser.parse_args()

    bench = Bench(ram_size=args.ram_size, icache=args.icache, dcache=args.dcache)
    ios = {
        bench.cd_sys.clk, bench.cd_sys.rst,
        bench.tty_stb, bench.tty_data,
        bench.exit_stb, bench.exit_code,
        bench.result_stb, bench.result,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    convert(bench, ios=ios, name="bench").write(args.output)

if __name__ == "__main__":
    main()
